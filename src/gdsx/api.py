"""The JSON facade

Everything the UI needs, coarse grained, JSON in and JSON out. The JS side
never touches a `Netlist`, a `Graph` or a `Design`; it holds an opaque handle
string and passes it back. That is what keeps a refactor of the other 6,500
lines from being a UI refactor as well.

Nothing here raises across the boundary. Every function returns an envelope::

    {"schema_version": 1, "ok": true,  "data": {...}}
    {"schema_version": 1, "ok": false, "error": {"code": ..., "message": ...}}

Imports are deliberately deferred into the functions that need them: `import
gdsx.api` must stay as light as `import gdsx`, or the worker pulls the whole
extraction stack in before it has been asked to do anything.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from dataclasses import dataclass, field
from typing import Any, Callable

from .core import serial
from .core.context import Design, NoLayoutAvailable

#: Bumped whenever a payload shape changes. The browser keys its caches on it.
SCHEMA_VERSION = 1

Progress = Callable[[str, int, int], None]


class ApiError(Exception):
    """An error with a code the UI can switch on"""

    def __init__(self, code: str, message: str, detail: Any = None) -> None:
        self.code = code
        self.message = message
        self.detail = detail
        super().__init__(message)


#! the handle registry: the only state in this module

_REGISTRY: dict[str, Design] = {}
_COUNTER = itertools.count(1)


def _register(design: Design) -> str:
    handle = f"design-{next(_COUNTER)}"
    _REGISTRY[handle] = design
    return handle


def _get(handle: str) -> Design:
    try:
        return _REGISTRY[handle]
    except KeyError:
        raise ApiError("bad_handle", f"no such handle: {handle!r}") from None


def _parse(text: str | None, what: str) -> Any:
    if text is None:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ApiError("bad_json", f"{what} is not valid JSON: {exc}") from None


def _report(progress: Progress | None, stage: str, done: int, total: int) -> None:
    """The single call site for progress"""
    if progress is not None:
        progress(stage, done, total)


#! the envelope


def _envelope(data: Any) -> str:
    return json.dumps({"schema_version": SCHEMA_VERSION, "ok": True, "data": data})


def _failure(code: str, message: str, detail: Any = None) -> str:
    return json.dumps(
        {
            "schema_version": SCHEMA_VERSION,
            "ok": False,
            "error": {"code": code, "message": message, "detail": detail},
        }
    )


def _error_of(exc: Exception) -> str:
    """Map an exception to an envelope. Codes are fixed and only ever added to"""
    from .functions import UnknownCell

    if isinstance(exc, ApiError):
        return _failure(exc.code, exc.message, exc.detail)
    if isinstance(exc, NoLayoutAvailable):
        return _failure("no_layout", str(exc))
    if isinstance(exc, UnknownCell):
        return _failure("unknown_cell", str(exc))
    return _failure("internal", str(exc), type(exc).__name__)


def _endpoint(func):
    """Wrap a function so it returns an envelope instead of raising"""

    def wrapper(*args, **kwargs) -> str:
        try:
            return _envelope(func(*args, **kwargs))
        except Exception as exc:  # noqa: BLE001 , nothing may cross the boundary
            return _error_of(exc)

    wrapper.__name__ = func.__name__
    wrapper.__doc__ = func.__doc__
    wrapper.__wrapped__ = func
    return wrapper


#! payload shapes


@dataclass
class RefView:
    instance: str
    pin: str
    cell: str
    direction: str  # input | output | power


@dataclass
class NetView:
    name: str
    driver: RefView | None  # None = primary input, constant, or undriven
    readers: list[RefView] = field(default_factory=list)
    is_port: str | None = None  # "input" | "output" | None
    leaf: str | None = None  # a LeafKind value, or None for combinational


@dataclass
class InstanceView:
    name: str  # "dfrtp_2_50"
    cell: str  # "sky130_fd_sc_hd__dfrtp_2"  -- the FULL name
    base_cell: str  # "dfrtp"                -- what Liberty is keyed on
    generic: str  # "DFFR"
    #: pin -> net. MAY OMIT unconnected pins; an absent pin is not a zero
    connections: dict[str, str] = field(default_factory=dict)
    is_sequential: bool = False
    functions: dict[str, str] = field(default_factory=dict)  # out pin -> Verilog
    #: db units, or None when there is no layout behind this design
    bbox: list[int] | None = None


@dataclass
class GateView:
    instance: str
    cell: str
    fn: str


@dataclass
class FlopView:
    """One state element of a gate tape, as indices into the value array

    `rst` and `set` are ALWAYS active high and `clk` always active high,
    whatever the cell's own polarity (the compiler emits the inversion as a
    `NOT` op instead). `-1` means the cell has no such pin.
    """

    d: int
    q: int
    clk: int
    rst: int
    set: int
    kind: int  # 0=DFF 1=DFFR 2=DFFS 3=DFFSR 4=DLATCH; informational


@dataclass
class TapeView:
    """A compiled design: the op stream both executors run

    `ops` is a flat stride-6 stream, `[opcode, out, in0, in1, in2, in3]` per op,
    topologically ordered so one forward pass settles the design. Values are 0
    or 1 only.
    """

    tape_version: int
    n_nets: int
    n_flops: int
    n_ops: int
    inputs: list[int]  # primary input net ids, in net-name order
    ops: list[int]
    flops: list[FlopView]
    consts: list[list[int]]  # [net id, 0|1], applied before the ops run
    names: dict[str, int] = field(default_factory=dict)
    flop_names: list[str] = field(default_factory=list)


@dataclass
class ConeNode:
    net: str
    gate: GateView | None = None
    pins: dict[str, str] = field(default_factory=dict)
    leaf: str | None = None
    children: list["ConeNode"] = field(default_factory=list)
    #: True = there is more below and the walk stopped. Rendering a truncated
    #: node as a leaf is how a player concludes a net is a primary input when
    #: it is not, so this is never inferred from an empty child list
    truncated: bool = False


@dataclass
class LeafValue:
    """One forced literal: `net` (as `Graph.label` names it) must be `value`."""

    net: str
    value: int


@dataclass
class ChoiceOption:
    """One satisfying cube of an unresolved disjunction: all of `literals` at once."""

    literals: list[LeafValue]


@dataclass
class ChoiceView:
    """A gate whose inputs were not all forced: any one option suffices."""

    net: str
    value: int
    options: list[ChoiceOption]


@dataclass
class RequirementsView:
    """What `net == value` demands upstream -- the flattened AND/OR cone.

    `leaves` are forced in every way of reaching `value`: this is the
    polarity-annotated list a pure AND (or NAND-of-NANDs) tree reduces to.
    `choices` are the OR branches that stopped that reduction; each is a
    dead end for "forced", not for the walk, so they are kept, not dropped.
    """

    net: str
    value: int
    consistent: bool
    leaves: list[LeafValue]
    choices: list[ChoiceView]
    conflicts: list[str]


@dataclass
class SliceView:
    """A self-contained mini-tape: the ops one question needs, and nothing else

    Same opcode encoding as `TapeView.ops`, so the same executor runs it -- but
    renumbered to itself, so every id here indexes a value array of `n_values`,
    not the design's. `free[i]` is the name of the variable at `free_ids[i]`, and
    `i` is its bit position in an assignment.
    """

    ops: list[int]
    free: list[str]
    free_ids: list[int]
    targets: list[int]
    target_names: list[str]
    consts: list[list[int]]
    n_values: int


@dataclass
class VerdictView:
    """A verdict the library settled on its own, always from the netlist

    Never `LIKELY`: nothing on this side of the boundary samples anything, and a
    sampled result is not a proof. Never carries a counterexample vector either
    -- a structural disproof is two names differing, which is what `observed` and
    `expected` hold.
    """

    kind: str  # "PROVEN" | "DISPROVEN" | "UNKNOWN" -- never "LIKELY"
    method: str  # "structural" when proven, "" otherwise
    cases: int
    reason: str
    observed: list[str]
    expected: list[str]


@dataclass
class PredicateView:
    """A parsed constraint predicate: comparisons of a measurement to a number

    A tree of `and`/`or`/`not` over `term` leaves. On a term, `measure`, `port`,
    `window`, `op` and `value` are set and `of` is empty; on everything else only
    `of` is.
    """

    node: str  # "and" | "or" | "not" | "term"
    of: list["PredicateView"]
    measure: str
    port: str
    window: list[int] | None
    op: str
    value: int


@dataclass
class JobView:
    """Verification work the library scheduled rather than performed

    `design` and `claim`, when both are present, share a free-variable list --
    same names, same order -- so one assignment means the same thing to both and
    a counterexample decodes to the same nets on either side.

    What is deliberately NOT here is how many assignments to visit and whether
    the answer may be called proven. That is one policy and it lives with the
    evaluator, so there is one copy of it rather than two that can drift.
    """

    engine: str  # "combinational" | "transition" | "sequential"
    check: str  # "equal" | "implies" | "essential" | "role" | "requirement" | "timing" | "constraint"
    design: SliceView | None
    claim: SliceView | None
    predicate: PredicateView | None


@dataclass
class StickyView:
    """One flop whose D pin latches on its own Q (`sequential.sticky`).

    Stickiness alone never says checkpoint or trap -- that is not computed
    here, and no field on this view claims it. The player states it.
    """

    flop: str
    polarity: int  # 1 = latches high, 0 = latches low
    condition: str


@dataclass
class WeightView:
    """One flop's inferred binary weight (`analysis.weights.infer`).

    `value` is meaningless when `confidence` is `"unknown"`. The UI must
    never render it with the same authority as an `"observed"` weight, and
    `"by_elimination"` sits between the two -- correct, but never sighted
    directly.
    """

    value: int
    confidence: str  # "observed" | "by_elimination" | "unknown"


@dataclass
class OrbitView:
    """The state sequence `analysis.decode.orbit` walked, and its classification."""

    states: list[list[int]]
    kind: str  # "fixed-point" | "saturating" | "wrapping" | "shift" | "counter" | "unknown"


@dataclass
class ConstraintView:
    """One row of a `System`: `lb <= sum(x[e] for e in elements) <= ub`."""

    name: str
    elements: list[int]
    lb: int
    ub: int


@dataclass
class SystemView:
    """A constraint set over a shared pool of candidate cycles (`analysis.constraints.System`)."""

    variables: list[int]
    watched: list[str]
    constraints: list[ConstraintView]
    unconstrained: list[str] = field(default_factory=list)


@dataclass
class ClaimPlanView:
    """What it would take to settle one claim: an answer, or the work to get one

    Exactly one of `verdict` and `job` is set. `notes` are the assumptions the
    plan was built under -- what got held at a fixed value, which cycle the claim
    turned out to be about. They are shown, not swallowed: a claim checked under
    assumptions is only honest if it says which.
    """

    kind: str
    call: str
    verdict: VerdictView | None
    job: JobView | None
    notes: list[str]


#! handles


@_endpoint
def open_design(
    gds_bytes: bytes, tech_json: str | None = None, top: str | None = None
) -> dict:
    """Open a GDS and report what is in it. Does not extract"""
    from . import config

    raw = _parse(tech_json, "tech_json")
    tech = config.from_raw(raw) if raw is not None else None
    design = Design.open(gds_bytes, tech=tech, top=top)
    layout = design.layout  # opened now, so a bad file fails here and not later
    cells: dict[str, int] = {}
    for name, _ in layout.instances():
        cells[name] = cells.get(name, 0) + 1
    return {
        "handle": _register(design),
        "top": layout.top,
        "dbu": layout.dbu,
        "cell_count": len(cells),
        "instance_count": sum(cells.values()),
        "has_layout": True,
    }


@_endpoint
def load_netlist(netlist_json: str) -> dict:
    """Open a design from a pre-baked netlist, skipping extraction entirely

    Takes exactly what `extract` puts in its `netlist` field, so one call's
    output is the next session's input.
    """
    from .core.netlist import Netlist

    data = _parse(netlist_json, "netlist_json")
    try:
        nl = Netlist.from_dict(data)
    except (KeyError, TypeError) as exc:
        raise ApiError("bad_json", f"not a netlist: {exc}") from None
    design = Design.from_netlist(nl)
    return {
        "handle": _register(design),
        "top": nl.top,
        "instance_count": len(nl.instances),
        "net_count": len(nl.nets),
        "port_count": len(nl.ports),
        "has_layout": False,
    }


@_endpoint
def close(handle: str) -> dict:
    """Drop a design and everything memoised on it"""
    design = _get(handle)
    design.invalidate()
    del _REGISTRY[handle]
    return {"closed": handle}


@_endpoint
def handles() -> dict:
    """Every open handle. A worker that leaks one leaks a whole netlist"""
    return {"handles": sorted(_REGISTRY)}


@_endpoint
def reset() -> dict:
    """Drop every design. For a worker being handed a new puzzle"""
    count = len(_REGISTRY)
    for design in _REGISTRY.values():
        design.invalidate()
    _REGISTRY.clear()
    return {"closed": count}


@_endpoint
def capabilities() -> dict:
    """What this environment can do. The browser greys out what it cannot"""
    from . import capabilities as _capabilities

    return _capabilities()


#! extraction


@_endpoint
def extract(handle: str, *, progress: Progress | None = None) -> dict:
    """Trace the routing and return the netlist

    `progress`, if given, is invoked as `(stage, done, total)` throughout: a
    single "open" report, then the real extraction stages ("trace" per
    routing layer, "vias" per via layer, "bond_pins", "resolve" per chunk of
    instances, "name") straight from `netlist.build_with_net_ids`, then
    "serialise" and "done". This is the watchable tracer. Re-extraction is
    a deliberate, explicit action.
    """
    design = _get(handle)
    _report(progress, "open", 0, 1)
    try:
        design.layout  # a netlist-backed design has none, and needs none
    except NoLayoutAvailable:
        pass
    _report(progress, "open", 1, 1)
    nl = design.extract(progress=progress)
    _report(progress, "serialise", 0, 1)
    payload = {
        "netlist": nl.to_dict(),
        "summary": {
            "top": nl.top,
            "instance_count": len(nl.instances),
            "net_count": len(nl.nets),
            "port_count": len(nl.ports),
            "floating": len(nl.floating),
            "conflicts": len(nl.conflicts),
        },
    }
    _report(progress, "serialise", 1, 1)
    _report(progress, "done", 1, 1)
    return payload


@_endpoint
def cache_key(gds_bytes: bytes) -> dict:
    """The IndexedDB key for a cached extraction of these GDS bytes

    `sha256(gds_bytes) + schema_version`: the hash pins it to this exact
    file, and `schema_version` pins it to this exact payload shape, so
    bumping `SCHEMA_VERSION` invalidates every cached bundle without the JS
    side having to know why. The JS side of the cache (reading/writing
    IndexedDB) does not exist yet. This only defines the key.
    """
    digest = hashlib.sha256(gds_bytes).hexdigest()
    return {"key": f"{digest}-{SCHEMA_VERSION}"}


#! the netlist, as the browser and cone walker see it


def _ref(ref) -> RefView:
    return RefView(ref.instance, ref.pin, ref.cell, ref.direction)


def _instance_view(graph, inst) -> InstanceView:
    from .functions import base_name, generic_name
    from .liberty import to_verilog

    cell = graph.cell_of.get(inst.name)
    return InstanceView(
        name=inst.name,
        cell=inst.cell,
        base_cell=base_name(inst.cell),
        generic=generic_name(inst.cell),
        # verbatim: a pin the layout did not connect is absent, not zero
        connections=dict(inst.connections),
        is_sequential=inst.name in graph.seq,
        functions=(
            {}
            if cell is None
            else {pin: to_verilog(expr) for pin, expr in cell.functions.items()}
        ),
        bbox=None,  # db-unit geometry arrives with the render bundle
    )


@_endpoint
def instances(handle: str, names_json: str | None = None) -> list:
    """Every instance as the netlist browser sees it, or just the named ones"""
    design = _get(handle)
    graph = design.graph
    wanted = _parse(names_json, "names_json")
    chosen = design.netlist.instances
    if wanted is not None:
        missing = [n for n in wanted if n not in graph.by_name]
        if missing:
            raise ApiError("unknown_instance", f"no such instance: {missing[0]!r}")
        chosen = [graph.by_name[n] for n in wanted]
    return serial.to_dict([_instance_view(graph, inst) for inst in chosen])


@_endpoint
def nets(handle: str, names_json: str | None = None) -> list:
    """Every net with its driver, readers and leaf classification"""
    design = _get(handle)
    nl, graph = design.netlist, design.graph
    wanted = _parse(names_json, "names_json")
    if wanted is None:
        chosen = sorted(nl.nets)
    else:
        missing = [n for n in wanted if n not in nl.nets]
        if missing:
            raise ApiError("unknown_net", f"no such net: {missing[0]!r}")
        chosen = list(wanted)

    found = []
    for net in chosen:
        driver = graph.driver.get(net)
        leaf = graph.leaf(net)
        found.append(
            NetView(
                name=net,
                driver=_ref(driver) if driver is not None else None,
                readers=[_ref(r) for r in graph.readers.get(net, ())],
                is_port=nl.ports.get(net),
                leaf=leaf.kind.value if leaf is not None else None,
            )
        )
    return serial.to_dict(found)


@_endpoint
def sub_netlist(handle: str, instances_json: str, name: str | None = None) -> dict:
    """Carve `instances` out as a standalone netlist, in the same shape
    `extract` returns
    """
    design = _get(handle)
    graph = design.graph
    wanted = _parse(instances_json, "instances_json")
    if not isinstance(wanted, list) or not wanted:
        raise ApiError(
            "bad_json", "instances_json must be a non-empty list of instance names"
        )
    missing = [n for n in wanted if n not in graph.by_name]
    if missing:
        raise ApiError("unknown_instance", f"no such instance: {missing[0]!r}")

    sub = graph.subgraph(set(wanted), name=name)
    return {
        "netlist": sub.to_dict(),
        "summary": {
            "top": sub.top,
            "instance_count": len(sub.instances),
            "net_count": len(sub.nets),
            "port_count": len(sub.ports),
        },
    }


@_endpoint
def layout(handle: str, instances_json: str | None = None) -> dict:
    """A left-to-right layered layout of `instances` (or the whole design),
    for a graph view the browser can draw without shipping graphviz

    `instances_json` is the same shape `sub_netlist` takes; with none given,
    lays out the whole design, which only succeeds under `analysis.layout.
    MAX_NODES` -- fails with `too_many_nodes` above that on purpose, since a
    728-gate hairball helps nobody. Sub-net first (`sub_netlist`) for a design
    that big.
    """
    from .analysis.layout import TooManyNodes, layered

    design = _get(handle)
    graph = design.graph
    wanted = _parse(instances_json, "instances_json")
    if wanted is None:
        nl = design.netlist
    else:
        if not isinstance(wanted, list) or not wanted:
            raise ApiError(
                "bad_json", "instances_json must be a non-empty list of instance names"
            )
        missing = [n for n in wanted if n not in graph.by_name]
        if missing:
            raise ApiError("unknown_instance", f"no such instance: {missing[0]!r}")
        nl = graph.subgraph(set(wanted))

    try:
        got = layered(nl)
    except TooManyNodes as exc:
        raise ApiError("too_many_nodes", str(exc), {"count": exc.count}) from None
    return serial.to_dict(got)


@_endpoint
def cone(
    handle: str,
    net: str,
    depth: int = 3,
    direction: str = "in",
    through_flops: bool = False,
) -> dict:
    """The fan-in (or fan-out) of `net` as a tree, `depth` levels deep"""
    design = _get(handle)
    graph = design.graph
    if net not in design.netlist.nets:
        raise ApiError("unknown_net", f"no such net: {net!r}")
    if direction not in ("in", "out"):
        raise ApiError(
            "bad_json", f"direction must be 'in' or 'out', not {direction!r}"
        )

    if direction == "in":
        root = _fanin_cone(graph, graph.fanin_tree(net, depth=depth))
    else:
        root = _fanout_cone(
            graph,
            graph.fanout_tree(net, depth=depth, through_flops=through_flops),
            through_flops,
        )
    return serial.to_dict(root)


def _gate(graph, ref, net: str) -> tuple[GateView, dict[str, str]]:
    """The gate that produced `net`, and the pin -> net map behind it

    `net` is always the gate's own output, so `function_of` answers for it
    whether the node was reached walking backwards (`ref` is the driving pin)
    or forwards (`ref` is the input pin the walk came in through).
    """
    conns = dict(graph.by_name[ref.instance].connections)
    return GateView(ref.instance, ref.cell, graph.function_of(net) or ""), conns


def _fanin_cone(graph, node) -> ConeNode:
    """A `FaninNode` tree as the UI's ConeNode. Iterative: cones reach depth ~40"""
    root = ConeNode(node.net)
    stack = [(node, root)]
    while stack:
        source, out = stack.pop()
        if source.leaf is not None:
            out.leaf = source.leaf.kind.value
            continue
        gate, conns = _gate(graph, source.driver, source.net)
        out.gate = gate
        out.pins = conns
        # No children and not a leaf means the walk stopped early: either the
        # depth budget ran out or the net was expanded on an earlier path
        out.truncated = not source.children
        out.children = [ConeNode(kid.net) for kid in source.children]
        stack.extend(zip(source.children, out.children))
    return root


def _fanout_cone(graph, node, through_flops: bool) -> ConeNode:
    """A `FanoutNode` tree as the UI's ConeNode

    The gate on a fan-out node is the instance that *read* the parent net to
    produce this one, so it is taken from the consumer rather than the driver.
    """
    root = ConeNode(node.net)
    stack = [(node, root)]
    while stack:
        source, out = stack.pop()
        if source.consumer is not None:
            out.gate, out.pins = _gate(graph, source.consumer, source.net)
        leaf = graph.leaf(source.net)
        out.leaf = leaf.kind.value if leaf is not None else None
        # `through_flops` has to match the walk that built the tree, or a node
        # whose only readers are flops reads as a dead end when it is not
        out.truncated = not source.children and bool(
            graph.readers_out(source.net, through_flops)
        )
        out.children = [ConeNode(kid.net) for kid in source.children]
        stack.extend(zip(source.children, out.children))
    return root


@_endpoint
def requirements(handle: str, net: str, value: int = 1) -> dict:
    """Flatten `net`'s fan-in into what must hold for it to equal `value`

    This is `analysis.justify.requirements`: every gate's Liberty truth table,
    enumerated and pushed backward. A pure AND (or NAND-of-NANDs) tree comes
    back entirely as `leaves` -- the polarity-annotated list a player would
    otherwise have to read off a tree dump by hand, one sign error away from
    wrong. An OR anywhere in the tree leaves a `choices` entry instead of
    forcing anything: collapsing that into a leaf would turn "might" into
    "must", so it never does.
    """
    design = _get(handle)
    graph = design.graph
    if net not in design.netlist.nets:
        raise ApiError("unknown_net", f"no such net: {net!r}")
    if value not in (0, 1):
        raise ApiError("bad_json", f"value must be 0 or 1, not {value!r}")

    from .analysis import justify

    try:
        found = justify.requirements(graph, net, value)
    except justify.Unenumerable as exc:
        raise ApiError("unenumerable", str(exc)) from None

    leaves = [
        LeafValue(label, found.forced[net_])
        for net_, label in sorted(found.labels.items(), key=lambda kv: kv[1])
    ]
    choices = [
        ChoiceView(
            c.net,
            c.value,
            [
                ChoiceOption(
                    [LeafValue(w, v) for w, v in sorted(cube)],
                )
                for cube in c.options
            ],
        )
        for c in found.choices
    ]
    return serial.to_dict(
        RequirementsView(
            net=net,
            value=value,
            consistent=found.consistent,
            leaves=leaves,
            choices=choices,
            conflicts=list(found.conflicts),
        )
    )


def _slice_view(sliced) -> SliceView:
    return SliceView(
        ops=list(sliced.ops),
        free=list(sliced.free),
        free_ids=list(sliced.free_ids),
        targets=list(sliced.targets),
        target_names=list(sliced.target_names),
        consts=[list(pair) for pair in sliced.consts],
        n_values=sliced.n_values,
    )


def _predicate_view(node: dict) -> PredicateView:
    return PredicateView(
        node=node["node"],
        of=[_predicate_view(child) for child in node.get("of", ())],
        measure=node.get("measure", ""),
        port=node.get("port", ""),
        window=node.get("window"),
        op=node.get("op", ""),
        value=node.get("value", 0),
    )


@_endpoint
def claim_plan(handle: str, claim_json: str) -> dict:
    """What it would take to settle one notebook claim

    Structural claims come back settled: their evidence is a fact about the
    netlist and looking it up *is* the verification. Everything else comes back
    as a job -- one or two slices of the gate tape and an instruction for what to
    compare -- because settling it means evaluating a cone over up to 2**24
    assignments, and that grind belongs where it can be done 32 assignments at a
    time rather than one at a time in a Python interpreter compiled to wasm.

    What this never returns is a guess. A claim that cannot be settled or
    scheduled comes back `UNKNOWN` with a reason, and no verdict from this side
    is ever `LIKELY`: sampling happens in the evaluator, and a sampled result is
    not a proof.
    """
    from .analysis import claims

    design = _get(handle)
    claim = _parse(claim_json, "claim_json")
    if not isinstance(claim, dict):
        raise ApiError("bad_json", "claim_json must be an object")

    try:
        plan = claims.plan(design.graph, design.tape(), claim)
    except claims.ClaimError as exc:
        raise ApiError(exc.code, exc.message, exc.detail) from None

    return serial.to_dict(
        ClaimPlanView(
            kind=plan.kind,
            call=plan.call,
            verdict=(
                None
                if plan.verdict is None
                else VerdictView(
                    kind=plan.verdict.kind,
                    method=plan.verdict.method,
                    cases=plan.verdict.cases,
                    reason=plan.verdict.reason,
                    observed=list(plan.verdict.observed),
                    expected=list(plan.verdict.expected),
                )
            ),
            job=(
                None
                if plan.job is None
                else JobView(
                    engine=plan.job.engine,
                    check=plan.job.check,
                    design=(
                        None if plan.job.design is None else _slice_view(plan.job.design)
                    ),
                    claim=(
                        None if plan.job.claim is None else _slice_view(plan.job.claim)
                    ),
                    predicate=(
                        None
                        if plan.job.predicate is None
                        else _predicate_view(plan.job.predicate)
                    ),
                )
            ),
            notes=list(plan.notes),
        )
    )


@_endpoint
def claim_vocabulary(handle: str) -> dict:
    """What the claim forms may offer: kinds, roles, events, measurements

    The UI builds its dropdowns from this rather than from a copy of the same
    lists, so adding a role or an event is one change here and none there.
    """
    from .analysis import claims

    design = _get(handle)
    nl = design.netlist
    return {
        "kinds": list(claims.KINDS),
        "roles": list(claims.ROLES),
        "events": list(claims.EVENTS),
        "measures": dict(claims.MEASURES),
        "flops": sorted(design.graph.seq),
        "inputs": sorted(net for net, kind in nl.ports.items() if kind == "input"),
        "outputs": sorted(net for net, kind in nl.ports.items() if kind == "output"),
    }


@_endpoint
def flop_d_net(handle: str, net: str) -> dict:
    """Step through the flop driving `net`: the net on its D pin

    `net` must be a flop's Q net -- the same net a `cone` walk's `flop_q`
    leaf names. This is one clock cycle earlier, which is why the cone walk
    never crosses a flop on its own; the UI takes this as an explicit step so
    a player is never unsure which cycle they are looking at.
    """
    design = _get(handle)
    graph = design.graph
    if net not in design.netlist.nets:
        raise ApiError("unknown_net", f"no such net: {net!r}")
    ref = graph.driver_of(net)
    if ref is None or ref.instance not in graph.seq:
        raise ApiError("not_a_flop", f"{net!r} is not a flop's Q net")
    return {"instance": ref.instance, "net": graph.d_pin(ref.instance)}


@_endpoint
def cone_slice(handle: str, net: str) -> dict:
    """The self-contained mini-tape for `net`: reuses `sim.slice.of` directly

    This is the same primitive the notebook's `function` claim evaluates
    against a player's expression -- exposed on its own so a panel can
    exhaustively (or, past the ceiling, by sampling) enumerate every
    assignment of `net`'s own cone leaves, e.g. a flop's D-pin truth table,
    without inventing a second cone-cutting implementation to do it.
    """
    from .sim import slice as tape_slice

    design = _get(handle)
    if net not in design.netlist.nets:
        raise ApiError("unknown_net", f"no such net: {net!r}")
    try:
        sliced = tape_slice.of(design.tape(), [net])
    except tape_slice.UnknownNet as exc:
        raise ApiError("unknown_net", str(exc)) from None
    return serial.to_dict(_slice_view(sliced))


#! recovered analysis: L34-L38, harvested from the honest-attempt investigation


@_endpoint
def sticky(handle: str) -> dict:
    """Every flop whose D pin latches on its own Q (`sequential.sticky`, L37)

    Never says checkpoint or trap: stickiness alone does not determine which.
    """
    from . import sequential

    design = _get(handle)
    found = sequential.sticky(design.graph)
    return {"sticky": [serial.to_dict(s) for s in found]}


@_endpoint
def grouping(handle: str, flops_json: str) -> dict:
    """Split flops into groups linked by mutual D-cone reference (`analysis.grouping.mutual`, L34)"""
    from .analysis import grouping as grouping_

    design = _get(handle)
    flops = _parse(flops_json, "flops_json")
    if not isinstance(flops, list):
        raise ApiError("bad_json", "flops_json must be a list of flop names")
    unknown = sorted(set(flops) - design.graph.seq)
    if unknown:
        raise ApiError("not_a_flop", f"not a flop: {', '.join(unknown)}")
    groups = grouping_.mutual(design.graph, flops)
    return {"groups": [list(g) for g in groups]}


@_endpoint
def weights(handle: str, group_json: str, stimulus_json: str, cycles: int) -> dict:
    """Infer each flop in a group's binary weight by observation (`analysis.weights.infer`, L35)"""
    from .analysis import weights as weights_

    design = _get(handle)
    group = _parse(group_json, "group_json")
    stimulus = _parse(stimulus_json, "stimulus_json")
    if not isinstance(group, list):
        raise ApiError("bad_json", "group_json must be a list of flop names")
    unknown = sorted(set(group) - design.graph.seq)
    if unknown:
        raise ApiError("not_a_flop", f"not a flop: {', '.join(unknown)}")
    found = weights_.infer(design.netlist, group, stimulus=stimulus, cycles=cycles)
    return {"weights": {f: serial.to_dict(w) for f, w in found.items()}}


def _resolvable(graph, group: list[str]) -> list[str]:
    return sorted(f for f in group if graph.d_pin(f) is None)


@_endpoint
def decode_selects(
    handle: str,
    group_json: str,
    control_json: str,
    baseline_json: str,
    perturb_json: str,
) -> dict:
    """Which control-flop values make `group` react to `perturb` at all (`analysis.decode.selects`, L36)"""
    from .analysis import decode

    design = _get(handle)
    group = _parse(group_json, "group_json")
    control = _parse(control_json, "control_json")
    baseline = _parse(baseline_json, "baseline_json")
    perturb = _parse(perturb_json, "perturb_json")
    bad = _resolvable(design.graph, group)
    if bad:
        raise ApiError("not_a_flop", f"not a single-D-pin flop: {', '.join(bad)}")
    hits = decode.selects(design.graph, group, control, baseline, perturb)
    return {"hits": hits}


@_endpoint
def decode_orbit(
    handle: str, group_json: str, stimulus_json: str, start_json: str | None = None
) -> dict:
    """Apply a stimulus repeatedly and classify the resulting state sequence (`analysis.decode.orbit`, L36)"""
    from .analysis import decode

    design = _get(handle)
    group = _parse(group_json, "group_json")
    stimulus = _parse(stimulus_json, "stimulus_json")
    start = _parse(start_json, "start_json")
    bad = _resolvable(design.graph, group)
    if bad:
        raise ApiError("not_a_flop", f"not a single-D-pin flop: {', '.join(bad)}")
    found = decode.orbit(design.graph, group, stimulus, start)
    return serial.to_dict(
        OrbitView(states=[list(s) for s in found.states], kind=found.kind)
    )


def _constraint_of(d: dict) -> "constraints_.Constraint":
    from .analysis import constraints as constraints_

    return constraints_.Constraint(d["name"], tuple(d["elements"]), d["lb"], d["ub"])


def _system_of(d: dict) -> "constraints_.System":
    from .analysis import constraints as constraints_

    return constraints_.System(
        variables=tuple(d["variables"]),
        watched=tuple(d["watched"]),
        constraints=tuple(_constraint_of(c) for c in d["constraints"]),
    )


def _system_view(system) -> dict:
    return serial.to_dict(
        SystemView(
            variables=list(system.variables),
            watched=list(system.watched),
            constraints=[
                ConstraintView(c.name, list(c.elements), c.lb, c.ub)
                for c in system.constraints
            ],
            unconstrained=list(system.unconstrained_elements()),
        )
    )


@_endpoint
def constraints_build(hits_json: str, watched_json: str, targets_json: str) -> dict:
    """Build a `System` directly from measured hits, one `exact` row per target (L38)

    Mirrors `analysis.constraints.from_sensitivity`'s one line, but takes the
    raw `{name: [cycles]}` map the browser already computed (the Sensitivity
    panel runs on the TS gate-tape port of `sim.sensitivity` for interactive
    speed, not on a live Python `SensitivityMap`) rather than requiring one.
    """
    from .analysis import constraints as constraints_

    hits = _parse(hits_json, "hits_json")
    watched = _parse(watched_json, "watched_json")
    targets = _parse(targets_json, "targets_json")
    rows = tuple(
        constraints_.Constraint.exact(name, hits.get(name, []), k)
        for name, k in targets.items()
    )
    variables = tuple(sorted({e for c in rows for e in c.elements}))
    system = constraints_.System(
        variables=variables, watched=tuple(watched), constraints=rows
    )
    return _system_view(system)


@_endpoint
def constraints_add(system_json: str, constraint_json: str) -> dict:
    """`System.with_constraint`, from JSON in to JSON out (L38)"""
    system = _system_of(_parse(system_json, "system_json"))
    row = _constraint_of(_parse(constraint_json, "constraint_json"))
    return _system_view(system.with_constraint(row))


@_endpoint
def constraints_solve(system_json: str, method: str = "dfs", limit: int = 50) -> dict:
    """Solutions to a `System`, up to `limit` -- "a solution" or "how many exist" (L38)

    `method="ilp"` needs `scipy` (the `solve` extra); missing it comes back as
    a clean `ApiError`, not a crash, so the UI can fall back to `"dfs"`.
    """
    system = _system_of(_parse(system_json, "system_json"))
    if method not in ("dfs", "ilp"):
        raise ApiError("bad_json", f"method must be 'dfs' or 'ilp', not {method!r}")
    try:
        solutions = system.solve(method=method, limit=limit)
    except ImportError as exc:
        raise ApiError("missing_dependency", str(exc)) from None
    return {
        "solutions": [list(s) for s in solutions],
        "capped": len(solutions) >= limit,
    }


#! the layout


@_endpoint
def inspect(handle: str) -> dict:
    """Hierarchy, layers, cell mix and whether the GDS is self-contained"""
    from . import loader

    design = _get(handle)
    return serial.to_dict(loader.inspect(design.layout, design.macros()))


@_endpoint
def pins(handle: str) -> dict:
    """The pin oracle for every logic cell used in the design"""
    from .pins import PinOracle

    design = _get(handle)
    layout = design.layout
    oracle = PinOracle(layout, design.macros())
    tech = layout.tech
    used = sorted({name for name, _ in layout.instances() if tech.is_logic_cell(name)})
    cells = {
        cell: [
            {
                "name": pin.name,
                "layer": pin.layer,
                "x": pin.point.x,
                "y": pin.point.y,
                "is_power": pin.is_power,
            }
            for pin in oracle.pins(cell)
        ]
        for cell in used
    }
    return {"cells": cells, "sources": dict(oracle.source)}


@_endpoint
def placement(handle: str, axis: str = "x") -> dict:
    """Where each instance sits, plus the rows and bands it falls into

    Coordinates are microns, which is what `physical.draw.placements` produces
    today.
    """
    from .physical import draw, placement as geometry

    design = _get(handle)
    placed = draw.placements(design.layout, design.netlist)
    points = {p.name: (p.x, p.y) for p in placed}
    return {
        "units": "um",
        "placements": serial.to_dict(placed),
        "rows": serial.to_dict(geometry.rows(points)),
        "bands": serial.to_dict(geometry.bands(points, axis)),
    }


#! analyses


@_endpoint
def registers(handle: str) -> dict:
    """What kind of thing each register is"""
    from . import sequential
    from .analysis import describe

    design = _get(handle)
    found = design.registers(ordered=True)
    roles = sequential.classify(design.netlist, found)
    return {
        "registers": [
            {**serial.to_dict(r), "width": r.width, "description": describe(r)}
            for r in found
        ],
        "roles": serial.to_dict(roles),
        "pipelines": serial.to_dict(sequential.pipelines(roles)),
    }


@_endpoint
def guards(handle: str, min_fanout: int = 4) -> dict:
    """What has to be true for each register to change"""
    design = _get(handle)
    found = design.guards(min_fanout=min_fanout)
    return {
        "guards": [
            {**serial.to_dict(g), "condition": g.condition} for g in found.guards
        ],
        "candidates": list(found.candidates),
        "flops": list(found.flops),
        "ungated": found.ungated,
        # `groups()` is keyed by a tuple of conditions, which json cannot hold
        "groups": [
            {
                "condition": [{"net": net, "value": value} for net, value in key],
                "flops": flops,
            }
            for key, flops in found.groups().items()
        ],
    }


@_endpoint
def ports(handle: str, cycles: int = 64) -> dict:
    """What each pin is for: clock, reset, gate, data"""
    design = _get(handle)
    return serial.to_dict(design.interface(cycles))


@_endpoint
def analyse(handle: str) -> dict:
    """Recover registers and identify what the logic computes"""
    from . import analyse as analysis

    design = _get(handle)
    return serial.to_dict(analysis.analyse(design.netlist))


@_endpoint
def fsm(handle: str, register: str | None = None) -> dict:
    """Recover state machines by exploring each register's reachable states"""
    from . import fsm as control

    design = _get(handle)
    found = design.registers()
    if register is not None:
        found = [r for r in found if r.name == register]
        if not found:
            raise ApiError("unknown_register", f"no such register: {register!r}")
    machines = control.find_state_machines(design.netlist, found)
    return {"machines": [_machine(m) for m in machines]}


def _machine(machine) -> dict:
    """A StateMachine as JSON

    `transitions` is keyed by a `(state, inputs)` tuple and `moore_outputs` by
    an int, neither of which survives `json.dumps`. Both become lists.
    """
    return {
        "register": machine.register,
        "width": machine.width,
        "inputs": list(machine.inputs),
        "reset_state": machine.reset_state,
        "states": list(machine.states),
        "density": machine.density,
        "transitions": [
            {"state": state, "input": values, "next": nxt}
            for (state, values), nxt in sorted(machine.transitions.items())
        ],
        "moore_outputs": [
            {"state": state, "outputs": outputs}
            for state, outputs in sorted(machine.moore_outputs.items())
        ],
    }


@_endpoint
def idioms(handle: str) -> dict:
    """Every net that computes something the library knows a name for"""
    from . import idiom

    design = _get(handle)
    matches = idiom.match(design.netlist)
    chains = idiom.carry_chains(matches)
    return {
        "considered": matches.considered,
        "found": [{**serial.to_dict(m), "width": m.width} for m in matches.found],
        "by_idiom": [
            {"idiom": name, "nets": [m.net for m in found]}
            for name, found in matches.by_idiom().items()
        ],
        "chains": [{**serial.to_dict(c), "width": c.width} for c in chains],
    }


@_endpoint
def normalise(handle: str, fold: bool = True, clean: bool = True) -> dict:
    """Collapse buffers and inverter pairs, propagate constants"""
    from . import normalise as _normalise

    design = _get(handle)
    result = _normalise.normalise(design.netlist, fold=fold, clean=clean)
    payload = serial.to_dict(result)
    payload["netlist"] = result.netlist.to_dict()
    payload["removed"] = result.removed
    return payload


@_endpoint
def truth_table(cell: str) -> dict:
    """The truth table of one library cell, by evaluating its Liberty function"""
    from .functions import base_name, generic_name, lookup
    from .liberty import to_verilog

    found = lookup(cell)
    if found is None:
        raise ApiError("unknown_cell", f"the library does not describe {cell!r}")
    if found.sequential is not None:
        raise ApiError("unknown_cell", f"{cell!r} is sequential; it has no truth table")
    width = len(found.inputs)
    if width > 12:
        raise ApiError("too_wide", f"{cell!r} has {width} inputs; the limit is 12")

    rows = []
    for values in range(1 << width):
        # `inputs[0]` is the most significant bit, so the rows count upwards in
        # the order the header names the pins
        bits = {
            pin: (values >> (width - 1 - i)) & 1 for i, pin in enumerate(found.inputs)
        }
        rows.append({"inputs": bits, "outputs": found.evaluate(bits)})
    return {
        "cell": cell,
        "base_cell": base_name(cell),
        "generic": generic_name(cell),
        "inputs": list(found.inputs),
        "outputs": list(found.outputs),
        "functions": {pin: to_verilog(e) for pin, e in found.functions.items()},
        "rows": rows,
    }


#! simulation


@_endpoint
def sim_run(handle: str, vectors_json: str, watch_json: str | None = None) -> dict:
    """Drive the design and report what the watched nets did, cycle by cycle

    One vector is one clock edge. `watch` defaults to the ports, because a
    design of this size has thousands of nets and the trace is dense.
    """
    design = _get(handle)
    nl = design.netlist
    vectors = _parse(vectors_json, "vectors_json")
    if not isinstance(vectors, list):
        raise ApiError("bad_json", "vectors_json must be a list of {net: 0|1}")
    watch = _parse(watch_json, "watch_json")
    if watch is None:
        watch = sorted(nl.ports)
    missing = [n for n in watch if n not in nl.nets]
    if missing:
        raise ApiError("unknown_net", f"no such net: {missing[0]!r}")

    sim = design.simulator()
    sim.reset()  # the simulator is memoised and carries the last caller's state
    traced: dict[str, list[int]] = {net: [] for net in watch}
    flops: dict[str, list[int]] = {name: [] for name in sorted(sim.state)}
    for vector in vectors:
        values = sim.step(vector)
        for net in watch:
            traced[net].append(values.get(net, 0))
        for name in flops:
            flops[name].append(sim.state[name])
    return {"cycles": len(vectors), "nets": traced, "flops": flops}


@_endpoint
def sim_compile(handle: str) -> dict:
    """The gate tape for this design: the op stream the JS executor runs

    Sending the tape once and scrubbing locally is the point.
    """
    from .sim.tape import UnconnectedPin, UnsupportedFunction

    design = _get(handle)
    try:
        tape = design.tape()
    except UnsupportedFunction as exc:
        raise ApiError(
            "unsupported_cell",
            str(exc),
            {"cell": exc.cell, "pin": exc.pin},
        ) from None
    except UnconnectedPin as exc:
        raise ApiError("unconnected_pin", str(exc)) from None
    return serial.to_dict(
        TapeView(
            tape_version=tape.tape_version,
            n_nets=tape.n_nets,
            n_flops=tape.n_flops,
            n_ops=tape.n_ops,
            inputs=list(tape.inputs),
            ops=list(tape.ops),
            flops=[FlopView(f.d, f.q, f.clk, f.rst, f.set, f.kind) for f in tape.flops],
            consts=[list(pair) for pair in tape.consts],
            names=dict(tape.names),
            flop_names=list(tape.flop_names),
        )
    )


def render_bundle(handle: str, lod: int = 0) -> bytes:
    """The binary render bundle for the die view

    The only function that answers in `bytes` rather than an envelope; a
    failure is a UTF-8 encoded error envelope instead, so the JS side has one
    thing to check either way.

    `lod` is accepted but not yet used to trim the payload -- the bundle
    always carries all three LOD levels (`render.LOD_LEVELS`), and the
    renderer picks which one to draw per frame.
    """
    try:
        design = _get(handle)
        layout = design.layout  # raises NoLayoutAvailable for a netlist-only design
        from . import render as render_module
        from .netlist import build_with_net_ids, trace_design

        macros = design.macros()
        conn = trace_design(layout, macros)
        _, net_names = build_with_net_ids(layout, conn, macros)
        bundle = render_module.build(layout, conn, net_names)
        return bundle.pack()
    except Exception as exc:  # noqa: BLE001 -- nothing may cross the boundary
        return _error_of(exc).encode("utf-8")

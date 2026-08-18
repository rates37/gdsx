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
    """Trace the routing and return the netlist"""
    design = _get(handle)
    _report(progress, "open", 0, 3)
    try:
        design.layout  # a netlist-backed design has none, and needs none
    except NoLayoutAvailable:
        pass
    _report(progress, "extract", 1, 3)
    nl = design.netlist
    _report(progress, "serialise", 2, 3)
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
    _report(progress, "done", 3, 3)
    return payload


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
    """The gate tape for this design -- not built yet"""
    _get(handle)  # so a bad handle still reports as a bad handle
    raise ApiError("unimplemented", "sim_compile needs the gate tape")


def render_bundle(handle: str, lod: int = 0) -> bytes:
    """The binary render bundle for the die view -- not built yet

    The only function that answers in `bytes`. Until L12 lands it answers with
    a UTF-8 encoded error envelope, so the JS side has one thing to check.
    """
    try:
        _get(handle)
        raise ApiError("unimplemented", "render_bundle needs the render extractor")
    except Exception as exc:  # noqa: BLE001 -- nothing may cross the boundary
        return _error_of(exc).encode("utf-8")

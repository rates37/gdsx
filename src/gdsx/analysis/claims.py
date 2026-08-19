"""Turning a player's claim into something a machine can check

A claim is a structured assertion about the design -- "this net is driven by a
NAND2", "this flop's D pin computes `n96 & ~n136`", "these eight flops are a
counter". This module answers one question about each of them: *what would it
take to settle this?* Some claims are settled here and now, by looking the answer
up in the graph. The rest come back as a **job**: one or two `sim.slice.Slice`
objects and an instruction for what to compare, to be evaluated somewhere that
can afford a million evaluations.

The split is not arbitrary. A structural claim is a fact about the netlist and
reading it out of `Graph` is the whole verification. A functional claim is a
statement about every assignment of a cone's leaves, and there can be 2**24 of
them; that grind belongs in the browser's bit-parallel evaluator, which does 32
assignments per machine word. What this module must never do is *guess* -- if a
claim cannot be settled or scheduled, it comes back `UNKNOWN` with the reason,
because "we could not check this" and "this is true" are different answers and
the game exists to keep them apart.

Two rules the rest of the file follows:

**Sampling never proves anything.** Nothing here returns `PROVEN` unless the
evidence is a graph fact or an exhaustive enumeration. The budget that decides
which is which lives with the evaluator, not here, so there is exactly one copy
of it.

**Both sides of a comparison share a frontier.** When a claim compares the design
against a player's expression, the two slices are cut over the *same* free
variables in the *same* order, so an assignment means the same thing to both and
a counterexample decodes to the same nets. `align` is the only way to build such
a pair.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from .. import liberty
from ..core.graph import Graph
from ..sim import slice as tape_slice
from ..sim.slice import Slice
from ..sim.tape import UNUSED, GateTape, Op

#: Every claim kind the notebook can make. Adding one means adding a `_plan_*`
#: function below and a form in the UI; nothing else dispatches on this list.
KINDS = (
    "structural",
    "support",
    "function",
    "role",
    "invariant",
    "requirement",
    "timing",
    "constraint",
)

#: The roles a register group can be claimed to play. The transition table is
#: checked against a model of each; see the notebook's evaluator for the models.
ROLES = ("counter", "lfsr", "shift-reg", "saturating-counter")

#: What a timing claim can assert about a net.
EVENTS = ("high", "low", "rises", "falls", "latches-high")


class ClaimError(ValueError):
    """A claim that cannot be planned, with the code the API reports

    Distinct from a verdict: this is "that is not a well-formed claim about this
    design", not "that claim is false". Naming a net that does not exist is a
    form error; naming one whose driver is not what you thought is a `DISPROVEN`.
    """

    def __init__(self, code: str, message: str, detail: object = None) -> None:
        self.code = code
        self.message = message
        self.detail = detail
        super().__init__(message)


@dataclass(frozen=True)
class Verdict:
    """A verdict settled here, which is always a structural one

    Nothing in this module can produce a counterexample vector, because nothing
    here evaluates anything over vectors -- a structural disproof is settled by
    two names differing, and `observed`/`expected` carry them. The vector-bearing
    verdicts are built by whoever runs the job.
    """

    kind: str  # "PROVEN" | "DISPROVEN" | "UNKNOWN"
    method: str = ""  # "structural" when proven; "" otherwise
    cases: int = 0
    reason: str = ""  # UNKNOWN only
    observed: tuple[str, ...] = ()
    expected: tuple[str, ...] = ()


@dataclass(frozen=True)
class Job:
    """Work handed to the evaluator, with everything it cannot work out itself

    `design` and `claim` share a free-variable list when both are present. The
    evaluator owns the budget: how many assignments to visit and whether the
    result may be called proven is decided from `design.free` in one place, not
    here and not twice.
    """

    engine: str  # "combinational" | "transition" | "sequential"
    check: str  # "equal" | "implies" | "essential" | "role" | "requirement" | "timing" | "constraint"
    design: Slice | None = None
    claim: Slice | None = None
    #: Parsed once here so the evaluator never parses anything (constraint only).
    predicate: dict | None = None


@dataclass(frozen=True)
class Plan:
    """Either a settled verdict or a job, plus what to show behind the `{ }`"""

    kind: str
    call: str
    verdict: Verdict | None = None
    job: Job | None = None
    #: Things the player should know about how this was set up -- what got
    #: pinned, where the tape's frontier differs from the graph's. Shown, not
    #: swallowed: a claim verified under assumptions is only honest if it says
    #: which.
    notes: tuple[str, ...] = ()


#! expressions


#: An identifier in a claim expression. Wider than Liberty's, because real net
#: names include bus indices (`O[7]`) and Liberty pin names never do.
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_$.]*(?:\[\d+(?::\d+)?\])?")


def parse_expression(text: str, nets: Sequence[str] | set[str]) -> liberty.Expr:
    """Parse a player's Boolean expression over `nets`

    Reuses `liberty.parse` rather than growing a second Boolean parser, with the
    one pre-pass it cannot do for us: substitute every net name for a plain
    identifier first. Two reasons it has to happen out here. Net names carry bus
    indices that Liberty's tokeniser has no rule for, and players write `~` where
    Liberty writes `!`. Widening the Liberty tokeniser instead would change how
    every cell function in the library parses, to serve something that is not a
    cell function.

    A name that is not a net of this design is an error rather than a free
    variable. An expression can only be about things that exist, and catching it
    here means the player fixes a typo instead of reading a verdict about a
    variable the design never had.
    """
    if not text or not text.strip():
        raise ClaimError("bad_expression", "an expression is required")

    known = set(nets)
    names: dict[str, str] = {}
    unknown: list[str] = []

    # A placeholder must not be able to collide with a real net name, or the
    # rename back at the end would hand one net's name to another net's variable
    # and the claim would silently be about the wrong thing.
    prefix = "v"
    while any(name.startswith(prefix) for name in known):
        prefix = "_" + prefix

    def substitute(match: re.Match) -> str:
        token = match.group(0)
        if token in known:
            placeholder = names.setdefault(token, f"{prefix}{len(names)}")
            return placeholder
        unknown.append(token)
        return token

    # `~` is the Verilog spelling of Liberty's `!`; both mean the same thing and
    # players type whichever their last tool used.
    normalised = _IDENT.sub(substitute, text.replace("~", "!"))
    if unknown:
        raise ClaimError(
            "unknown_net",
            f"no net named {unknown[0]!r} in this design",
            {"names": sorted(set(unknown))},
        )

    try:
        parsed = liberty.parse(normalised)
    except liberty.ExpressionError as exc:
        raise ClaimError("bad_expression", str(exc)) from None

    back = {placeholder: name for name, placeholder in names.items()}
    return _rename(parsed, back)


def _rename(expr: liberty.Expr, names: Mapping[str, str]) -> liberty.Expr:
    if expr[0] == "var":
        return ("var", names[expr[1]])
    if expr[0] == "const":
        return expr
    return (expr[0], *(_rename(child, names) for child in expr[1:]))


_BINARY = {"and": Op.AND2, "or": Op.OR2, "xor": Op.XOR2}


def expression_slice(
    expr: liberty.Expr, free: Sequence[str], name: str = "claim"
) -> Slice:
    """Compile a parsed expression into a `Slice` over exactly `free`

    Emitted in the tape's own opcode encoding, so the evaluator that runs the
    design side runs this side too -- one evaluator, one set of opcode semantics,
    no second interpreter to disagree with the first. `free` is taken as given
    including variables the expression never mentions, because it is the
    assignment order shared with the design slice.
    """
    ids = {net: i for i, net in enumerate(free)}
    ops: list[int] = []
    counter = len(ids)

    def emit(node: liberty.Expr) -> int:
        nonlocal counter
        kind = node[0]
        if kind == "var":
            if node[1] not in ids:
                raise ClaimError(
                    "unknown_net", f"{node[1]!r} is not a variable of this comparison"
                )
            return ids[node[1]]
        out = counter
        counter += 1
        if kind == "const":
            ops.extend([Op.CONST1 if node[1] else Op.CONST0, out, UNUSED, UNUSED, UNUSED, UNUSED])
        elif kind == "not":
            ops.extend([Op.NOT, out, emit(node[1]), UNUSED, UNUSED, UNUSED])
        else:
            left, right = emit(node[1]), emit(node[2])
            ops.extend([_BINARY[kind], out, left, right, UNUSED, UNUSED])
        return out

    # `emit` appends a child's ops during the argument evaluation of its
    # parent's `ops.extend`, so the stream comes out in post-order and one
    # forward pass settles it -- the same property the tape compiler relies on.
    root = emit(expr)
    return Slice(
        ops=tuple(int(v) for v in ops),
        free=tuple(free),
        free_ids=tuple(range(len(free))),
        targets=(root,),
        target_names=(name,),
        consts=(),
        n_values=counter,
    )


def align(
    tape: GateTape,
    targets: Sequence[str],
    *,
    expr: liberty.Expr | None = None,
    free_first: Sequence[str] = (),
    pinned: Mapping[str, int] | None = None,
    name: str = "claim",
) -> tuple[Slice, Slice | None, tuple[str, ...]]:
    """Cut the design side and the claim side over one shared frontier

    Two passes, because the frontier is not known until the first cut: take the
    design's own free variables, add any the expression mentions that the design
    does not read, then re-cut both over that union. An expression variable the
    design's cone never touches is kept rather than rejected -- claiming a flop's
    D depends on a net it does not is a claim the player is entitled to make, and
    it should come back `DISPROVEN` with a counterexample, not as a form error.
    """
    first = tape_slice.of(tape, targets, free=free_first, pinned=pinned)
    union = list(first.free)
    if expr is not None:
        for net in sorted(liberty.variables(expr)):
            if net not in union:
                union.append(net)

    design = tape_slice.of(tape, targets, free=union, pinned=pinned)
    claim = expression_slice(expr, design.free, name) if expr is not None else None
    # `first.free` is the design's own frontier, before the expression widened
    # it. Callers report against that one: a variable the player introduced is
    # not the compiled design reading something extra.
    return design, claim, first.free


#! stimulus predicates, for the constraint claim


#: The measurements a constraint predicate can make about one input track. Each
#: is an integer, so the predicate language is comparisons rather than Boolean
#: algebra -- "the key has exactly 22 pulses", not "the key is high".
MEASURES = {
    "count": "how many cycles the track is high",
    "gaps": "how many runs of low cycles separate the pulses",
    "mingap": "the shortest run of low cycles between two pulses",
    "maxgap": "the longest run of low cycles between two pulses",
    "first": "the first cycle the track is high, or -1",
    "last": "the last cycle the track is high, or -1",
    "runs": "how many runs of consecutive high cycles there are",
}

_COMPARISONS = ("==", "!=", "<=", ">=", "<", ">")

_TERM = re.compile(
    r"""\s*(?P<measure>[a-z]+)\s*\(\s*(?P<port>[^,)]+?)\s*
        (?:,\s*(?P<from>\d+)\s*,\s*(?P<to>\d+)\s*)?\)\s*
        (?P<op>==|!=|<=|>=|<|>)\s*(?P<value>-?\d+)\s*""",
    re.VERBOSE,
)


def parse_predicate(text: str, ports: Sequence[str]) -> dict:
    """Parse a constraint predicate into a small tree the evaluator walks

    Deliberately not Python and not the Boolean expression language: a claim
    about a key is a claim about counts and gaps, which are integers, and giving
    it the same syntax as a net expression would invite `count(I) & 3`. The
    grammar is comparisons of a measurement against a number, combined with
    `&`, `|`, `!` and parentheses.

        count(I) == 22 & mingap(I) >= 2
        count(I, 0, 40) <= 3 | first(I) > 7
    """
    if not text or not text.strip():
        raise ClaimError("bad_predicate", "a predicate is required")

    known = set(ports)
    terms: list[dict] = []

    def substitute(match: re.Match) -> str:
        measure = match.group("measure")
        if measure not in MEASURES:
            raise ClaimError(
                "bad_predicate",
                f"no measurement called {measure!r}; try one of "
                + ", ".join(sorted(MEASURES)),
            )
        port = match.group("port").strip()
        if port not in known:
            raise ClaimError(
                "unknown_port", f"{port!r} is not an input of this design"
            )
        window = None
        if match.group("from") is not None:
            window = [int(match.group("from")), int(match.group("to"))]
        terms.append(
            {
                "measure": measure,
                "port": port,
                "window": window,
                "op": match.group("op"),
                "value": int(match.group("value")),
            }
        )
        return f" v{len(terms) - 1} "

    reduced = _TERM.sub(substitute, text.replace("~", "!"))
    if not terms:
        raise ClaimError(
            "bad_predicate",
            "a predicate is a comparison like `count(I) == 22`, "
            "optionally combined with & | ! and parentheses",
        )
    try:
        shape = liberty.parse(reduced)
    except liberty.ExpressionError as exc:
        raise ClaimError("bad_predicate", str(exc)) from None

    return _predicate_tree(shape, terms)


def _predicate_tree(expr: liberty.Expr, terms: Sequence[dict]) -> dict:
    if expr[0] == "var":
        return {"node": "term", **terms[int(expr[1][1:])]}
    if expr[0] == "const":
        raise ClaimError("bad_predicate", "a bare 0 or 1 is not a predicate")
    if expr[0] == "not":
        return {"node": "not", "of": [_predicate_tree(expr[1], terms)]}
    return {
        "node": expr[0],
        "of": [_predicate_tree(child, terms) for child in expr[1:]],
    }


#! planning, one function per claim kind


def plan(graph: Graph, tape: GateTape, claim: Mapping) -> Plan:
    """Settle `claim` or schedule it. The module's one entry point."""
    kind = claim.get("kind")
    if kind not in KINDS:
        raise ClaimError(
            "bad_claim",
            f"unknown claim kind {kind!r}; expected one of {', '.join(KINDS)}",
        )
    return _PLANNERS[kind](_Context(graph, tape), claim)


@dataclass
class _Context:
    graph: Graph
    tape: GateTape
    notes: list[str] = field(default_factory=list)
    _by_id: dict[int, str] = field(default_factory=dict)

    def net(self, claim: Mapping, key: str) -> str:
        name = claim.get(key)
        if not isinstance(name, str) or name not in self.graph.netlist.nets:
            raise ClaimError("unknown_net", f"no net named {name!r} in this design")
        return name

    def flop(self, claim: Mapping, key: str) -> str:
        name = claim.get(key)
        if not isinstance(name, str) or name not in self.graph.seq:
            raise ClaimError("unknown_flop", f"{name!r} is not a flop of this design")
        return name

    def d_net(self, flop: str) -> str:
        """The net on a flop's D pin, as the tape names it"""
        if not self._by_id:
            for name, ident in sorted(self.tape.names.items()):
                self._by_id.setdefault(ident, name)
        try:
            position = self.tape.flop_names.index(flop)
        except ValueError:
            raise ClaimError("unknown_flop", f"{flop!r} is not on the tape") from None
        name = self._by_id.get(self.tape.flops[position].d)
        if name is None:
            raise ClaimError("unconnected_pin", f"{flop}'s D pin is not on a named net")
        return name

    def q_net(self, flop: str) -> str:
        try:
            return tape_slice.flop_q_nets(self.tape, [flop])[0]
        except tape_slice.UnknownNet as exc:
            raise ClaimError("unknown_flop", str(exc)) from None

    def ports(self) -> list[str]:
        nl = self.graph.netlist
        return sorted(net for net, kind in nl.ports.items() if kind == "input")

    def as_nets(self, leaves) -> set[str]:
        """A `Graph.support` result, with its flop instances turned into nets

        `support` reports a flop leaf by instance name and everything else by net
        name. The notebook speaks nets throughout -- it is what the cone walker
        shows and what a counterexample has to be expressed in -- so the mixed
        form is converted here, once, rather than at four call sites.
        """
        found = set()
        for leaf in leaves:
            found.add(self.q_net(leaf) if leaf in self.graph.seq else leaf)
        return found


def _plan_structural(ctx: _Context, claim: Mapping) -> Plan:
    from ..functions import base_name, generic_name

    net = ctx.net(claim, "net")
    wanted = str(claim.get("cell", "")).strip()
    if not wanted:
        raise ClaimError("bad_claim", "a cell name is required")

    call = f"gdsx.core.graph.Graph.of(nl).driver_of({net!r})"
    driver = ctx.graph.driver_of(net)
    if driver is None:
        leaf = ctx.graph.leaf(net)
        return Plan(
            "structural",
            call,
            verdict=Verdict(
                "DISPROVEN",
                observed=(leaf.kind.value if leaf else "undriven",),
                expected=(wanted,),
            ),
        )

    # A player may name the cell any of the three ways the UI shows it: the full
    # sky130 name, the Liberty base name, or the generic label. All three mean
    # the same instance and rejecting two of them would be pedantry.
    spellings = {driver.cell, base_name(driver.cell), generic_name(driver.cell)}
    matched = wanted in spellings or wanted.lower() in {s.lower() for s in spellings}
    if matched:
        return Plan(
            "structural",
            call,
            verdict=Verdict("PROVEN", method="structural", cases=1),
        )
    return Plan(
        "structural",
        call,
        verdict=Verdict(
            "DISPROVEN", observed=(driver.cell,), expected=(wanted,)
        ),
    )


def _plan_support(ctx: _Context, claim: Mapping) -> Plan:
    flop = ctx.flop(claim, "flop")
    sense = claim.get("sense", "structural")
    if sense not in ("structural", "functional"):
        raise ClaimError("bad_claim", "sense must be 'structural' or 'functional'")

    claimed = claim.get("leaves")
    if not isinstance(claimed, list) or not all(isinstance(x, str) for x in claimed):
        raise ClaimError("bad_claim", "leaves must be a list of net names")
    call = f"gdsx.core.graph.Graph.of(nl).d_support({flop!r})"

    actual = ctx.as_nets(ctx.graph.d_support(flop))
    if sense == "structural":
        wanted = set(claimed)
        if wanted == actual:
            return Plan(
                "support",
                call,
                verdict=Verdict("PROVEN", method="structural", cases=len(actual)),
            )
        return Plan(
            "support",
            call,
            verdict=Verdict(
                "DISPROVEN",
                observed=tuple(sorted(actual)),
                expected=tuple(sorted(wanted)),
            ),
        )

    design, _, frontier = align(ctx.tape, [ctx.d_net(flop)])
    _note_frontier(ctx, set(frontier), actual)
    return Plan(
        "support",
        f"{call}  # then check each leaf actually changes D",
        job=Job("combinational", "essential", design=design),
        notes=tuple(ctx.notes),
    )


def _plan_function(ctx: _Context, claim: Mapping) -> Plan:
    flop = ctx.flop(claim, "flop")
    text = claim.get("expression", "")
    expr = parse_expression(text, ctx.graph.netlist.nets)

    target = ctx.d_net(flop)
    design, compiled, frontier = align(ctx.tape, [target], expr=expr, name=str(text))
    _note_frontier(ctx, set(frontier), ctx.as_nets(ctx.graph.d_support(flop)))
    return Plan(
        "function",
        f"gdsx.core.graph.Graph.of(nl).formula({target!r})",
        job=Job("combinational", "equal", design=design, claim=compiled),
        notes=tuple(ctx.notes),
    )


def _plan_role(ctx: _Context, claim: Mapping) -> Plan:
    group = claim.get("group")
    if not isinstance(group, list) or not group:
        raise ClaimError("bad_claim", "a role claim needs a group of flops")
    for name in group:
        ctx.flop({"flop": name}, "flop")
    role = claim.get("role")
    if role not in ROLES:
        raise ClaimError(
            "bad_claim", f"role must be one of {', '.join(ROLES)}, not {role!r}"
        )
    width = claim.get("width", len(group))
    if width != len(group):
        raise ClaimError(
            "bad_claim",
            f"width {width} does not match the {len(group)} flops named",
        )

    # In the group's own order: bit position 0 of a counter's state is the
    # first flop the player named, and re-sorting would silently renumber it.
    q_nets = [ctx.q_net(f) for f in group]
    d_nets = [ctx.d_net(f) for f in group]

    # Everything the group's next state depends on other than the group itself
    # gets held at the stimulus the claim was made under. A role claim is always
    # conditional on that, so it is recorded in the notes rather than hidden.
    stimulus = claim.get("stimulus") or {}
    first = tape_slice.of(ctx.tape, d_nets, free=q_nets)
    outside = [net for net in first.free if net not in set(q_nets)]
    pinned = {net: int(stimulus.get(net, 0)) for net in outside}
    if pinned:
        held = ", ".join(f"{net}={value}" for net, value in sorted(pinned.items()))
        ctx.notes.append(f"holds {held} for the whole walk; the claim is under that")

    design = tape_slice.of(ctx.tape, d_nets, free=q_nets, pinned=pinned)
    return Plan(
        "role",
        f"gdsx.analysis.decode.orbit(graph, {group!r}, {dict(pinned)!r})",
        job=Job("transition", "role", design=design),
        notes=tuple(ctx.notes),
    )


def _plan_invariant(ctx: _Context, claim: Mapping) -> Plan:
    net = ctx.net(claim, "net")
    value = claim.get("value", 1)
    if value not in (0, 1):
        raise ClaimError("bad_claim", "value must be 0 or 1")
    condition = parse_expression(claim.get("condition", ""), ctx.graph.netlist.nets)
    regime = claim.get("regime", "frame")
    if regime not in ("frame", "sequential"):
        raise ClaimError("bad_claim", "regime must be 'frame' or 'sequential'")

    if regime == "sequential":
        # Nothing to slice: the condition is evaluated against whatever the nets
        # actually did, cycle by cycle, over generated stimuli. No amount of that
        # proves anything, which the UI says before the player presses verify.
        variables = sorted(liberty.variables(condition))
        return Plan(
            "invariant",
            "gdsx.api.sim_run(handle, vectors_json)",
            job=Job(
                "sequential",
                "implies",
                claim=expression_slice(condition, variables, str(claim.get("condition"))),
            ),
            notes=("checked over generated stimuli; sampling cannot prove it",),
        )

    target = _through_flop(ctx, net)
    design, compiled, _ = align(
        ctx.tape, [target], expr=condition, name=str(claim.get("condition"))
    )
    return Plan(
        "invariant",
        f"gdsx.api.requirements(handle, {target!r}, value={value})",
        job=Job("combinational", "implies", design=design, claim=compiled),
        notes=tuple(ctx.notes),
    )


def _plan_requirement(ctx: _Context, claim: Mapping) -> Plan:
    from . import justify

    output = ctx.net(claim, "output")
    value = claim.get("value", 1)
    flop = ctx.flop(claim, "flop")
    flop_value = claim.get("flop_value", 1)
    if value not in (0, 1) or flop_value not in (0, 1):
        raise ClaimError("bad_claim", "values must be 0 or 1")

    q = ctx.q_net(flop)
    target = _through_flop(ctx, output)
    call = f"gdsx.api.requirements(handle, {target!r}, value={value})"

    try:
        found = justify.requirements(ctx.graph, target, value)
    except justify.Unenumerable as exc:
        return Plan(
            "requirement",
            call,
            verdict=Verdict("UNKNOWN", reason=f"unenumerable: {exc}"),
        )

    if q in found.forced:
        forced = found.forced[q]
        if forced == flop_value:
            return Plan(
                "requirement",
                call,
                verdict=Verdict(
                    "PROVEN", method="structural", cases=len(found.forced)
                ),
            )
        # Backward justification did not merely fail to find it -- it found the
        # opposite. That is a disproof, not an inconclusive search.
        return Plan(
            "requirement",
            call,
            verdict=Verdict(
                "DISPROVEN",
                observed=(f"{q} == {forced}",),
                expected=(f"{q} == {flop_value}",),
            ),
        )

    # Not forced by the AND-tree walk. It may still be required -- justification
    # gives up at a disjunction rather than guessing -- so look for the thing
    # that would settle it: an assignment where the output takes `value` and the
    # flop does not take `flop_value`.
    design, _, _ = align(ctx.tape, [target, q])
    ctx.notes.append(
        f"{q} is not forced by backward justification; "
        "searching for a counterexample instead"
    )
    return Plan(
        "requirement",
        call,
        job=Job("combinational", "requirement", design=design),
        notes=tuple(ctx.notes),
    )


def _plan_timing(ctx: _Context, claim: Mapping) -> Plan:
    net = ctx.net(claim, "net")
    event = claim.get("event")
    if event not in EVENTS:
        raise ClaimError(
            "bad_claim", f"event must be one of {', '.join(EVENTS)}, not {event!r}"
        )
    cycles = claim.get("cycles")
    if not isinstance(cycles, list) or not all(isinstance(c, int) for c in cycles):
        raise ClaimError("bad_claim", "cycles must be a list of cycle numbers")

    return Plan(
        "timing",
        f"gdsx.api.sim_run(handle, vectors_json, watch_json={[net]!r})",
        job=Job("sequential", "timing"),
        notes=("replay of one stimulus; the verdict is about that stimulus only",),
    )


def _plan_constraint(ctx: _Context, claim: Mapping) -> Plan:
    predicate = parse_predicate(str(claim.get("predicate", "")), ctx.ports())
    ctx.net(claim, "output")  # the net that has to latch for a key to count
    return Plan(
        "constraint",
        "gdsx.api.sim_run(handle, vectors_json)",
        job=Job("sequential", "constraint", predicate=predicate),
    )


_PLANNERS = {
    "structural": _plan_structural,
    "support": _plan_support,
    "function": _plan_function,
    "role": _plan_role,
    "invariant": _plan_invariant,
    "requirement": _plan_requirement,
    "timing": _plan_timing,
    "constraint": _plan_constraint,
}


def _through_flop(ctx: _Context, net: str) -> str:
    """`net`, or the D net behind it when `net` is a flop's Q

    Within one clock frame a flop's Q is a free variable, not a function of
    anything -- asking "what must hold for `success` to be 1" of the Q net gets
    the honest but useless answer "`success` must be 1". The question the player
    means is about the D input, one cycle earlier, which is the same step the
    cone walker makes explicit rather than silently. So it is taken here, and
    said out loud in the notes: a verdict about a different cycle than the player
    thinks is exactly the confusion this game exists to prevent.
    """
    driver = ctx.graph.driver_of(net)
    if driver is None or driver.instance not in ctx.graph.seq:
        return net
    d_net = ctx.d_net(driver.instance)
    ctx.notes.append(
        f"{net} is driven by flop {driver.instance}; this is about its D input "
        f"({d_net}), which is one cycle earlier"
    )
    return d_net


def _note_frontier(ctx: _Context, from_tape: set[str], from_graph: set[str]) -> None:
    """Record where the tape reads something different from what the graph says

    They agree on every design seen so far, and they are still not the same
    question: `Graph.support` answers for the netlist, a slice answers for the
    compiled tape, and the tape folds constants the netlist still lists. The
    evaluator is running the tape, so the tape's frontier is what a verdict is
    actually about -- if the two ever disagree the player is told, rather than
    one of them being quietly preferred.
    """
    if from_tape == from_graph:
        return
    only_graph = sorted(from_graph - from_tape)
    only_tape = sorted(from_tape - from_graph)
    if only_graph:
        ctx.notes.append(
            "the netlist lists "
            + ", ".join(only_graph)
            + " upstream, but the compiled design does not read them; "
            "they are not part of this verdict"
        )
    if only_tape:
        ctx.notes.append("the compiled design also reads " + ", ".join(only_tape))
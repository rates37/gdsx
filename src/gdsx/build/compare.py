"""Structural comparison of two netlists.

The build loop needs an oracle: does the GDS that came out extract to the
netlist that went in? Everything cheap is checked first (instance count, cell
multiset, floating pins, shorts, net count), and then the strong check --
"every instance's pin->net map agrees, up to renaming".

Keying on instance name would seem natural, on the grounds that
`netlist.build` assigns them deterministically. That holds when comparing two
extractions of the same layout; it does not hold here, because the netlist
going *in* comes from `synth` and names an inverter `inv_1` where extraction
names it `inv_2_7`. So the bijection has to be solved for on both sides,
instances as well as nets.

That is a graph isomorphism in principle, and in practice it is not hard:
the two graphs are the same graph, the ports are labelled identically in
both, and cell types are strong colours to begin with. Colour refinement
from those seeds separates almost everything; whatever it leaves tied is
resolved by trying the possibilities, and the pin-by-pin check at the end
means a wrong guess is caught rather than believed.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from ..netlist import Netlist

MAX_REFINE = 40  # colour-refinement rounds; a fixpoint arrives far sooner
MAX_GUESSES = 5000  # individualisation steps before giving up


@dataclass
class Comparison:
    """The result of comparing an intended netlist against an extracted one."""

    problems: list[str] = field(default_factory=list)
    instances: dict[str, str] = field(default_factory=dict)  # expected -> actual
    nets: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.problems

    def __str__(self) -> str:
        if self.ok:
            return (
                f"equivalent: {len(self.instances)} instances, "
                f"{len(self.nets)} nets matched"
            )
        return "\n".join(self.problems)


@dataclass(frozen=True)
class _Graph:
    """One netlist as a bipartite instance/net graph, power stripped out.

    Power is excluded on both sides deliberately. A synthesised netlist does
    not carry `VPWR`/`VGND` as nets at all, while an extracted one connects
    every cell to them, so including them would make two netlists that
    describe the same circuit look different -- and they carry no
    information, since every cell is on both.
    """

    cell: dict[str, str]  # instance -> cell type
    pins: dict[str, dict[str, str]]  # instance -> pin -> net
    refs: dict[str, list[tuple[str, str]]]  # net -> [(instance, pin), ...]
    ports: dict[str, str]  # net -> direction


def _graph(nl: Netlist) -> _Graph:
    power = set(nl.power_nets)
    cell: dict[str, str] = {}
    pins: dict[str, dict[str, str]] = {}
    refs: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for inst in nl.instances:
        cell[inst.name] = inst.cell
        kept = {p: n for p, n in inst.connections.items() if n not in power}
        pins[inst.name] = kept
        for p, n in kept.items():
            refs[n].append((inst.name, p))
    ports = {n: d for n, d in nl.ports.items() if n not in power}
    return _Graph(cell, pins, dict(refs), ports)


def _refine(
    g: _Graph, inst_colour: dict[str, str], net_colour: dict[str, str]
) -> tuple[dict[str, str], dict[str, str]]:
    """Colour refinement to a fixpoint: an instance's colour absorbs its
    pins' nets' colours, a net's colour absorbs its instances' colours.
    """
    for _ in range(MAX_REFINE):
        new_inst = _compact({
            i: str(
                (inst_colour[i], tuple(sorted((p, net_colour[n]) for p, n in ps.items())))
            )
            for i, ps in g.pins.items()
        })
        new_net = _compact({
            n: str(
                (net_colour[n], tuple(sorted((inst_colour[i], p) for i, p in rs)))
            )
            for n, rs in g.refs.items()
        })
        if (
            _classes(new_inst) == _classes(inst_colour)
            and _classes(new_net) == _classes(net_colour)
        ):
            return inst_colour, net_colour
        inst_colour, net_colour = new_inst, new_net
    return inst_colour, net_colour


def _compact(colour: dict[str, str]) -> dict[str, str]:
    """Renumber colours to short ids, preserving the partition exactly.

    Each refinement round builds a node's new colour out of its old colour
    plus its neighbours' colours. Kept verbatim, that string roughly doubles
    in length every round, so on a few hundred instances the colours reach
    gigabytes and the process is killed long before the fixpoint. Only the
    partition matters, never the text, so the values are renumbered here.
    Sorted so the numbering -- and therefore the search order downstream --
    stays deterministic.
    """
    ids = {c: f"c{i}" for i, c in enumerate(sorted(set(colour.values())))}
    return {k: ids[c] for k, c in colour.items()}


def _classes(colour: dict[str, str]) -> set[frozenset[str]]:
    groups: dict[str, set[str]] = defaultdict(set)
    for k, c in colour.items():
        groups[c].add(k)
    return {frozenset(v) for v in groups.values()}


def _seed(g: _Graph) -> tuple[dict[str, str], dict[str, str]]:
    inst = {i: c for i, c in g.cell.items()}
    net = {n: f"port:{g.ports[n]}:{n}" if n in g.ports else "" for n in g.refs}
    return inst, net


def _buckets(colour: dict[str, str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = defaultdict(list)
    for k in sorted(colour):
        out[colour[k]].append(k)
    return out


def _solve(
    ge: _Graph, ga: _Graph, budget: list[int]
) -> dict[str, str] | None:
    """An instance bijection agreeing with colour refinement, or None.

    Refine; if every colour class is a singleton on both sides the mapping
    reads straight off. Otherwise pick the smallest ambiguous class, pin one
    expected member to each candidate in turn (individualisation), and
    recurse. `budget` bounds the total number of guesses.
    """
    ie, ne = _refine(ge, *_seed(ge))
    ia, na = _refine(ga, *_seed(ga))
    return _search(ge, ga, ie, ne, ia, na, budget)


def _search(ge, ga, ie, ne, ia, na, budget) -> dict[str, str] | None:
    be, ba = _buckets(ie), _buckets(ia)
    if set(be) != set(ba) or any(len(be[c]) != len(ba[c]) for c in be):
        return None
    if _buckets(ne).keys() != _buckets(na).keys():
        return None

    ambiguous = sorted((len(v), c) for c, v in be.items() if len(v) > 1)
    if not ambiguous:
        return {be[c][0]: ba[c][0] for c in be}

    _, colour = ambiguous[0]
    fixed = be[colour][0]
    for candidate in ba[colour]:
        if budget[0] <= 0:
            return None
        budget[0] -= 1
        got = _search(
            ge,
            ga,
            *_refine(ge, {**ie, fixed: f"{colour}#fixed"}, ne),
            *_refine(ga, {**ia, candidate: f"{colour}#fixed"}, na),
            budget,
        )
        if got is not None:
            return got
    return None


def compare(expected: Netlist, actual: Netlist) -> Comparison:
    """Check that `actual` (extracted from a built GDS) implements `expected`
    (what the placer was given).
    """
    out = Comparison()
    say = out.problems.append

    if len(expected.instances) != len(actual.instances):
        say(
            f"instance count: expected {len(expected.instances)}, "
            f"extracted {len(actual.instances)}"
        )
    ce = Counter(i.cell for i in expected.instances)
    ca = Counter(i.cell for i in actual.instances)
    if ce != ca:
        for cell in sorted(set(ce) | set(ca)):
            if ce[cell] != ca[cell]:
                say(f"cell {cell}: expected {ce[cell]}, extracted {ca[cell]}")
    if actual.floating:
        say(f"{len(actual.floating)} floating pins, e.g. {sorted(actual.floating)[:5]}")
    if actual.conflicts:
        say(f"{len(actual.conflicts)} conflicting pins, e.g. {sorted(actual.conflicts)[:5]}")

    ge, ga = _graph(expected), _graph(actual)
    if len(ge.refs) != len(ga.refs):
        say(f"net count: expected {len(ge.refs)}, extracted {len(ga.refs)} (excluding power)")

    for net, direction in sorted(ge.ports.items()):
        if net not in ga.ports:
            say(f"port {net}: missing from the extracted netlist")
        elif ga.ports[net] != direction:
            say(f"port {net}: expected {direction}, extracted {ga.ports[net]}")
    for net in sorted(set(ga.ports) - set(ge.ports)):
        say(f"port {net}: extracted but not in the intended netlist")

    if out.problems:
        return out

    mapping = _solve(ge, ga, [MAX_GUESSES])
    if mapping is None:
        say("no bijection between the two netlists' instances")
        return out

    # The mapping came out of a heuristic; this is the part that is actually
    # believed. Every pin of every instance has to agree, and the induced
    # net map has to be a bijection that fixes every port.
    nets: dict[str, str] = {}
    seen: dict[str, str] = {}
    for e_inst, a_inst in sorted(mapping.items()):
        e_pins, a_pins = ge.pins[e_inst], ga.pins[a_inst]
        if e_pins.keys() != a_pins.keys():
            say(f"{e_inst} -> {a_inst}: pins {sorted(e_pins)} vs {sorted(a_pins)}")
            continue
        for pin, e_net in sorted(e_pins.items()):
            a_net = a_pins[pin]
            if nets.setdefault(e_net, a_net) != a_net:
                say(f"net {e_net}: maps to both {nets[e_net]} and {a_net}")
            if seen.setdefault(a_net, e_net) != e_net:
                say(f"extracted net {a_net}: claimed by both {seen[a_net]} and {e_net}")
    for net in sorted(ge.ports):
        if nets.get(net) != net:
            say(f"port {net}: maps to {nets.get(net)!r}, not to itself")

    if not out.problems:
        out.instances.update(mapping)
        out.nets.update(nets)
    return out
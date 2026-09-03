"""Backward justification: what must be true upstream for a net to take a value.

Given `net` and a target `value`, `requirements` pushes that value back through
each gate's Liberty function and reports two things, kept strictly apart:

- forced: literals that hold in *every* way of achieving the target. These
  are derivations. `dfrtp_2_22.Q` must be 0, full stop.
- choices: disjunctions that are not resolved. An OR fed a target of 1
  forces nothing; it only records that one of its inputs must go high.

Collapsing the two would turn "must" into "might", so nothing here ever does.

Each gate is handled by **enumerating its Liberty truth table**, not by matching
on its cell name. Standard cells have at most five inputs, so the enumeration is
at most 32 rows, and it gets the awkward cells right for free: `and4b`'s
inverted `A_N` pin, and a `mux2`'s don't-care on the unselected data pin, both
fall out of the rows rather than out of a naming rule.

`forced_by` and `conflict` are the same machinery run forwards.

Every graph question goes through `core.graph.Graph`; this module contains no
traversal of its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from itertools import product

from ..core.graph import Graph
from ..liberty import Cell, Expr, evaluate, variables

# (net, required value)
Literal = tuple[str, int]

# A standard cell has at most five inputs, so 32 rows. A function wide enough to
# break this budget is not something this module should be guessing at.
MAX_ENUMERATED_INPUTS = 12


class Unenumerable(ValueError):
    """A cell's function has too many variables to enumerate exhaustively"""


@dataclass(frozen=True)
class Choice:
    """One unresolved disjunction: `net` reaches `value` by any one option.

    Each option is a cube: a set of literals that together suffice. No literal
    in a choice is forced, because another option could be taken instead.

    Options are over the immediate inputs of the gate driving `net` and are
    deliberately not expanded further: recursing into every branch of a
    reconvergent cone is how this kind of analysis goes exponential.
    """

    net: str
    value: int
    options: tuple[frozenset[Literal], ...]


@dataclass(frozen=True)
class Requirements:
    """What `net == value` demands upstream.

    `forced` is net-keyed, so reconvergence dedups and contradictions are found
    in one namespace. `leaves` is the view for reading: it names flops and
    constants the way `Graph.label` does, and covers only the literals that
    ended the walk.
    """

    net: str
    value: int
    forced: dict[str, int]
    choices: tuple[Choice, ...]
    conflicts: tuple[str, ...]
    labels: dict[str, str]  # net -> Graph.label(net), forced leaves only

    @property
    def consistent(self) -> bool:
        """False when the target is unachievable: some net was implied 0 and 1."""
        return not self.conflicts

    @property
    def leaves(self) -> dict[str, int]:
        """label -> value for the forced literals that ended the walk.

        A flop reads as `"dfrtp_2_22.Q"`, a primary input as its net name.
        """
        return {label: self.forced[net] for net, label in self.labels.items()}


@lru_cache(maxsize=None)
def _table(expr: Expr) -> tuple[tuple[str, ...], tuple[tuple[int, ...], ...]]:
    """(variable names, every row over them) for one cell function.

    Cached on the expression, which is a plain tuple: the whole design uses a
    few dozen distinct cell functions, so this is computed a few dozen times.
    """
    names = tuple(sorted(variables(expr)))
    if len(names) > MAX_ENUMERATED_INPUTS:
        raise Unenumerable(
            f"{len(names)} variables in a cell function: {', '.join(names)}"
        )
    return names, tuple(product((0, 1), repeat=len(names)))


def _fixed(graph: Graph, net: str) -> int | None:
    """The value `net` is nailed to, or None if it is free to move.

    Covers the two ways a net is constant: it is a supply, or it is driven by a
    tie cell -- `conb_1`'s `HI`/`LO` pins have Liberty functions with no
    variables at all. A tie is not a power net, so `Graph.leaf` does not catch
    it, and a requirement placed on one would be nonsense.
    """
    if net in graph.netlist.power_nets:
        return 0 if net.endswith("GND") else 1
    ref = graph.driver_of(net)
    if ref is None or ref.instance in graph.seq:
        return None
    cell = graph.cell_of[ref.instance]
    expr = cell.functions[ref.pin]
    return None if variables(expr) else evaluate(expr, {})


def _consistent_rows(
    expr: Expr, cell: Cell, conns: dict[str, str], known: dict[str, int]
) -> tuple[tuple[str, ...], list[tuple[int, ...]]]:
    """Rows of `expr` compatible with the net values already in `known`.

    A variable that is not an input pin, or whose pin the instance leaves
    unconnected, constrains nothing. Both cases are quantified over rather
    than assumed, so an unconnected pin never fabricates a requirement.
    """
    names, rows = _table(expr)
    fixed = [
        (i, known[conns[p]])
        for i, p in enumerate(names)
        if p in cell.inputs and p in conns and conns[p] in known
    ]
    return names, [r for r in rows if all(r[i] == v for i, v in fixed)]


def _primes(
    rows: list[tuple[int, ...]], free: list[int]
) -> set[tuple[int | None, ...]]:
    """Prime implicants of `rows` over the positions in `free`.

    Quine-McCluskey, and cheap enough to be uninteresting: at most five free
    variables means at most 32 cubes to merge. Without it a 4-input AND asked
    for 0 reports fifteen minterms instead of the four terms a reader wants:
    "A_N is 1", "B is 0", "C is 0", "D is 0".
    """
    cubes = {tuple(r[i] for i in free) for r in rows}
    primes: set[tuple[int | None, ...]] = set()
    while cubes:
        merged: set[tuple[int | None, ...]] = set()
        used: set[tuple[int | None, ...]] = set()
        for a in cubes:
            for b in cubes:
                if a is b:
                    continue
                differ = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
                if len(differ) != 1 or a[differ[0]] is None or b[differ[0]] is None:
                    continue
                used.add(a)
                used.add(b)
                merged.add(a[: differ[0]] + (None,) + a[differ[0] + 1 :])
        primes |= cubes - used
        cubes = merged
    return primes


def _implications(
    graph: Graph, net: str, value: int
) -> tuple[list[Literal], Choice | None] | None:
    """What the gate driving `net` demands for `net == value`.

    Returns (forced literals, residual choice), or None when no assignment of
    the gate's inputs produces `value` at all. A pin is forced when *every*
    satisfying row agrees on it; the rest become one choice.
    """
    ref = graph.driver_of(net)
    cell = graph.cell_of[ref.instance]
    conns = graph.by_name[ref.instance].connections
    expr = cell.functions[ref.pin]
    names, rows = _table(expr)

    # Only a variable that is both a pin of this cell and connected can carry a
    # requirement back to a net; the rest are existentially quantified away. A
    # pin already tied to a constant is not a variable either -- it narrows the
    # rows instead, which is how `(a & b) | c` with `c` tied low comes out as
    # "a and b are both forced" rather than as an unresolved choice.
    usable: list[tuple[int, str]] = []
    tied: list[tuple[int, int]] = []
    for i, pin in enumerate(names):
        if pin not in cell.inputs or pin not in conns:
            continue
        constant = _fixed(graph, conns[pin])
        if constant is None:
            usable.append((i, conns[pin]))
        else:
            tied.append((i, constant))

    rows = [
        r
        for r in rows
        if all(r[i] == v for i, v in tied)
        and evaluate(expr, dict(zip(names, r))) == value
    ]
    if not rows:
        return None

    forced: list[Literal] = []
    free: list[tuple[int, str]] = []
    for i, wire in usable:
        seen = {r[i] for r in rows}
        if len(seen) == 1:
            forced.append((wire, seen.pop()))
        else:
            free.append((i, wire))

    if not free:
        return forced, None

    # The residue: the satisfying rows projected onto the unforced pins, then
    # reduced to prime implicants so each option is a readable term rather than
    # a minterm. The projection is exact: the forced pins are constant across
    # every row, and anything projected away was quantified over already.
    wires = [wire for _, wire in free]
    cubes = {
        frozenset((wire, v) for wire, v in zip(wires, cube) if v is not None)
        for cube in _primes(rows, [i for i, _ in free])
    }
    if frozenset() in cubes:
        # Every assignment of the free pins works, so they carry no requirement.
        # An empty option would be true.
        return forced, None
    return forced, Choice(net, value, tuple(sorted(cubes, key=sorted)))


def requirements(graph: Graph, net: str, value: int = 1) -> Requirements:
    """What must hold upstream for `net` to take `value`.

    The walk stops at flops, constants and primary inputs. Past a flop you are
    in the previous clock cycle, which is a different question from "what
    computes this value now".

    An unachievable target is reported, not raised: `consistent` is False and
    `conflicts` names the nets that were implied both ways.
    """
    forced: dict[str, int] = {}
    labels: dict[str, str] = {}
    conflicts: list[str] = []
    choices: list[Choice] = []
    pending: list[Literal] = [(net, value)]

    def note_conflict(name: str) -> None:
        if name not in conflicts:
            conflicts.append(name)

    def drain() -> None:
        while pending:
            current, want = pending.pop()
            if current in forced:
                # Already expanded. Reaching it again at the other value means
                # the target needs this net to be both things at once.
                if forced[current] != want:
                    note_conflict(current)
                continue
            forced[current] = want

            constant = _fixed(graph, current)
            if constant is not None:
                # A constant is a fact, not a requirement, so it is left out of
                # `labels` -- the same call `Graph.support` makes. It can still
                # contradict the target, and that is worth saying.
                if constant != want:
                    note_conflict(current)
                continue

            if graph.leaf(current) is not None:
                labels[current] = graph.label(current)
                continue

            implied = _implications(graph, current, want)
            if implied is None:
                note_conflict(current)
                continue
            more, residue = implied
            pending.extend(more)
            if residue is not None:
                choices.append(residue)

    drain()

    # Unit propagation over the residue. This never guesses: an option is only
    # dropped when `forced` already contradicts it, and its literals are only
    # promoted when it is the last one left. So every entry in `forced` stays a
    # derivation, and the walk stays linear.
    while True:
        survivors: list[Choice] = []
        progress = False
        for choice in choices:
            live = [
                cube
                for cube in choice.options
                if all(forced.get(w, v) == v for w, v in cube)
            ]
            if any(all(forced.get(w) == v for w, v in cube) for cube in live):
                progress = True  # discharged: something forced already satisfies it
                continue
            if not live:
                note_conflict(choice.net)
                progress = True
                continue
            if len(live) == 1:
                pending.extend(live[0])
                progress = True
                continue
            if len(live) != len(choice.options):
                progress = True
                choice = Choice(choice.net, choice.value, tuple(live))
            survivors.append(choice)
        choices = survivors
        if not progress and not pending:
            break
        drain()

    return Requirements(
        net=net,
        value=value,
        forced=forced,
        choices=tuple(choices),
        conflicts=tuple(conflicts),
        labels=labels,
    )


def _propagate(
    graph: Graph, assignments: dict[str, int]
) -> tuple[dict[str, int], list[str]]:
    """Forward implication to a fixpoint, plus the nets implied two ways.

    A gate's output is implied only when every row of its truth table that is
    consistent with the known inputs agrees on it, so an AND with one input
    known 0 implies its output even though the other input is unknown.

    Flops are opaque: their outputs belong to the previous cycle, so nothing
    propagates through one.
    """
    known = dict(assignments)
    conflicts: list[str] = []

    watchers: dict[str, set[str]] = {}
    combinational: list[str] = []
    for inst in graph.netlist.instances:
        cell = graph.cell_of[inst.name]
        if cell is None or inst.name in graph.seq:
            continue
        combinational.append(inst.name)
        for pin in cell.inputs:
            wire = inst.connections.get(pin)
            if wire is not None:
                watchers.setdefault(wire, set()).add(inst.name)

    queue = list(combinational)
    while queue:
        inst = queue.pop()
        cell = graph.cell_of[inst]
        conns = graph.by_name[inst].connections
        for pin, expr in cell.functions.items():
            out = conns.get(pin)
            if out is None:
                continue
            names, rows = _consistent_rows(expr, cell, conns, known)
            results = {evaluate(expr, dict(zip(names, r))) for r in rows}
            if len(results) != 1:
                continue
            implied = results.pop()
            if out in known:
                if known[out] != implied and out not in conflicts:
                    conflicts.append(out)
                continue
            known[out] = implied
            queue.extend(watchers.get(out, ()))
    return known, conflicts


def forced_by(graph: Graph, assignments: dict[str, int]) -> dict[str, int]:
    """Every net value `assignments` determines, including `assignments` itself.

    Three-valued, with "unknown" spelt as absent from the result. Where an
    assignment contradicts what the circuit implies, the given value is kept:
    ask `conflict` whether that happened.
    """
    return _propagate(graph, assignments)[0]


def conflict(graph: Graph, assignments: dict[str, int]) -> list[str] | None:
    """Nets `assignments` forces two ways, or None if it is self-consistent."""
    return _propagate(graph, assignments)[1] or None

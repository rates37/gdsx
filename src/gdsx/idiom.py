"""Recognise known circuit idioms by what that subcircuit computes"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from itertools import permutations

from .core.graph import Graph
from .functions import is_sequential
from .liberty import evaluate
from .netlist import Netlist

# Widest cut we enumerate. Six inputs is 64 rows and 92k NPN transforms, which
# is affordable per cut but not per cut per design
MAX_CUT = 4

# Cuts kept per net. Priority cuts, as in every technology mapper: keeping all
# of them is exponential.
#
# Kept smallest first. Raising the cut limit from four to five used to find
# fewer adders, not more, because the wider cuts evicted the three-input ones
# that actually matched something.
MAX_CUTS_PER_NET = 24


# truth tables and NPN canonical form


def rotate(table: int, width: int, index: int) -> int:
    """The truth table with input `index` negated"""
    result = 0
    for row in range(1 << width):
        flipped = row ^ (1 << index)
        if (table >> flipped) & 1:
            result |= 1 << row
    return result


def permute(table: int, width: int, order: tuple[int, ...]) -> int:
    """The truth table with inputs reordered by `order`"""
    result = 0
    for row in range(1 << width):
        source = 0
        for position, index in enumerate(order):
            if (row >> position) & 1:
                source |= 1 << index
        if (table >> source) & 1:
            result |= 1 << row
    return result


@lru_cache(maxsize=1 << 16)
def npn(table: int, width: int) -> int:
    """The canonical form of a function under input/output negation and permutation

    Brute force over all `2^k * k! * 2` transforms, keeping the smallest. Exact,
    and at k <= 5 fast enough that caching makes it free in practice.
    """
    if width == 0:
        return table & 1
    mask = (1 << (1 << width)) - 1
    best = None
    for order in permutations(range(width)):
        permuted = permute(table, width, order)
        for negations in range(1 << width):
            candidate = permuted
            for index in range(width):
                if (negations >> index) & 1:
                    candidate = rotate(candidate, width, index)
            for value in (candidate, ~candidate & mask):
                if best is None or value < best:
                    best = value
    return best


def evaluate_cone(nl: Netlist, output: str, leaves: list[str]) -> int | None:
    """The truth table of `output` as a function of `leaves`"""

    graph = Graph(nl)
    # The instances between the leaves and the output. A sequential one in the
    # way means this is not a combinational function of the leaves.
    inside = graph.cone({output}, stop=frozenset(leaves), returns="instances")
    if inside & graph.seq:
        return None
    order = [inst.name for inst in graph.topo(inside)]

    table = 0
    for row in range(1 << len(leaves)):
        values = {leaf: (row >> i) & 1 for i, leaf in enumerate(leaves)}
        values.update({net: 0 for net in nl.power_nets if net.endswith("GND")})
        values.update({net: 1 for net in nl.power_nets if not net.endswith("GND")})
        for name in order:
            inst = graph.by_name[name]
            cell = graph.cell_of[name]
            pins = {
                p: values.get(inst.connections[p], 0)
                for p in cell.inputs
                if p in inst.connections
            }
            for pin, expr in cell.functions.items():
                if pin in inst.connections:
                    values[inst.connections[pin]] = evaluate(expr, pins)
        if values.get(output, 0):
            table |= 1 << row
    return table


# cut enumeration


def cuts(nl: Netlist, limit: int = MAX_CUT) -> dict[str, list[frozenset[str]]]:
    """The k-feasible cuts of every net: which small sets of nets it is a function of"""
    graph = Graph(nl)
    found: dict[str, list[frozenset[str]]] = {}

    def compute(net: str, depth: int = 0) -> list[frozenset[str]]:
        if net in found:
            return found[net]
        trivial = [frozenset({net})]
        ref = graph.driver_of(net)
        inst = graph.by_name[ref.instance] if ref is not None else None
        if inst is None or is_sequential(inst.cell) or depth > 32:
            found[net] = trivial
            return trivial

        cell = graph.cell_of[inst.name]
        inputs = [
            inst.connections[p]
            for p in cell.inputs
            if p in inst.connections and inst.connections[p] not in nl.power_nets
        ]
        found[net] = trivial  # guard against combinational loops
        combined = [frozenset()]
        for source in inputs:
            grown = []
            for prefix in combined:
                for cut in compute(source, depth + 1):
                    union = prefix | cut
                    if len(union) <= limit:
                        grown.append(union)
            combined = sorted(set(grown), key=lambda c: (len(c), sorted(c)))[
                : MAX_CUTS_PER_NET * 4
            ]
            if not combined:
                break

        unique = {frozenset({net}): None}
        for cut in sorted(combined, key=lambda c: (len(c), sorted(c))):
            unique.setdefault(cut, None)
        found[net] = list(unique)[:MAX_CUTS_PER_NET]
        return found[net]

    for net in sorted(nl.nets):
        if net not in nl.power_nets:
            compute(net)
    return found


# the library


def table_of(width: int, function) -> int:
    """Build a truth table from a Python function of `width` bits"""
    table = 0
    for row in range(1 << width):
        bits = [(row >> i) & 1 for i in range(width)]
        if function(*bits):
            table |= 1 << row
    return table


# Functions worth recognising, written as expressions and canonicalised on
# import
DEFINITIONS: list[tuple[str, int, object]] = [
    ("adder carry (majority)", 3, lambda a, b, c: (a + b + c) >= 2),
    ("adder sum / parity", 3, lambda a, b, c: a ^ b ^ c),
    ("mux", 3, lambda a, b, s: b if s else a),
    ("and/or of 3", 3, lambda a, b, c: a & b & c),
    ("one-hot of 3", 3, lambda a, b, c: (a + b + c) == 1),
    ("two-of-three", 3, lambda a, b, c: (a + b + c) == 2),
    ("parity of 4", 4, lambda a, b, c, d: a ^ b ^ c ^ d),
    ("and/or of 4", 4, lambda a, b, c, d: a & b & c & d),
    ("compare bit (a>b)", 4, lambda a, b, g, e: g or (e and a and not b)),
    ("equality of two pairs", 4, lambda a, b, c, d: (a == b) and (c == d)),
    ("select 1 of 4", 4, lambda a, b, c, s: (b if s else a) if c else (a if s else b)),
    ("one-hot of 4", 4, lambda a, b, c, d: (a + b + c + d) == 1),
    ("carry-select", 4, lambda a, b, c, s: (a if s else b) & (c | s)),
]


@lru_cache(maxsize=1)
def library() -> dict[tuple[int, int], str]:
    """(inputs, canonical form) -> name

    Definitions that turn out to share a canonical form share a name joined
    with a slash.
    """
    found: dict[tuple[int, int], str] = {}
    for name, width, function in DEFINITIONS:
        if width > MAX_CUT:
            continue
        key = (width, npn(table_of(width, function), width))
        if key in found and name not in found[key].split(" / "):
            found[key] = f"{found[key]} / {name}"
        else:
            found.setdefault(key, name)
    return found


@dataclass(frozen=True)
class Match:
    net: str
    idiom: str
    leaves: tuple[str, ...]

    @property
    def width(self) -> int:
        return len(self.leaves)


@dataclass
class Matches:
    # the widest match per net (what to report)
    found: list[Match] = field(default_factory=list)
    # every match, including narrower cuts of the same net
    # because a carry bit's link to the previous stage is visible in its
    # three-input cut and hidden in its four-input one
    every: list[Match] = field(default_factory=list)
    considered: int = 0

    def by_idiom(self) -> dict[str, list[Match]]:
        out: dict[str, list[Match]] = {}
        for match in self.found:
            out.setdefault(match.idiom, []).append(match)
        return dict(sorted(out.items(), key=lambda kv: -len(kv[1])))


def match(nl: Netlist, limit: int = MAX_CUT) -> Matches:
    """Every net that computes something the library knows a name for"""
    known = library()
    result = Matches()
    for net, options in sorted(cuts(nl, limit).items()):
        best: Match | None = None
        for cut in sorted(options, key=lambda c: -len(c)):
            if len(cut) < 2 or net in cut:
                continue
            result.considered += 1
            leaves = sorted(cut)
            table = evaluate_cone(nl, net, leaves)
            if table is None:
                continue
            name = known.get((len(leaves), npn(table, len(leaves))))
            if not name:
                continue
            found = Match(net, name, tuple(leaves))
            result.every.append(found)
            if best is None or len(leaves) > best.width:
                best = found
        if best is not None:
            result.found.append(best)
    return result


# composition: idioms that only mean something together


@dataclass
class Chain:
    """A carry chain: full adders whose carry-out feeds the next carry-in"""

    carries: list[str]
    sums: list[str] = field(default_factory=list)

    @property
    def width(self) -> int:
        return len(self.carries)


CARRY = "adder carry (majority)"
SUM = "adder sum / parity"


def carry_chains(matches: Matches) -> list[Chain]:
    """Link adder bits into adders

    A carry bit whose inputs include another carry bit is the next stage up, so
    following that relation gives the adder and its width. No hypothesis about
    what the operands are, and no solver -- which is the whole difference from
    `find_buses`, which needs two known registers and a SAT call per guess.
    """
    carry: dict[str, list[Match]] = {}
    for found in matches.every:
        if found.idiom == CARRY:
            carry.setdefault(found.net, []).append(found)
    sums = {found.net: found for found in matches.every if found.idiom == SUM}

    successor: dict[str, str] = {}
    for net, options in carry.items():
        for found in options:
            for leaf in found.leaves:
                if leaf in carry and leaf != net:
                    successor.setdefault(leaf, net)

    chains = []
    for start in sorted(set(carry) - set(successor.values())):
        run, seen = [start], {start}
        while run[-1] in successor and successor[run[-1]] not in seen:
            run.append(successor[run[-1]])
            seen.add(run[-1])
        if len(run) < 2:
            continue
        chain = Chain(run)
        reachable = {
            leaf for net in run for found in carry[net] for leaf in found.leaves
        }
        chain.sums = sorted(
            net for net, found in sums.items() if set(found.leaves) & reachable
        )
        chains.append(chain)
    return chains


def report(nl: Netlist, matches: Matches, chains: list[Chain]) -> str:
    lines = [
        f"{len(matches.found)} nets match a known function "
        f"({matches.considered} cuts examined, {len(library())} idioms in the library)",
        "",
    ]
    for idiom, found in matches.by_idiom().items():
        sample = ", ".join(m.net for m in found[:6])
        lines.append(
            f"  {len(found):4d} x {idiom:22s} {sample}"
            + (" ..." if len(found) > 6 else "")
        )

    if chains:
        lines += ["", f"{len(chains)} carry chains:"]
        for chain in sorted(chains, key=lambda c: -c.width):
            lines.append(
                f"  {chain.width}-bit adder: carries {' -> '.join(chain.carries[:6])}"
                + (" ..." if chain.width > 6 else "")
            )
    lines += [
        "",
        "  Matched by canonical function, so a resynthesised adder still matches.",
        "  Nothing here is a claim about what the operands mean.",
    ]
    return "\n".join(lines)

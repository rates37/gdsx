from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations, product

from . import idiom
from .analyse import Register, _survey
from .core.graph import Graph
from .functions import data_nets, lookup, output_net
from .liberty import Expr, evaluate, to_verilog
from .netlist import Netlist


@dataclass
class Role:
    """What a register appears to be and why that was inferred"""

    register: str
    kind: str
    evidence: str = ""
    feeds: list[str] = field(default_factory=list)  # registers it drives
    fed_by: list[str] = field(default_factory=list)  # registers driving it

    def __str__(self) -> str:
        return f"{self.register}: {self.kind}" + (
            f" ({self.evidence})" if self.evidence else ""
        )


def graph(nl: Netlist, registers: list[Register]) -> dict[str, set[str]]:
    """register -> the registers its next state depends on

    Built from the flop-level survey, which already walks each flop's data cone
    back to whatever drives it.
    """
    info = _survey(nl)
    owner = {flop: register.name for register in registers for flop in register.flops}
    edges: dict[str, set[str]] = {register.name: set() for register in registers}
    for register in registers:
        for flop in register.flops:
            if flop not in info:
                continue
            edges[register.name] |= {
                owner[source] for source in info[flop].depends if source in owner
            }
    return edges


def _next_state_cone(nl: Netlist, register: Register) -> set[str]:
    """Every net feeding the register's data inputs, back to the flops"""

    by_name = {i.name: i for i in nl.instances}
    direct: set[str] = set()
    for flop in register.flops:
        inst = by_name[flop]
        cell = lookup(inst.cell)
        if cell is not None and cell.is_sequential:
            direct |= data_nets(cell, inst.connections)
    return direct | Graph.of(nl).cone(direct)


def classify(
    nl: Netlist,
    registers: list[Register],
    matches: idiom.Matches | None = None,
    facts=None,
) -> list[Role]:
    """Name what each register is, from its own feedback and what is in the way"""
    matches = matches if matches is not None else idiom.match(nl)
    stated = facts.roles() if facts is not None else {}
    info = _survey(nl)
    edges = graph(nl, registers)
    reverse: dict[str, set[str]] = {register.name: set() for register in registers}
    for name, sources in edges.items():
        for source in sources:
            reverse.setdefault(source, set()).add(name)

    by_net: dict[str, list[idiom.Match]] = {}
    for match in matches.every:
        by_net.setdefault(match.net, []).append(match)
    owner = {flop: register.name for register in registers for flop in register.flops}
    chains = idiom.carry_chains(matches)

    roles = []
    for register in registers:
        name = register.name
        sources = edges[name]
        others = sources - {name}

        if name in stated:
            roles.append(
                Role(
                    register=name,
                    kind=stated[name],
                    evidence="asserted",
                    feeds=sorted(reverse.get(name, set()) - {name}),
                    fed_by=sorted(others),
                )
            )
            continue

        cone = _next_state_cone(nl, register)

        arithmetic, chain = _arithmetic_in(cone, chains, by_net)
        xors = _taps(cone, by_net)

        if name in sources and arithmetic:
            own = _own_nets(nl, register)
            external = bool(others) or (
                chain is not None and _reads_outside(chain, by_net, own, nl.power_nets)
            )
            kind = "accumulator" if external else "counter"
            evidence = f"feedback through {arithmetic}"
        elif name in sources and not others and _counts_up(register, info):
            kind, evidence = "counter", "each bit depends on the ones below it"
        elif (
            name in sources
            and xors
            and register.kind in ("shift register", "feedback register")
        ):
            kind, evidence = "LFSR", f"shift with {xors} parity taps"
        elif name in sources and register.kind == "shift register":
            kind, evidence = "shift register", "bits feed the next"
        elif name in sources:
            kind, evidence = "state register", "depends on itself"
        elif len(others) == 1:
            kind, evidence = "pipeline stage", f"fed only by {next(iter(others))}"
        elif others:
            kind, evidence = "combining register", f"fed by {len(others)} registers"
        else:
            kind, evidence = "input register", "fed from outside"

        roles.append(
            Role(
                register=name,
                kind=kind,
                evidence=evidence,
                feeds=sorted(reverse.get(name, set()) - {name}),
                fed_by=sorted(others),
            )
        )
    _ = owner
    return roles


def _own_nets(nl: Netlist, register: Register) -> set[str]:
    """The nets the register's own flops drive"""

    by_name = {i.name: i for i in nl.instances}
    nets = set()
    for flop in register.flops:
        inst = by_name[flop]
        cell = lookup(inst.cell)
        if cell is not None:
            net = output_net(cell, inst.connections)
            if net:
                nets.add(net)
    return nets


def _reads_outside(chain, by_net, own: set[str], power: set[str]) -> bool:
    """Does the adder read anything but the register and constants"""
    leaves: set[str] = set()
    for net in chain.carries + chain.sums:
        for match in by_net.get(net, ()):
            leaves |= set(match.leaves)
    return bool(leaves - own - power - set(chain.carries) - set(chain.sums))


def _arithmetic_in(cone, chains, by_net) -> str:
    """Describe any adder found in a register's next-state cone"""
    for chain in sorted(chains, key=lambda c: -c.width):
        if cone & (set(chain.sums) | set(chain.carries)):
            return f"a {chain.width}-bit carry chain", chain
    for net in cone:
        for match in by_net.get(net, ()):
            if match.idiom == idiom.CARRY:
                return "a carry bit", None
    return "", None


def _counts_up(register: Register, info) -> bool:
    """True if the bits form a ripple: one depends on none, one on one, and so on"""
    if register.width < 2 or any(flop not in info for flop in register.flops):
        return False
    inside = set(register.flops)

    # A clean ripple: one bit depends on none, one on one, and so on.
    counts = sorted(
        len((info[flop].depends & inside) - {flop}) for flop in register.flops
    )
    if counts == list(range(register.width)):
        return True

    # Or, where the bit order is known, the containment version of the same
    # thing
    if not register.ordered:
        return False
    for index, flop in enumerate(register.flops[1:], start=1):
        if not set(register.flops[:index]) <= (info[flop].depends & inside):
            return False
    return True


def _taps(cone, by_net) -> int:
    """How many nets in the cone compute a parity of several bits"""
    return sum(
        1 for net in cone for match in by_net.get(net, ()) if "parity" in match.idiom
    )


def pipelines(roles: list[Role]) -> list[list[str]]:
    """Runs of registers that only pass data along, with no feedback"""
    stages = {role.register: role for role in roles}
    successors = {name: [f for f in role.feeds] for name, role in stages.items()}

    heads = [
        name
        for name, role in stages.items()
        if role.kind in ("pipeline stage", "input register")
        and not any(stages.get(s, role).kind == "pipeline stage" for s in role.fed_by)
    ]
    found = []
    for head in sorted(heads):
        run, seen = [head], {head}
        while True:
            nxt = [
                s
                for s in successors.get(run[-1], [])
                if stages.get(s)
                and stages[s].kind == "pipeline stage"
                and s not in seen
            ]
            if len(nxt) != 1:
                break
            run.append(nxt[0])
            seen.add(nxt[0])
        if len(run) > 1:
            found.append(run)
    return found


@dataclass
class Sticky:
    """A flop whose D pin latches on its own Q: `D = <condition> | Q`, or the
    AND mirror, `D = <condition> & Q`.

    Detection is exhaustive over the D-driver cell's Liberty truth table, not
    structural pattern matching: `flop` is sticky iff some input pin of the
    cell driving its D net is fed by that same flop's own `Q`, and forcing
    that pin to `polarity` forces the cell's output to `polarity` for every
    setting of the cell's other inputs, possibly with a handful of those
    other pins also pinned down.

    Stickiness alone does not say whether `flop` is a checkpoint (must reach
    `polarity`) or a trap (must avoid it).
    """

    flop: str
    polarity: int  # 1 = latches high, 0 = latches low
    condition: str  # the Verilog expression that sets it


def _fix(expr: Expr, pin: str, value: int) -> Expr:
    """`expr` with every occurrence of `pin` replaced by the constant `value`"""
    kind = expr[0]
    if kind == "var":
        return expr if expr[1] != pin else ("const", value)
    if kind == "const":
        return expr
    if kind == "not":
        return ("not", _fix(expr[1], pin, value))
    return (kind, _fix(expr[1], pin, value), _fix(expr[2], pin, value))


def _simplify(expr: Expr) -> Expr:
    """Fold the constants `_fix` introduces, so `to_verilog` reads as a
    condition over the remaining pins rather than as `(1 & a) | (0 & b)`."""
    kind = expr[0]
    if kind in ("var", "const"):
        return expr
    if kind == "not":
        inner = _simplify(expr[1])
        return ("const", 1 - inner[1]) if inner[0] == "const" else ("not", inner)
    left, right = _simplify(expr[1]), _simplify(expr[2])
    if kind == "and":
        if left == ("const", 0) or right == ("const", 0):
            return ("const", 0)
        if left == ("const", 1):
            return right
        if right == ("const", 1):
            return left
    elif kind == "or":
        if left == ("const", 1) or right == ("const", 1):
            return ("const", 1)
        if left == ("const", 0):
            return right
        if right == ("const", 0):
            return left
    elif kind == "xor":
        if left == ("const", 0):
            return right
        if right == ("const", 0):
            return left
        if left == ("const", 1):
            return ("not", right)
        if right == ("const", 1):
            return ("not", left)
    return (kind, left, right)


def _minimal_forcing_cube(
    expr: Expr, pin: str, target: int, others: list[str]
) -> dict[str, int] | None:
    """The smallest set of literals -- `pin` fixed to `target`, plus as few of
    `others` as necessary -- that forces `expr` to `target` no matter how the
    rest of `others` ends up. None if no such cube exists at all, i.e. `pin`
    never decides the output, however the rest of the cell is pinned down.

    `others` is never pinned down in full: with nothing left free, "every
    setting of what's left" is vacuously true of a single satisfying row, and
    almost any pin can be made to look decisive that way. At least one other
    input must stay free for the cube to mean anything.

    Brute force: cube sizes smallest first, so the first cube found is
    minimal, over at most 3**4 partial assignments of `others` (unset, 0, or
    1 each), cheap, since a standard cell has at most five inputs.
    """
    for size in range(len(others)):
        for chosen in combinations(others, size):
            for signs in product((0, 1), repeat=size):
                fixed = {pin: target, **dict(zip(chosen, signs))}
                free = [p for p in others if p not in chosen]
                if all(
                    evaluate(expr, {**fixed, **dict(zip(free, bits))}) == target
                    for bits in product((0, 1), repeat=len(free))
                ):
                    return fixed
    return None


def sticky(graph: Graph) -> list[Sticky]:
    """Every flop whose D pin latches on its own Q, high or low.

    For each flop, finds the cell driving its D net and tests each of that
    cell's input pins that is fed only by the flop's own Q (through
    combinational logic, stopping at the next flop) with
    `_minimal_forcing_cube`. A pin that forces the output high makes
    `D = condition | Q` (`condition` being the cell's function with the
    feedback pin fixed at 0); one that forces the output low makes
    `D = condition & Q`. At most one pin per cell is the feedback pin in
    practice, so the first hit per flop wins.
    """
    found: list[Sticky] = []
    for flop in sorted(graph.seq):
        d_net = graph.d_pin(flop)
        if d_net is None:
            continue
        ref = graph.driver_of(d_net)
        if ref is None or ref.instance in graph.seq:
            continue
        cell = graph.cell_of[ref.instance]
        if cell is None:
            continue
        expr = cell.functions.get(ref.pin)
        if expr is None:
            continue
        conns = graph.by_name[ref.instance].connections

        for pin in cell.inputs:
            net = conns.get(pin)
            if net is None or graph.support(net) != {flop}:
                continue
            others = [p for p in cell.inputs if p != pin]
            if _minimal_forcing_cube(expr, pin, 1, others) is not None:
                found.append(Sticky(flop, 1, to_verilog(_simplify(_fix(expr, pin, 0)))))
                break
            if _minimal_forcing_cube(expr, pin, 0, others) is not None:
                found.append(Sticky(flop, 0, to_verilog(_simplify(_fix(expr, pin, 1)))))
                break
    return found

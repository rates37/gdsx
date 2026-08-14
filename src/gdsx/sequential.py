from __future__ import annotations

from dataclasses import dataclass, field

from . import idiom
from .analyse import cone_nets, Register, _survey
from .functions import data_nets, is_sequential, lookup, output_net
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
    return direct | cone_nets(nl, direct, set())


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


def report(nl: Netlist, roles: list[Role], runs: list[list[str]]) -> str:
    flops = sum(1 for i in nl.instances if is_sequential(i.cell))
    lines = [f"{len(roles)} registers over {flops} flops", ""]

    by_kind: dict[str, list[Role]] = {}
    for role in roles:
        by_kind.setdefault(role.kind, []).append(role)
    for kind, found in sorted(by_kind.items(), key=lambda kv: -len(kv[1])):
        lines.append(f"  {len(found):3d} x {kind}")
        for role in found[:6]:
            arrow = ""
            if role.fed_by:
                arrow = f"  <- {', '.join(role.fed_by[:3])}"
            lines.append(f"        {role.register}{arrow}   [{role.evidence}]")
        if len(found) > 6:
            lines.append(f"        ... and {len(found) - 6} more")

    if runs:
        lines += ["", f"{len(runs)} pipelines:"]
        for run in runs:
            lines.append("  " + " -> ".join(run))

    lines += [
        "",
        "  Structural: what feeds what, and what is in the way. Nothing here has",
        "  been proven, and a register that does something the idiom library has",
        "  no name for is reported by its topology alone.",
    ]
    return "\n".join(lines)

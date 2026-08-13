"""What has to be true for a register to change: enable recovery by cofactor

This is `normalise` used as a cofactor engine. The cost is one netlist simplification
per candidate, not per flop, because a single cofactor answers the question for
every flop at once.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .functions import data_nets, lookup, output_net
from .netlist import Netlist
from .normalise import normalise

# Candidate guards are broadcast signals. An enable that reaches one flop is
# possible but rare, and testing every net costs a cofactor each
MIN_FANOUT = 4


@dataclass
class Guard:
    """`flop` holds its value whenever `net` is `value`"""
    flop: str
    net: str
    value: int

    @property
    def condition(self) -> str:
        return f"{self.net}" if self.value == 0 else f"!{self.net}"

    def __str__(self) -> str:
        return f"{self.flop} frozen when {self.net}={self.value}"


@dataclass
class Guards:
    guards: list[Guard] = field(default_factory=list)
    candidates: list[str] = field(default_factory=list)
    flops: list[str] = field(default_factory=list)

    def of(self, flop: str) -> list[Guard]:
        return [g for g in self.guards if g.flop == flop]

    @property
    def ungated(self) -> list[str]:
        held = {g.flop for g in self.guards}
        return [f for f in self.flops if f not in held]

    def groups(self) -> dict[tuple[tuple[str, int], ...], list[str]]:
        """Flops sharing an identical set of freeze conditions"""
        out: dict[tuple[tuple[str, int], ...], list[str]] = defaultdict(list)
        for flop in self.flops:
            key = tuple(sorted((g.net, g.value) for g in self.of(flop)))
            out[key].append(flop)
        return {k: sorted(v) for k, v in out.items()}

    def report(self) -> str:
        lines = [
            f"{len(self.candidates)} candidate control nets tested against {len(self.flops)} flops",
            f"{len({g.flop for g in self.guards})} flops have a recovered freeze condition, "
            f"{len(self.ungated)} do not",
            "",
            "GROUPS  (flops that are enabled together)",
        ]
        for key, members in sorted(
            self.groups().items(), key=lambda kv: (-len(kv[1]), kv[0])
        ):
            cond = ", ".join(f"{net}={value}" for net, value in key) or "(never frozen)"
            lines.append(f"  {len(members):3d} flops frozen when {cond}")
            for chunk in range(0, len(members), 6):
                lines.append("        " + ", ".join(members[chunk : chunk + 6]))
        return "\n".join(lines)


def _state(nl: Netlist) -> dict[str, tuple[set[str], str]]:
    """flop name -> (nets feeding its next value, net carrying its value)"""
    out: dict[str, tuple[set[str], str]] = {}
    for inst in nl.instances:
        cell = lookup(inst.cell)
        if cell is None or not cell.is_sequential:
            continue
        q = output_net(cell, inst.connections)
        d = data_nets(cell, inst.connections)
        if q is not None and d:
            out[inst.name] = (d, q)
    return out


def candidates(nl: Netlist, min_fanout: int = MIN_FANOUT) -> list[str]:
    """Nets worth testing: the ports, and anything broadcast widely enough"""
    readers: dict[str, int] = defaultdict(int)
    for inst in nl.instances:
        cell = lookup(inst.cell)
        outputs = set(cell.outputs) if cell is not None else set()
        for pin, net in inst.connections.items():
            if pin not in outputs and net not in nl.power_nets:
                readers[net] += 1

    ports = {p for p, d in nl.ports.items() if d == "input"}
    wide = {net for net, n in readers.items() if n >= min_fanout}
    return sorted((ports | wide) - nl.power_nets)


def find(
    nl: Netlist, *, min_fanout: int = MIN_FANOUT, nets: list[str] | None = None
) -> Guards:
    """For every flop, the conditions under which its data input becomes its own output"""
    state = _state(nl)
    tested = nets if nets is not None else candidates(nl, min_fanout)
    result = Guards(candidates=list(tested), flops=sorted(state))

    # A flop already wired D-to-Q would match every assumption and mean nothing.
    baseline = normalise(nl, clean=False).merges

    def settle(alias: dict[str, str], net: str) -> str:
        seen: set[str] = set()
        while net in alias and net not in seen:
            seen.add(net)
            net = alias[net]
        return net

    state = {
        flop: (d, q)
        for flop, (d, q) in state.items()
        if not all(settle(baseline, n) == settle(baseline, q) for n in d)
    }

    for net in tested:
        for value in (0, 1):
            alias = normalise(nl, seed={net: value}, clean=False).merges
            for flop, (d_nets, q) in state.items():
                target = settle(alias, q)
                if all(settle(alias, d) == target for d in d_nets):
                    result.guards.append(Guard(flop, net, value))
    return result

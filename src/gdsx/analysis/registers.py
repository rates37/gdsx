"""Register recovery: group flip-flops that act as a unit.

Flops sharing a control signature (cell type, clock, async reset) are
candidates for one register; splitting each control group by data flow
separates registers that just share a clock.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..core.graph import Graph
from ..functions import (
    async_nets,
    base_name,
    clock_nets,
    data_nets,
    is_sequential,
    lookup,
)
from ..netlist import Netlist

if TYPE_CHECKING:  # types only, so these are not a runtime dependency
    from .datapath import Bus
    from .solve import Predicate


@dataclass
class Register:
    """A group of flip-flops that act as a unit.

    `flops` is in bit order, LSB first

    `ordered` says whether that bit order is real: a shift chain or a carry
    chain gives each flop a distinct depth in the group's dependency graph, but
    a parallel-load register's bits are order indistinguishable
    """

    name: str
    flops: list[str]
    serial_input: str | None = None
    kind: str = "register"
    ordered: bool = True
    # where the bit order came from: "topology" (depth in the group's own
    # dependency graph), "probe" (recovered by one-hot probing, then checked),
    # or "" when there is no order to justify
    order_evidence: str = "topology"

    @property
    def width(self) -> int:
        return len(self.flops)


def describe(register: Register) -> str:
    order = "" if register.ordered else ", bit order unknown"
    return f"{register.width}-bit {register.kind}{order}"


@dataclass
class Block:
    """A named piece of the design, with the gates that make it up"""

    name: str
    description: str
    instances: set[str] = field(default_factory=set)


@dataclass
class Analysis:
    registers: list[Register] = field(default_factory=list)
    blocks: list[Block] = field(default_factory=list)
    operators: list[str] = field(default_factory=list)
    predicates: list[Predicate] = field(default_factory=list)
    buses: list[Bus] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class _FlopInfo:
    signature: tuple  # what controls this flop: cell type, clock, async nets
    depends: set[str]  # FFs directly in its next-state cone
    ports: set[str]  # top-level ports in its next-state cone


def _roots(graph: Graph, nets: set[str]) -> frozenset:
    """What ultimately drives these nets: ports, or the flops behind them"""
    return frozenset().union(*(graph.support(n) for n in nets)) if nets else frozenset()


def _survey(nl: Netlist) -> dict[str, _FlopInfo]:
    graph = Graph.of(nl)
    flops = [i for i in nl.instances if is_sequential(i.cell)]
    names = {f.name for f in flops}
    info = {}
    for f in flops:
        cell = lookup(f.cell)
        # next_state may involve several pins,
        # so the data cone is the union over all of them
        deps = set().union(
            *(graph.support(net) for net in data_nets(cell, f.connections))
        )
        # Signature on what drives the clock and reset, not on the net itself.
        # A buffered clock tree gives every few flops their own clock net, which
        # would otherwise split one register into a group per buffer
        info[f.name] = _FlopInfo(
            signature=(
                base_name(f.cell),
                _roots(graph, clock_nets(cell, f.connections)),
                _roots(graph, async_nets(cell, f.connections)),
            ),
            depends=deps & names,
            ports={d for d in deps if d in nl.ports},
        )
    return info


def _components(members: list[str], info: dict[str, _FlopInfo]) -> list[list[str]]:
    """Split FFs that share control into groups that actually talk to each other"""
    inside = set(members)
    adjacency = {m: (info[m].depends & inside) - {m} for m in members}
    for m, linked in list(adjacency.items()):
        for other in linked:
            adjacency[other] = adjacency[other] | {m}  # treat as undirected

    seen: set[str] = set()
    groups = []
    for start in sorted(members):
        if start in seen:
            continue
        stack, group = [start], []
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            group.append(node)
            stack.extend(sorted(adjacency[node] - seen))
        groups.append(sorted(group))
    return groups


def _transitive_depth(group: list[str], info: dict[str, _FlopInfo]) -> dict[str, int]:
    """How many other flops of the group each flop transitively depends on"""
    inside = set(group)
    reach: dict[str, set[str]] = {}

    def walk(node: str, path: frozenset) -> set[str]:
        if node in reach:
            return reach[node]
        if node in path:
            return set()  # a cycle: counters depend on themselves
        out = set()
        for dep in sorted(info[node].depends & inside):
            out.add(dep)
            out |= walk(dep, path | {node})
        reach[node] = out
        return out

    return {f: len(walk(f, frozenset()) - {f}) for f in group}


def _classify(group: list[str], info: dict[str, _FlopInfo]) -> str:
    inside = set(group)
    forward = {f: (info[f].depends & inside) - {f} for f in group}
    if len(group) > 1 and not any(forward.values()):
        return "parallel register"  # bits never talk, only shared control links them
    if all(len(v) <= 1 for v in forward.values()):
        return "shift register"
    if any(f in info[f].depends for f in group):
        return "feedback register"  # counter, accumulator, LFSR
    return "register"


def _group_name(
    group: list[str], info: dict[str, _FlopInfo], common: set[str]
) -> str | None:
    """Name a parallel register after the data ports its bits load from"""
    private = [sorted(info[f].ports - common) for f in group]
    if not all(len(p) == 1 for p in private):
        return None
    names = [p[0] for p in private]
    prefix = names[0]
    for name in names[1:]:
        while not name.startswith(prefix):
            prefix = prefix[:-1]
    prefix = prefix.rstrip("_[")
    return f"reg_{prefix}" if prefix else None


def find_registers(nl: Netlist) -> list[Register]:
    """Group flops into registers.

    Flops that share a control signature (same cell type, clock and async
    reset) are candidates for one register. That over-groups (warm up
    design's two shift registers share everything), so each group is
    then split into the parts that actually exchange data
    """
    info = _survey(nl)
    if not info:
        return []

    by_signature: dict[tuple, list[str]] = {}
    for name, flop in info.items():
        by_signature.setdefault(flop.signature, []).append(name)

    # Control ports reach every flop, a data port only one flop sees is that
    # group's serial input
    common = set.intersection(*(f.ports for f in info.values()))

    registers = []
    used_names: set[str] = set()
    for signature in sorted(by_signature, key=str):
        members = by_signature[signature]
        groups = _components(members, info)
        if len(groups) > 1 and all(len(g) == 1 for g in groups):
            # none of these bits feed each other, so shared control is the only evidence there is
            # -> they are one parallel-load register
            groups = [sorted(members)]

        for group in groups:
            depth = _transitive_depth(group, info)
            ordered = len(set(depth.values())) == len(group)
            flops = sorted(group, key=lambda f: (depth[f], f))

            kind = _classify(group, info)
            head = flops[0]
            private = sorted(info[head].ports - common)
            serial = (
                private[0] if len(private) == 1 and kind == "shift register" else None
            )
            name = f"reg_{serial}" if serial else _group_name(group, info, common)
            name = name or f"reg_{head}"
            while name in used_names:  # two groups can want the same data port
                name += "_"
            used_names.add(name)
            registers.append(
                Register(
                    name=name,
                    flops=flops,
                    serial_input=serial,
                    kind=kind,
                    ordered=ordered,
                )
            )
    registers.sort(
        key=lambda r: r.name
    )  # sort to give stable output so testing is deterministic
    return registers

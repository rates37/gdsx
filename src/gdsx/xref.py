"""Common functionality for use by other features/functions"""

from __future__ import annotations

from dataclasses import dataclass, field

from .core.graph import Graph
from .core.netlist import Instance, Netlist, Ref  # noqa: F401  (Ref re-exported)
from .pins import direction_of


@dataclass
class Xref:
    net: str
    drivers: list[Ref] = field(default_factory=list)
    readers: list[Ref] = field(default_factory=list)
    port: str | None = None  # "input"/"output" if the net reaches the boundary

    @property
    def undriven(self) -> bool:
        return not self.drivers and self.port != "input"

    @property
    def multiply_driven(self) -> bool:
        return len(self.drivers) > 1


def refs(nl: Netlist, net: str) -> Xref:
    """Who drives and who reads a net"""
    found = Xref(net=net, port=nl.ports.get(net))
    for inst in nl.instances:
        for pin, connected in inst.connections.items():
            if connected != net:
                continue
            ref = Ref(inst.name, pin, inst.cell, direction_of(inst.cell, pin))
            (found.drivers if ref.direction == "output" else found.readers).append(ref)
    found.drivers.sort(key=lambda r: (r.instance, r.pin))
    found.readers.sort(key=lambda r: (r.instance, r.pin))
    return found


def cone_report(nl: Netlist, net: str, depth: int, through_flops: bool) -> str:
    graph = Graph.of(nl)
    lines = []
    for label, levels in (
        ("upstream", graph.fanin(net, depth, through_flops)),
        ("downstream", graph.fanout(net, depth, through_flops)),
    ):
        if not levels:
            continue
        lines.append(f"  {label}:")
        for i, level in enumerate(levels, 1):
            shown = ", ".join(level[:12]) + (
                f", ... (+{len(level) - 12})" if len(level) > 12 else ""
            )
            lines.append(f"    {i} hop{'s' if i > 1 else ' '}: {shown}")
    if not through_flops:
        lines.append("  (stopping at flops; --through-flops to cross them)")
    return "\n".join(lines)

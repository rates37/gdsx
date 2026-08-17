"""Common functionality for use by other features/functions"""

from __future__ import annotations

import warnings
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


def fanin(nl: Netlist, net: str, depth: int = 3, through_flops: bool = False):
    """Nets upstream of `net`, level by level

    Stops at flops by default: past a flop you are in the previous clock cycle,
    which is a different question from "what used to compute this value".

    Deprecated: use `core.graph.Graph.fanin`.
    """
    warnings.warn(
        "gdsx.xref.fanin is deprecated; use gdsx.core.graph.Graph.fanin",
        DeprecationWarning,
        stacklevel=2,
    )
    return Graph.of(nl).fanin(net, depth, through_flops)


def fanout(nl: Netlist, net: str, depth: int = 3, through_flops: bool = False):
    """Nets downstream of `net`, level by level

    Deprecated: use `core.graph.Graph.fanout`.
    """
    warnings.warn(
        "gdsx.xref.fanout is deprecated; use gdsx.core.graph.Graph.fanout",
        DeprecationWarning,
        stacklevel=2,
    )
    return Graph.of(nl).fanout(net, depth, through_flops)


def between(
    nl: Netlist, sources: set[str], sinks: set[str], through_flops: bool = True
) -> set[str]:
    """Instances on a path from any source net to any sink net

    Deprecated: use `core.graph.Graph.between`.
    """
    warnings.warn(
        "gdsx.xref.between is deprecated; use gdsx.core.graph.Graph.between",
        DeprecationWarning,
        stacklevel=2,
    )
    return Graph.of(nl).between(sources, sinks, through_flops=through_flops)


def sub_netlist(nl: Netlist, instances: set[str], name: str | None = None) -> Netlist:
    """Carve `instances` out as a netlist in their own right

    Deprecated: use `core.graph.Graph.subgraph`.
    """
    warnings.warn(
        "gdsx.xref.sub_netlist is deprecated; use gdsx.core.graph.Graph.subgraph",
        DeprecationWarning,
        stacklevel=2,
    )
    return Graph.of(nl).subgraph(instances, name)


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

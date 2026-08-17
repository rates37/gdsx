"""Common functionality for use by other features/functions"""

from __future__ import annotations

from dataclasses import dataclass, field

from .core.netlist import Instance, Netlist, Ref  # noqa: F401  (Ref re-exported)
from .functions import is_sequential, lookup
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


def _graph(nl: Netlist):
    """(net -> driving instance, instance -> input nets, instance -> output nets)"""
    driven_by: dict[str, str] = {}
    reads: dict[str, set[str]] = {}
    drives: dict[str, set[str]] = {}
    for inst in nl.instances:
        cell = lookup(inst.cell)
        if cell is None:
            continue
        outputs = {inst.connections[p] for p in cell.functions if p in inst.connections}
        inputs = {inst.connections[p] for p in cell.inputs if p in inst.connections}
        for net in outputs:
            driven_by[net] = inst.name
        drives[inst.name] = outputs
        reads[inst.name] = inputs - nl.power_nets
    return driven_by, reads, drives


def fanin(nl: Netlist, net: str, depth: int = 3, through_flops: bool = False):
    """Nets upstream of `net`, level by level

    Stops at flops by default: past a flop you are in the previous clock cycle,
    which is a different question from "what used to compute this value".
    """
    driven_by, reads, _ = _graph(nl)
    by_name = {i.name: i for i in nl.instances}

    levels: list[list[str]] = []
    frontier, seen = {net}, {net}
    for _ in range(depth):
        nxt: set[str] = set()
        for current in frontier:
            source = driven_by.get(current)
            if source is None:
                continue
            if not through_flops and is_sequential(by_name[source].cell):
                continue
            nxt |= reads.get(source, set()) - seen
        if not nxt:
            break
        levels.append(sorted(nxt))
        seen |= nxt
        frontier = nxt
    return levels


def fanout(nl: Netlist, net: str, depth: int = 3, through_flops: bool = False):
    """Nets downstream of `net`, level by level"""
    _, reads, drives = _graph(nl)
    by_name = {i.name: i for i in nl.instances}
    consumers: dict[str, set[str]] = {}
    for name, inputs in reads.items():
        for source in inputs:
            consumers.setdefault(source, set()).add(name)

    levels: list[list[str]] = []
    frontier, seen = {net}, {net}
    for _ in range(depth):
        nxt: set[str] = set()
        for current in frontier:
            for name in consumers.get(current, set()):
                if not through_flops and is_sequential(by_name[name].cell):
                    continue
                nxt |= drives.get(name, set()) - seen
        if not nxt:
            break
        levels.append(sorted(nxt))
        seen |= nxt
        frontier = nxt
    return levels


def between(
    nl: Netlist, sources: set[str], sinks: set[str], through_flops: bool = True
) -> set[str]:
    """Instances on a path from any source net to any sink net

    Forwards from the sources and backwards from the sinks, intersected: an
    instance is on a path only if it is reachable from both ends
    """
    driven_by, reads, drives = _graph(nl)
    sequential = {i.name for i in nl.instances if is_sequential(i.cell)}
    consumers: dict[str, set[str]] = {}
    for name, inputs in reads.items():
        for net in inputs:
            consumers.setdefault(net, set()).add(name)

    def walk(start: set[str], step, onward) -> set[str]:
        found: set[str] = set()
        seen = set(start)
        stack = list(start)
        while stack:
            net = stack.pop()
            for name in step(net):
                if name in found:
                    continue
                found.add(name)
                if not through_flops and name in sequential:
                    continue
                for nxt in onward.get(name, set()) - seen:
                    seen.add(nxt)
                    stack.append(nxt)
        return found

    downstream = walk(sources, lambda net: consumers.get(net, set()), drives)
    upstream = walk(
        sinks, lambda net: {driven_by[net]} if net in driven_by else set(), reads
    )
    return downstream & upstream


def sub_netlist(nl: Netlist, instances: set[str], name: str | None = None) -> Netlist:
    """Carve `instances` out as a netlist in their own right

    Nets the carved logic reads but does not drive become inputs; nets it drives
    that something outside reads (or that were ports) become outputs. The result
    is a `Netlist` like any other, so every other command can use it
    """
    chosen = [i for i in nl.instances if i.name in instances]
    inside = {i.name for i in chosen}

    driven, read = set(), set()
    for inst in chosen:
        cell = lookup(inst.cell)
        if cell is None:
            continue
        driven |= {inst.connections[p] for p in cell.functions if p in inst.connections}
        read |= {inst.connections[p] for p in cell.inputs if p in inst.connections}

    out = Netlist(top=name or f"{nl.top}_slice", power_nets=set(nl.power_nets))
    out.instances = [Instance(i.name, i.cell, dict(i.connections)) for i in chosen]
    for inst in out.instances:
        for pin, net in inst.connections.items():
            out.nets.setdefault(net, []).append(f"{inst.name}/{pin}")
    for net in out.nets:
        out.nets[net].sort()

    outside_readers = {
        net
        for inst in nl.instances
        if inst.name not in inside
        for net in inst.connections.values()
    }
    for net in sorted(out.nets):
        if net in out.power_nets:
            continue
        if net in read - driven:
            out.ports[net] = "input"
        elif net in driven and (
            net in outside_readers or nl.ports.get(net) == "output"
        ):
            out.ports[net] = "output"
    return out


def report(found: Xref) -> str:
    lines = [f"net {found.net}" + (f"  [{found.port} port]" if found.port else "")]
    if found.multiply_driven:
        lines.append("  ** more than one driver **")
    if found.undriven:
        lines.append("  ** no driver **")
    for ref in found.drivers:
        lines.append(f"  {ref}")
    for ref in found.readers:
        lines.append(f"  {ref}")
    if not found.drivers and not found.readers:
        lines.append("  (nothing connects to it)")
    return "\n".join(lines)


def cone_report(nl: Netlist, net: str, depth: int, through_flops: bool) -> str:
    lines = []
    for label, levels in (
        ("upstream", fanin(nl, net, depth, through_flops)),
        ("downstream", fanout(nl, net, depth, through_flops)),
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

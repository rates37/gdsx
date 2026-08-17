"""The traversal API"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ..functions import data_nets, is_sequential, lookup
from ..liberty import Cell, to_verilog
from .netlist import Instance, Netlist, Ref


class CombinationalLoop(ValueError):
    """A topological sort could not finish.

    Subclasses ValueError, and carries the message sim.py has always raised, so
    existing `except ValueError` callers keep working unchanged.
    """

    def __init__(self, instances: list[str]) -> None:
        self.instances = instances  # up to 5 of the instances still blocked
        super().__init__(
            f"combinational loop or undriven input near: {', '.join(instances)}"
        )


class LeafKind(Enum):
    PRIMARY_IN = "primary_in"  # a net in netlist.ports with direction "input"
    CONST0 = "const0"  # a power net whose name ends in "GND"
    CONST1 = "const1"  # any other power net
    FLOP_Q = "flop_q"  # driven by an output pin of a sequential instance
    UNDRIVEN = "undriven"  # no driver and not a port: dangling


@dataclass(frozen=True)
class Leaf:
    kind: LeafKind
    net: str
    instance: str | None = None  # set only for FLOP_Q
    pin: str | None = None  # set only for FLOP_Q


@dataclass
class Graph:
    """The single traversal API. Built once per netlist, cached on Design.

    Every index is built in __post_init__ in ONE pass over instances.
    Nothing in this class rebuilds an index lazily.
    """

    netlist: Netlist

    by_name: dict[str, Instance] = field(init=False)
    cell_of: dict[str, Cell | None] = field(
        init=False
    )  # instance -> Cell (None = fill/tap)
    driver: dict[str, Ref] = field(init=False)  # net -> the ONE pin driving it
    readers: dict[str, list[Ref]] = field(init=False)  # net -> pins reading it
    seq: frozenset[str] = field(init=False)  # sequential instance names

    def __post_init__(self) -> None:
        by_name: dict[str, Instance] = {}
        cell_of: dict[str, Cell | None] = {}
        driver: dict[str, Ref] = {}
        readers: dict[str, list[Ref]] = {}
        seq: set[str] = set()

        for inst in self.netlist.instances:
            by_name[inst.name] = inst
            # The library-level check, not `cell.is_sequential`: it is what every
            # existing call site computes, and it stays true for cells lookup()
            # rejects.
            if is_sequential(inst.cell):
                seq.add(inst.name)
            cell = lookup(inst.cell)
            cell_of[inst.name] = cell
            if cell is None:
                # fill / tap / decap / antenna / tristate. Really present in the
                # netlist, but they drive nothing and read nothing.
                continue
            for pin in cell.functions:  # a pin drives a net if it has a function
                net = inst.connections.get(pin)
                if net is not None:
                    driver[net] = Ref(inst.name, pin, inst.cell, "output")
            for pin in cell.inputs:  # a pin reads a net if it is an input
                net = inst.connections.get(pin)
                if net is not None:
                    readers.setdefault(net, []).append(
                        Ref(inst.name, pin, inst.cell, "input")
                    )

        self.by_name = by_name
        self.cell_of = cell_of
        self.driver = driver
        self.readers = readers
        self.seq = frozenset(seq)

    #! edge helpers
    #
    # `_input_nets`/`_inputs_of` keep power nets; `_reads` drops them. That is
    # not an oversight: the cone walkers handle power at the node (a power net
    # classifies as a constant leaf), while the level-by-level walkers strip it
    # from the edge set so it never appears in a reported level. Collapsing the
    # two changes fanin/fanout output.

    def _input_nets(self, inst: str) -> list[str]:
        """Nets on an instance's connected input pins, power included"""
        cell = self.cell_of.get(inst)
        if cell is None:
            return []
        conns = self.by_name[inst].connections
        return [conns[p] for p in cell.inputs if p in conns]

    def _inputs_of(self, net: str) -> list[str]:
        """Nets feeding the gate that drives `net`. Empty if nothing drives it."""
        ref = self.driver.get(net)
        return self._input_nets(ref.instance) if ref is not None else []

    def _reads(self, inst: str) -> set[str]:
        """Nets an instance reads, power excluded."""
        return set(self._input_nets(inst)) - self.netlist.power_nets

    def _drives(self, inst: str) -> set[str]:
        """Nets an instance drives."""
        cell = self.cell_of.get(inst)
        if cell is None:
            return set()
        conns = self.by_name[inst].connections
        return {conns[p] for p in cell.functions if p in conns}

    def _consumers(self, net: str) -> set[str]:
        """Instances reading `net`. Empty for power nets, matching `_reads`."""
        if net in self.netlist.power_nets:
            return set()
        return {ref.instance for ref in self.readers.get(net, ())}

    #! classification

    def leaf(self, net: str) -> Leaf | None:
        """None means 'driven by combinational logic, keep walking'"""
        if net in self.netlist.power_nets:
            kind = LeafKind.CONST0 if net.endswith("GND") else LeafKind.CONST1
            return Leaf(kind, net)
        ref = self.driver.get(net)
        if ref is None:
            # Nothing drives it. Cells the library does not describe contribute
            # no drivers, so a net hanging off a fill or tap lands here too.
            kind = (
                LeafKind.PRIMARY_IN
                if self.netlist.ports.get(net) == "input"
                else LeafKind.UNDRIVEN
            )
            return Leaf(kind, net)
        if ref.instance in self.seq:
            return Leaf(LeafKind.FLOP_Q, net, ref.instance, ref.pin)
        return None

    def is_leaf(self, net: str) -> bool:
        return self.leaf(net) is not None

    #! traversal

    def support(
        self,
        net: str,
        *,
        stop: frozenset[str] = frozenset(),
        through_flops: bool = False,
    ) -> set[str]:
        """Every leaf reachable upstream of `net`. Stops at flops by default.

        Returns a mix, as the code this replaces did: net names for primary
        inputs and undriven nets, instance names for flops. Constants are not
        dependencies and are left out. A net in `stop` is neither returned nor
        expanded.
        """
        result: set[str] = set()
        seen: set[str] = set()
        stack = [net]
        while stack:
            current = stack.pop()
            if current in seen or current in stop:
                continue
            seen.add(current)
            found = self.leaf(current)
            if found is None:
                stack.extend(self._inputs_of(current))
                continue
            if found.kind in (LeafKind.CONST0, LeafKind.CONST1):
                continue
            if found.kind is LeafKind.FLOP_Q:
                if not through_flops:
                    result.add(found.instance)
                    continue
                stack.extend(self._inputs_of(current))
                continue
            result.add(current)
        return result

    def cone(
        self,
        nets: set[str],
        *,
        stop: frozenset[str] = frozenset(),
        returns: str = "nets",
        depth: int | None = None,
        through_flops: bool = False,
    ) -> set[str]:
        """Every driven net (returns='nets') or instance (returns='instances')
        feeding `nets`, walking back but never through `stop`.

        The seed nets are included when they are driven, and a net in `stop` is
        neither returned nor expanded.
        Undriven nets are not returned: they are leaves, ask `support` for those.
        """
        if returns not in ("nets", "instances"):
            raise ValueError(f"returns must be 'nets' or 'instances', not {returns!r}")

        seen: set[str] = set()
        found: set[str] = set()
        stack = [(net, 0) for net in nets]
        while stack:
            net, level = stack.pop()
            if net in seen or net in stop or net in self.netlist.power_nets:
                continue
            ref = self.driver.get(net)
            if ref is None:
                continue
            seen.add(net)
            found.add(ref.instance)
            if ref.instance in self.seq and not through_flops:
                continue
            if depth is not None and level >= depth:
                continue
            stack.extend((n, level + 1) for n in self._inputs_of(net))
        return found if returns == "instances" else seen

    def fanin(
        self, net: str, depth: int = 3, through_flops: bool = False
    ) -> list[list[str]]:
        """Nets upstream, level by level. Index 0 is the immediate drivers.

        Stops at flops by default: past a flop you are in the previous clock
        cycle, which is a different question from "what computed this value".
        """
        levels: list[list[str]] = []
        frontier, seen = {net}, {net}
        for _ in range(depth):
            nxt: set[str] = set()
            for current in frontier:
                ref = self.driver.get(current)
                if ref is None:
                    continue
                if not through_flops and ref.instance in self.seq:
                    continue
                nxt |= self._reads(ref.instance) - seen
            if not nxt:
                break
            levels.append(sorted(nxt))
            seen |= nxt
            frontier = nxt
        return levels

    def fanout(
        self, net: str, depth: int = 3, through_flops: bool = False
    ) -> list[list[str]]:
        """Nets downstream, level by level."""
        levels: list[list[str]] = []
        frontier, seen = {net}, {net}
        for _ in range(depth):
            nxt: set[str] = set()
            for current in frontier:
                for name in self._consumers(current):
                    if not through_flops and name in self.seq:
                        continue
                    nxt |= self._drives(name) - seen
            if not nxt:
                break
            levels.append(sorted(nxt))
            seen |= nxt
            frontier = nxt
        return levels

    def between(
        self, sources: set[str], sinks: set[str], *, through_flops: bool = True
    ) -> set[str]:
        """Instance names on some path from any source net to any sink net.

        Forwards from the sources and backwards from the sinks, intersected. An
        instance is on a path only if it is reachable from both ends.
        """

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
                    if not through_flops and name in self.seq:
                        continue
                    for nxt in onward(name) - seen:
                        seen.add(nxt)
                        stack.append(nxt)
            return found

        def driving(net: str) -> set[str]:
            ref = self.driver.get(net)
            return {ref.instance} if ref is not None else set()

        downstream = walk(sources, self._consumers, self._drives)
        upstream = walk(sinks, driving, self._reads)
        return downstream & upstream

    def d_support(self, flop: str) -> set[str]:
        """Leaves feeding a flop's data pins.

        Unions over every net the cell's next_state expression mentions, so scan
        flops are covered too, not just the `D` pin.
        """
        cell = self.cell_of.get(flop)
        if cell is None or cell.sequential is None:
            return set()
        result: set[str] = set()
        for net in data_nets(cell, self.by_name[flop].connections):
            result |= self.support(net)
        return result

    def topo(
        self, instances: set[str] | None = None, *, sources: set[str] | None = None
    ) -> list[Instance]:
        """Combinational instances in dependency order, O(V+E)

        Level-batched Kahn: each round emits every instance that has just become
        ready, in netlist order. That reproduces the levelised order the
        simulator has always produced.

        `instances` restricts the sort to a subset; `sources` names the nets
        already known before any of them runs, defaulting to every net the
        chosen instances do not drive. Raises CombinationalLoop naming up to 5
        instances that never became ready.
        """
        chosen = [
            inst
            for inst in self.netlist.instances
            if self.cell_of[inst.name] is not None
            and inst.name not in self.seq
            and (instances is None or inst.name in instances)
        ]
        order = {inst.name: i for i, inst in enumerate(self.netlist.instances)}

        if sources is None:
            driven: set[str] = set()
            for inst in chosen:
                driven |= self._drives(inst.name)
            known = set(self.netlist.nets) - driven
        else:
            known = set(sources)

        missing: dict[str, set[str]] = {}
        blocked_on: dict[str, list[str]] = {}
        level: list[Instance] = []
        for inst in chosen:
            need = {n for n in self._input_nets(inst.name) if n not in known}
            missing[inst.name] = need
            if need:
                for net in need:
                    blocked_on.setdefault(net, []).append(inst.name)
            else:
                level.append(inst)

        ordered: list[Instance] = []
        placed: set[str] = set()
        while level:
            ordered.extend(level)
            fresh: set[str] = set()
            for inst in level:
                placed.add(inst.name)
                for net in self._drives(inst.name):
                    if net not in known:
                        known.add(net)
                        fresh.add(net)
            ready: list[str] = []
            for net in fresh:
                for name in blocked_on.get(net, ()):
                    need = missing[name]
                    need.discard(net)
                    if not need and name not in placed:
                        ready.append(name)
            level = sorted(
                (self.by_name[n] for n in ready), key=lambda i: order[i.name]
            )

        if len(ordered) != len(chosen):
            stuck = [inst.name for inst in chosen if inst.name not in placed][:5]
            raise CombinationalLoop(stuck)
        return ordered

    def subgraph(self, instances: set[str], name: str | None = None) -> Netlist:
        """Carve `instances` out as a netlist in their own right

        Nets the carved logic reads but does not drive become inputs; nets it
        drives that something outside reads (or that were ports) become outputs.
        The result is a `Netlist` like any other, so every other command can use
        it.
        """
        nl = self.netlist
        chosen = [i for i in nl.instances if i.name in instances]
        inside = {i.name for i in chosen}

        driven, read = set(), set()
        for inst in chosen:
            cell = self.cell_of[inst.name]
            if cell is None:
                continue
            driven |= {
                inst.connections[p] for p in cell.functions if p in inst.connections
            }
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

    #! convenience the investigation needed and the library lacked

    def driver_of(self, net: str) -> Ref | None:
        return self.driver.get(net)

    def d_pin(self, flop: str) -> str | None:
        """The net on a flop's D pin. None if unconnected, or if the cell takes
        its next state from more than one net (a scan flop)."""
        cell = self.cell_of.get(flop)
        if cell is None or cell.sequential is None:
            return None
        nets = data_nets(cell, self.by_name[flop].connections)
        return next(iter(nets)) if len(nets) == 1 else None

    def function_of(self, net: str) -> str | None:
        """Liberty function of the gate driving `net`, as a Verilog string."""
        ref = self.driver.get(net)
        if ref is None:
            return None
        cell = self.cell_of[ref.instance]
        expr = cell.functions.get(ref.pin) if cell is not None else None
        return to_verilog(expr) if expr is not None else None

"""Structure-preserving cleanup of an extracted netlist

A netlist that came out of place-and-route carries a lot of logic that says
nothing about functionality, e.g., a massive clock tree, inverter pairs to fix
timing, tie cells feeding gate inputs that were then never optimised away, etc.

The three transforms:

* tie propagation: constants flow forward, and any gate whose output is
  the same for every assignment of its remaining inputs becomes a constant too.
  Done by exhaustive evaluation, which is free at these input counts
* identity collapse: a buffer's output *is* its input, so the two nets
  become one
* inverter pairs: two inversions in series are a buffer

A port net is never merged away, and a net driven by something outside this
netlist is never assumed constant.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product

from .core.graph import Graph
from .functions import lookup
from .netlist import Instance, Netlist

# Above this many unresolved inputs the exhaustive check stops being free.
# No standard cell comes close; the guard is for synthetic and merged cells.
MAX_FOLD_INPUTS = 10


@dataclass
class Normalisation:
    """What changed, in enough detail to justify each removal"""

    netlist: Netlist
    constants: dict[str, int] = field(default_factory=dict)  # net -> 0/1
    folded: list[tuple[str, str, str, int]] = field(default_factory=list)
    buffers: list[tuple[str, str, str, str]] = field(default_factory=list)
    inverter_pairs: list[tuple[str, str, str, str]] = field(default_factory=list)
    degenerate: list[tuple[str, str, str, str]] = field(default_factory=list)
    dangling: list[tuple[str, str]] = field(default_factory=list)
    merges: dict[str, str] = field(default_factory=dict)  # net -> net it became

    @property
    def removed(self) -> int:
        return (
            len(self.folded)
            + len(self.buffers)
            + len(self.inverter_pairs)
            + len(self.degenerate)
            + len(self.dangling)
        )

    def report(self) -> str:
        out = [
            f"{self.removed} cells removed, {len(self.merges)} nets merged",
            f"  {len(self.buffers):4d} buffers collapsed",
            f"  {len(self.inverter_pairs):4d} inverter pairs collapsed",
            f"  {len(self.folded):4d} cells folded to a constant",
            f"  {len(self.degenerate):4d} cells degenerated into a wire",
            f"  {len(self.dangling):4d} cells driving nothing",
            f"  {len(self.constants):4d} nets known constant",
        ]
        if self.buffers:
            out.append("\nbuffers")
            for inst, cell, src, dst in self.buffers:
                out.append(f"  {inst:20s} {cell:10s} {dst} := {src}")
        if self.inverter_pairs:
            out.append("\ninverter pairs")
            for inst, cell, src, dst in self.inverter_pairs:
                out.append(f"  {inst:20s} {cell:10s} {dst} := {src}")
        if self.degenerate:
            out.append("\ndegenerate cells")
            for inst, cell, src, dst in self.degenerate:
                out.append(f"  {inst:20s} {cell:10s} {dst} := {src}")
        if self.folded:
            out.append("\nconstant folds")
            for inst, cell, net, value in self.folded:
                out.append(f"  {inst:20s} {cell:10s} {net} = {value}")
        if self.dangling:
            out.append("\ndriving nothing")
            for inst, cell in self.dangling:
                out.append(f"  {inst:20s} {cell}")
        return "\n".join(out)


class _Aliases:
    """Union-find over net names

    Port names always win/survive, because a port that got merged into an internal net
    would change the interface. Otherwise the driver side wins, so a chain of
    buffers collapses back towards its source rather than towards a leaf.
    """

    def __init__(self, ports: set[str], power: set[str]):
        self.parent: dict[str, str] = {}
        self.pinned = ports | power

    def resolve(self, net: str) -> str:
        seen = []
        while net in self.parent:
            seen.append(net)
            net = self.parent[net]
        for n in seen:
            self.parent[n] = net
        return net

    def merge(self, loser: str, winner: str) -> bool:
        """Point `loser` at `winner`. Refused if `loser` must keep its name."""
        loser, winner = self.resolve(loser), self.resolve(winner)
        if loser == winner or loser in self.pinned:
            return False
        self.parent[loser] = winner
        return True


def _identity(cell) -> tuple[str, str] | None:
    """(output pin, input pin) if this cell just repeats its input"""
    if cell.is_sequential or len(cell.functions) != 1:
        return None
    ((out, expr),) = cell.functions.items()
    return (out, expr[1]) if expr[0] == "var" else None


def _inverting(cell) -> tuple[str, str] | None:
    """(output pin, input pin) if this cell is exactly one inversion."""
    if cell.is_sequential or len(cell.functions) != 1:
        return None
    ((out, expr),) = cell.functions.items()
    if expr[0] == "not" and expr[1][0] == "var":
        return (out, expr[1][1])
    return None


def _simplify(cell, inst: Instance, value_of) -> tuple[dict[str, int], dict[str, str]]:
    """What this cell degenerates to, given what is already known to be constant

    Returns (outputs that are now constant, outputs that are now a copy of one
    input)
    """
    if cell.is_sequential or not cell.has_behaviour:
        return {}, {}
    known: dict[str, int] = {}
    unknown: list[str] = []
    for pin in cell.inputs:
        if pin not in inst.connections:
            return {}, {}  # a floating input: say nothing
        value = value_of(inst.connections[pin])
        if value is None:
            unknown.append(pin)
        else:
            known[pin] = value
    if len(unknown) > MAX_FOLD_INPUTS or (unknown and not known):
        return {}, {}  # nothing is known about this cell, so nothing follows

    rows: list[tuple[dict[str, int], dict[str, int]]] = []
    for bits in product((0, 1), repeat=len(unknown)):
        values = dict(known, **dict(zip(unknown, bits)))
        rows.append((values, cell.evaluate(values)))

    constants: dict[str, int] = {}
    copies: dict[str, str] = {}
    for pin in rows[0][1]:
        seen = {out[pin] for _, out in rows}
        if len(seen) == 1:
            constants[pin] = seen.pop()
            continue
        for candidate in unknown:
            if all(out[pin] == inputs[candidate] for inputs, out in rows):
                copies[pin] = candidate
                break
    return constants, copies


def _tie(nl: Netlist) -> tuple[Instance, str, str] | None:
    """One tie cell to act as the design's constant driver, and its two nets"""
    for inst in nl.instances:
        cell = lookup(inst.cell)
        if cell is None or cell.is_sequential or cell.inputs or not cell.functions:
            continue
        values = {}
        for pin, expr in cell.functions.items():
            if expr[0] != "const" or pin not in inst.connections:
                break
            values[expr[1]] = inst.connections[pin]
        else:
            if 0 in values and 1 in values:
                return inst, values[0], values[1]
    return None


def normalise(
    nl: Netlist,
    *,
    fold: bool = True,
    clean: bool = True,
    seed: dict[str, int] | None = None,
) -> Normalisation:
    """Collapse buffers and inverter pairs, propagate constants, drop dead cells

    `seed` forces nets to a value before folding starts, which turns this into a
    **cofactor**: simplify the circuit under an assumption. Asking what the
    design does when one net is held low is how a guard is found without a
    solver. If holding `g` low makes a flop's D collapse onto its own Q, then
    `g` is that flop's enable, and the collapse is the proof
    """
    ports = set(nl.ports)
    aliases = _Aliases(ports, set(nl.power_nets))
    result = Normalisation(netlist=nl)

    constants: dict[str, int] = {}
    for net in nl.power_nets:
        constants[net] = 0 if "GND" in net.upper() or "VSS" in net.upper() else 1

    anchor = _tie(nl) if fold else None
    canonical: dict[int, str] = {}
    if anchor is not None:
        keeper, zero_net, one_net = anchor
        canonical = {0: zero_net, 1: one_net}
        constants[zero_net], constants[one_net] = 0, 1
        aliases.pinned |= {zero_net, one_net}
    else:
        keeper = None

    for net, value in (seed or {}).items():
        constants[net] = value
        aliases.pinned.add(net)

    def value_of(net: str) -> int | None:
        return constants.get(aliases.resolve(net))

    live = {inst.name: inst for inst in nl.instances}
    removed: set[str] = set()

    changed = True
    while changed:
        changed = False
        graph = Graph.of(Netlist(nl.top, list(live.values())))
        drivers = {
            net: (graph.by_name[ref.instance], ref.pin)
            for net, ref in graph.driver.items()
        }

        for inst in list(live.values()):
            if inst.name in removed:
                continue
            cell = lookup(inst.cell)
            if cell is None or cell.is_sequential:
                continue

            # constants, and cells that degenerated into wires
            if fold and inst is not keeper:
                found, copies = _simplify(cell, inst, value_of)
                driven = [p for p in cell.outputs if p in inst.connections]
                settled: set[str] = set()

                for pin, value in found.items():
                    net = aliases.resolve(inst.connections[pin])
                    if net in ports or net in aliases.pinned:
                        continue
                    # Knowing a net is constant is useful even when there is
                    # nowhere to put the constant. It still lets the cells
                    # downstream simplify, which is all a cofactor needs.
                    if constants.get(net) != value:
                        constants[net] = value
                        changed = True
                    if canonical and aliases.merge(net, canonical[value]):
                        result.folded.append((inst.name, inst.cell, net, value))
                        settled.add(pin)
                        changed = True

                for pin, source in copies.items():
                    dst = aliases.resolve(inst.connections[pin])
                    src = aliases.resolve(inst.connections[source])
                    if aliases.merge(dst, src):
                        result.degenerate.append((inst.name, inst.cell, src, dst))
                        settled.add(pin)
                        changed = True

                # Only gone once every output it drives has somewhere else to come from
                if driven and all(p in settled for p in driven):
                    removed.add(inst.name)
            if inst.name in removed:
                continue

            # buffers
            pair = _identity(cell)
            if pair is not None:
                out_pin, in_pin = pair
                if out_pin in inst.connections and in_pin in inst.connections:
                    dst = aliases.resolve(inst.connections[out_pin])
                    src = aliases.resolve(inst.connections[in_pin])
                    if aliases.merge(dst, src):
                        result.buffers.append((inst.name, inst.cell, src, dst))
                        removed.add(inst.name)
                        changed = True
                        continue

            # inverter pairs
            pair = _inverting(cell)
            if pair is not None:
                out_pin, in_pin = pair
                if out_pin in inst.connections and in_pin in inst.connections:
                    mid = aliases.resolve(inst.connections[in_pin])
                    upstream = drivers.get(mid)
                    if upstream is not None and upstream[0].name not in removed:
                        up_cell = lookup(upstream[0].cell)
                        up_pair = _inverting(up_cell) if up_cell is not None else None
                        if (
                            up_pair is not None
                            and up_pair[1] in upstream[0].connections
                        ):
                            src = aliases.resolve(upstream[0].connections[up_pair[1]])
                            dst = aliases.resolve(inst.connections[out_pin])
                            if aliases.merge(dst, src):
                                result.inverter_pairs.append(
                                    (inst.name, inst.cell, src, dst)
                                )
                                removed.add(inst.name)
                                changed = True
                                continue

        for name in removed:
            live.pop(name, None)

    # rebuild with resolved names
    instances: list[Instance] = []
    for inst in nl.instances:
        if inst.name in removed:
            continue
        instances.append(
            Instance(
                inst.name,
                inst.cell,
                {pin: aliases.resolve(net) for pin, net in inst.connections.items()},
            )
        )

    if clean:
        instances = _drop_dangling(instances, ports, result)

    out = Netlist(
        top=nl.top,
        instances=instances,
        ports=dict(nl.ports),
        power_nets=set(nl.power_nets),
        floating=list(nl.floating),
        conflicts=list(nl.conflicts),
    )
    for inst in out.instances:
        for pin, net in inst.connections.items():
            out.nets.setdefault(net, []).append(f"{inst.name}/{pin}")
    for net in out.nets:
        out.nets[net].sort()

    result.netlist = out
    result.constants = {
        net: value for net, value in constants.items() if net not in nl.power_nets
    }
    result.merges = {net: aliases.resolve(net) for net in aliases.parent}
    return result


def _drop_dangling(
    instances: list[Instance], ports: set[str], result: Normalisation
) -> list[Instance]:
    """Remove cells whose outputs nothing reads, repeatedly"""
    while True:
        read: set[str] = set()
        for inst in instances:
            cell = lookup(inst.cell)
            outputs = set(cell.outputs) if cell is not None else set()
            for pin, net in inst.connections.items():
                if pin not in outputs:
                    read.add(net)

        keep, dropped = [], False
        for inst in instances:
            cell = lookup(inst.cell)
            if cell is None or not cell.outputs:
                keep.append(inst)
                continue
            nets = {inst.connections[p] for p in cell.outputs if p in inst.connections}
            if nets and not (nets & (read | ports)):
                result.dangling.append((inst.name, inst.cell))
                dropped = True
            else:
                keep.append(inst)
        instances = keep
        if not dropped:
            return instances

"""Simulator for the extracted netlist

The clock is an event: `step()` is one rising edge. Flops are
sampled simultaneously, so registers behave.
"""

from __future__ import annotations
from dataclasses import dataclass, field

from .functions import CellFunction, FlopFunction, is_sequential, lookup
from .netlist import Instance, Netlist


class UnsupportedCell(Exception):
    pass


@dataclass
class Simulator:
    netlist: Netlist
    combinational: list[tuple[Instance, CellFunction]] = field(default_factory=list)
    flops: list[tuple[Instance, FlopFunction]] = field(default_factory=list)
    state: dict[str, int] = field(default_factory=dict)  # instance name -> Q
    free_nets: set[str] = field(
        default_factory=set
    )  # undriven: ports and dangling pins

    def __post_init__(self) -> None:
        for inst in self.netlist.instances:
            fn = lookup(inst.cell)
            if fn is None:
                raise UnsupportedCell(
                    f"no function for {inst.cell}; add it to functions.py"
                )
            if is_sequential(inst.cell):
                self.flops.append((inst, fn))
            else:
                self.combinational.append((inst, fn))
        self.combinational = _topological(self.combinational, self._sources())
        self.reset()

    # setup
    def _sources(self) -> set[str]:
        """Nets whose value is known before any combinational gate runs

        Anything undriven counts as a free input and defaults to 0
        """
        driven = {inst.connections[fn.output] for inst, fn in self.combinational}
        driven |= {inst.connections[fn.output] for inst, fn in self.flops}
        self.free_nets = set(self.netlist.nets) - driven - set(self.netlist.power_nets)
        return (
            self.free_nets
            | set(self.netlist.power_nets)
            | {inst.connections[fn.output] for inst, fn in self.flops}
        )

    def reset(self) -> None:
        self.state = {inst.name: 0 for inst, _ in self.flops}

    # evaluation
    def settle(self, inputs: dict[str, int]) -> dict[str, int]:
        """Evaluate all combinational logic for the current inputs and state"""
        values = {net: 0 for net in self.free_nets}
        values.update(
            {net: 0 for net in self.netlist.power_nets if net.endswith("GND")}
        )
        values.update(
            {net: 1 for net in self.netlist.power_nets if not net.endswith("GND")}
        )
        values.update(inputs)
        for inst, fn in self.flops:
            values[inst.connections[fn.output]] = self.state[inst.name]
        for inst, fn in self.combinational:
            pins = {p: values[inst.connections[p]] for p in fn.inputs}
            values[inst.connections[fn.output]] = fn.evaluate(pins)
        return values

    def step(self, inputs: dict[str, int]) -> dict[str, int]:
        """One rising clock edge, returns the settled values after the edge"""
        values = self.settle(inputs)
        nxt = {}
        for inst, fn in self.flops:
            if fn.reset_n and values[inst.connections[fn.reset_n]] == 0:
                nxt[inst.name] = 0
            else:
                nxt[inst.name] = values[inst.connections[fn.data]]
        self.state = nxt
        return self.settle(inputs)

    def run(self, vectors: list[dict[str, int]]) -> list[dict[str, int]]:
        return [self.step(v) for v in vectors]

    def clock_sense(self, inputs: dict[str, int]) -> dict[str, int]:
        """+1 if a flop's CLK follows the given inputs' clock, -1 if inverted"""
        clk_nets = {inst.connections[fn.clock] for inst, fn in self.flops}
        low = self.settle({**inputs, "clk": 0})
        high = self.settle({**inputs, "clk": 1})
        return {net: (1 if high[net] > low[net] else -1) for net in clk_nets}


def _topological(gates, sources: set[str]):
    """Order combinational gates so every gate runs after its drivers"""
    pending = list(gates)
    known = set(sources)
    ordered = []
    while pending:
        ready = [
            g for g in pending if all(g[0].connections[p] in known for p in g[1].inputs)
        ]
        if not ready:
            stuck = ", ".join(f"{i.name}" for i, _ in pending[:5])
            raise ValueError(f"combinational loop or undriven input near: {stuck}")
        for gate in ready:
            known.add(gate[0].connections[gate[1].output])
        ordered += ready
        pending = [g for g in pending if g not in ready]
    return ordered

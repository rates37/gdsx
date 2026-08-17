"""simulator for the extracted netlist

Note `settle` is pure. it evaluates combinational logic for the state you give it,
which lets the analysis force register contents and sweep
"""

from __future__ import annotations
from dataclasses import dataclass, field

from ..core.graph import Graph
from ..functions import is_sequential, lookup
from ..liberty import Cell, evaluate
from ..netlist import Instance, Netlist


class UnsupportedCell(Exception):
    pass


@dataclass
class Simulator:
    netlist: Netlist
    combinational: list[tuple[Instance, Cell]] = field(default_factory=list)
    flops: list[tuple[Instance, Cell]] = field(default_factory=list)
    state: dict[str, int] = field(default_factory=dict)  # instance name -> state var
    free_nets: set[str] = field(
        default_factory=set
    )  # undriven: ports and dangling pins

    def __post_init__(self) -> None:
        for inst in self.netlist.instances:
            cell = lookup(inst.cell)
            if cell is None:
                raise UnsupportedCell(f"the library does not describe {inst.cell} ")
            (self.flops if is_sequential(inst.cell) else self.combinational).append(
                (inst, cell)
            )
        pairs = {inst.name: (inst, cell) for inst, cell in self.combinational}
        order = Graph.of(self.netlist).topo(sources=self._sources())
        self.combinational = [pairs[inst.name] for inst in order]
        self.reset()

    # setup

    def _driven(self) -> set[str]:
        return {
            inst.connections[pin]
            for inst, cell in self.combinational + self.flops
            for pin in cell.functions
            if pin in inst.connections
        }

    def _sources(self) -> set[str]:
        """Nets whose value is known before any combinational gate runs

        Anything undriven counts as a free input and defaults to 0
        """
        driven = self._driven()
        self.free_nets = set(self.netlist.nets) - driven - set(self.netlist.power_nets)
        flop_outputs = {
            inst.connections[pin]
            for inst, cell in self.flops
            for pin in cell.functions
            if pin in inst.connections
        }
        return self.free_nets | set(self.netlist.power_nets) | flop_outputs

    def reset(self) -> None:
        self.state = {inst.name: 0 for inst, _ in self.flops}

    # evaluation

    def _state_values(self, inst: Instance, cell: Cell) -> dict[str, int]:
        """Bind the cell's internal state variables (IQ, IQ_N) for evaluation"""
        held = self.state[inst.name]
        names = cell.sequential.state_vars
        values = {names[0]: held}
        if len(names) > 1:
            values[names[1]] = 1 - held
        return values

    def settle(self, inputs: dict[str, int]) -> dict[str, int]:
        # Evaluate all combinational logic for the current inputs and state
        values = {net: 0 for net in self.free_nets}
        values.update(
            {net: 0 for net in self.netlist.power_nets if net.endswith("GND")}
        )
        values.update(
            {net: 1 for net in self.netlist.power_nets if not net.endswith("GND")}
        )
        values.update(inputs)

        for inst, cell in self.flops:
            bound = self._state_values(inst, cell)
            for pin, expr in cell.functions.items():
                if pin in inst.connections:
                    values[inst.connections[pin]] = evaluate(expr, bound)

        for inst, cell in self.combinational:
            pins = {
                p: values[inst.connections[p]]
                for p in cell.inputs
                if p in inst.connections
            }
            for pin, expr in cell.functions.items():
                if pin in inst.connections:
                    values[inst.connections[pin]] = evaluate(expr, pins)
        return values

    def _async_override(
        self, inst: Instance, cell: Cell, values: dict[str, int]
    ) -> int | None:
        # Async clear/preset wins over the clock whenever it is asserted
        seq = cell.sequential
        pins = {
            p: values[inst.connections[p]] for p in cell.inputs if p in inst.connections
        }
        if seq.clear is not None and evaluate(seq.clear, pins):
            return 0
        if seq.preset is not None and evaluate(seq.preset, pins):
            return 1
        return None

    def step(self, inputs: dict[str, int]) -> dict[str, int]:
        """One clock edge. Returns the settled values afterwards"""
        values = self.settle(inputs)
        nxt = {}
        for inst, cell in self.flops:
            pins = {
                p: values[inst.connections[p]]
                for p in cell.inputs
                if p in inst.connections
            }
            override = self._async_override(inst, cell, values)
            if override is not None:
                nxt[inst.name] = override
            else:
                nxt[inst.name] = evaluate(cell.sequential.next_state, pins)
        self.state = nxt
        return self.settle(inputs)

    def edge(self, before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
        """Apply an input transition, capturing only on a genuine clock edge"""
        was = self.settle(before)
        now = self.settle(after)
        nxt = dict(self.state)
        for inst, cell in self.flops:
            pins_was = {
                p: was[inst.connections[p]]
                for p in cell.inputs
                if p in inst.connections
            }
            pins_now = {
                p: now[inst.connections[p]]
                for p in cell.inputs
                if p in inst.connections
            }
            seq = cell.sequential
            rising = not evaluate(seq.clocked_on, pins_was) and evaluate(
                seq.clocked_on, pins_now
            )
            override = self._async_override(inst, cell, now)
            if override is not None:
                nxt[inst.name] = override
            elif rising:
                nxt[inst.name] = evaluate(seq.next_state, pins_now)
        self.state = nxt
        return self.settle(after)

    def run(self, vectors: list[dict[str, int]]) -> list[dict[str, int]]:
        return [self.step(v) for v in vectors]

    # convenience functions

    def clock_sense(self, inputs: dict[str, int], clock: str = "clk") -> dict[str, int]:
        """+1 if a flop clocks in phase with `clock`, -1 if inverted"""
        low = self.settle({**inputs, clock: 0})
        high = self.settle({**inputs, clock: 1})
        sense = {}
        for inst, cell in self.flops:
            pins_low = {
                p: low[inst.connections[p]]
                for p in cell.inputs
                if p in inst.connections
            }
            pins_high = {
                p: high[inst.connections[p]]
                for p in cell.inputs
                if p in inst.connections
            }
            seq = cell.sequential
            on_high = evaluate(seq.clocked_on, pins_high)
            on_low = evaluate(seq.clocked_on, pins_low)
            sense[inst.name] = 1 if on_high > on_low else -1
        return sense

"""simulator for the extracted netlist

Note `settle` is pure. it evaluates combinational logic for the state you give it,
which lets the analysis force register contents and sweep
"""

from __future__ import annotations
from dataclasses import dataclass, field

from ..core.graph import Graph
from ..functions import is_sequential, lookup
from ..liberty import Cell, Expr, evaluate
from ..netlist import Instance, Netlist


class UnsupportedCell(Exception):
    pass


@dataclass(frozen=True)
class _Plan:
    """One instance with its pin->net lookups already done.

    `inst.connections[p]` is a dict hit per pin per gate per cycle, and the
    answer never changes, so it is resolved once here instead. Unconnected pins
    are absent from both tuples, which is what the `if p in inst.connections`
    guards used to decide per cycle.

    The pairs are stored zipped rather than as two parallel tuples on purpose:
    `zip(a, b)` allocates an iterator, and at 636 gates times two settles per
    cycle that allocation costs more than the lookups the plan removes.
    """

    inst: Instance
    cell: Cell
    reads: tuple[tuple[str, str], ...]  # (input pin, the net on it)
    writes: tuple[tuple[str, Expr], ...]  # (net driven, the expression driving it)

    @classmethod
    def of(cls, inst: Instance, cell: Cell) -> "_Plan":
        conns = inst.connections
        return cls(
            inst,
            cell,
            tuple((p, conns[p]) for p in cell.inputs if p in conns),
            tuple((conns[p], e) for p, e in cell.functions.items() if p in conns),
        )


@dataclass
class Simulator:
    netlist: Netlist
    combinational: list[tuple[Instance, Cell]] = field(default_factory=list)
    flops: list[tuple[Instance, Cell]] = field(default_factory=list)
    state: dict[str, int] = field(default_factory=dict)  # instance name -> state var
    free_nets: set[str] = field(
        default_factory=set
    )  # undriven: ports and dangling pins

    # Pre-resolved views of the two lists above, in the same order. Derived, so
    # not constructor arguments. `combinational` and `flops` stay the pairs
    # every caller outside this module already reads.
    comb_plan: list[_Plan] = field(init=False, default_factory=list, repr=False)
    flop_plan: list[_Plan] = field(init=False, default_factory=list, repr=False)

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
        self.comb_plan = [_Plan.of(inst, cell) for inst, cell in self.combinational]
        self.flop_plan = [_Plan.of(inst, cell) for inst, cell in self.flops]
        self.reset()

    @staticmethod
    def _bind(plan: _Plan, values: dict[str, int]) -> dict[str, int]:
        """This instance's input pin values, read straight off the plan"""
        return {pin: values[net] for pin, net in plan.reads}

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

        for plan in self.flop_plan:
            bound = self._state_values(plan.inst, plan.cell)
            for net, expr in plan.writes:
                values[net] = evaluate(expr, bound)

        # The hot loop of the whole library. `_bind` is inlined here, and only
        # here, because at 636 gates times two settles per cycle the call frame
        # is measurable.
        for plan in self.comb_plan:
            pins = {pin: values[net] for pin, net in plan.reads}
            for net, expr in plan.writes:
                values[net] = evaluate(expr, pins)
        return values

    def _async_override(self, pins: dict[str, int], cell: Cell) -> int | None:
        # Async clear/preset wins over the clock whenever it is asserted
        seq = cell.sequential
        if seq.clear is not None and evaluate(seq.clear, pins):
            return 0
        if seq.preset is not None and evaluate(seq.preset, pins):
            return 1
        return None

    def step(self, inputs: dict[str, int]) -> dict[str, int]:
        """One clock edge. Returns the settled values afterwards"""
        values = self.settle(inputs)
        nxt = {}
        for plan in self.flop_plan:
            pins = self._bind(plan, values)
            override = self._async_override(pins, plan.cell)
            if override is not None:
                nxt[plan.inst.name] = override
            else:
                nxt[plan.inst.name] = evaluate(plan.cell.sequential.next_state, pins)
        self.state = nxt
        return self.settle(inputs)

    def edge(self, before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
        """Apply an input transition, capturing only on a genuine clock edge"""
        was = self.settle(before)
        now = self.settle(after)
        nxt = dict(self.state)
        for plan in self.flop_plan:
            pins_was = self._bind(plan, was)
            pins_now = self._bind(plan, now)
            seq = plan.cell.sequential
            rising = not evaluate(seq.clocked_on, pins_was) and evaluate(
                seq.clocked_on, pins_now
            )
            override = self._async_override(pins_now, plan.cell)
            if override is not None:
                nxt[plan.inst.name] = override
            elif rising:
                nxt[plan.inst.name] = evaluate(seq.next_state, pins_now)
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
        for plan in self.flop_plan:
            seq = plan.cell.sequential
            on_high = evaluate(seq.clocked_on, self._bind(plan, high))
            on_low = evaluate(seq.clocked_on, self._bind(plan, low))
            sense[plan.inst.name] = 1 if on_high > on_low else -1
        return sense

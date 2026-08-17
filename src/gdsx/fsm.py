"""Recover state machines from a register's reachable states"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product

from .analyse import Register, load_state, read_state
from .core.graph import Graph
from .functions import data_nets, lookup
from .netlist import Netlist
from .sim import Simulator

MAX_STATES = 256  # give up on anything that is likely a datapath
MAX_INPUTS = 8  # 2^8 input combinations per state


@dataclass
class StateMachine:
    register: str
    width: int
    inputs: list[str]  # the ports that affect transitions
    reset_state: int
    states: list[int] = field(default_factory=list)  # reachable, reset first
    #: (state, input combination as an int over `inputs`) -> next state
    transitions: dict[tuple[int, int], int] = field(default_factory=dict)
    #: state -> {output port: value}, only outputs constant throughout the state
    moore_outputs: dict[int, dict[str, int]] = field(default_factory=dict)

    @property
    def density(self) -> float:
        """Fraction of the encoding space actually used"""
        return len(self.states) / (1 << self.width)

    def successors(self, state: int) -> set[int]:
        return {v for (s, _), v in self.transitions.items() if s == state}


def reset_state(nl: Netlist, register: Register) -> int:
    """The state the flops take when their async clear/preset asserts"""
    by_name = {i.name: i for i in nl.instances}
    value = 0
    for bit, flop in enumerate(register.flops):
        cell = lookup(by_name[flop].cell)
        seq = cell.sequential if cell else None
        if seq is not None and seq.clear is None and seq.preset is not None:
            value |= 1 << bit
    return value


def _resolve(expr, connections: dict[str, str]) -> tuple[str, bool] | None:
    """A one-signal condition as (net, active_low)"""
    if expr[0] == "var" and expr[1] in connections:
        return connections[expr[1]], False
    if expr[0] == "not" and expr[1][0] == "var" and expr[1][1] in connections:
        return connections[expr[1][1]], True
    return None


def quiescent(nl: Netlist, register: Register) -> dict[str, int]:
    """Values that hold the async clear and preset de-asserted"""
    by_name = {i.name: i for i in nl.instances}
    held: dict[str, int] = {}
    for flop in register.flops:
        inst = by_name[flop]
        cell = lookup(inst.cell)
        if cell is None or not cell.is_sequential:
            continue
        for expr in (cell.sequential.clear, cell.sequential.preset):
            if expr is None:
                continue
            resolved = _resolve(expr, inst.connections)
            if resolved is not None:
                net, active_low = resolved
                held[net] = 1 if active_low else 0
    return held


def _relevant_inputs(nl: Netlist, register: Register, exclude: set[str]) -> list[str]:
    """Input ports that appear in the register's own next-state logic

    Reset lines are excluded: asserting one just returns to the reset state,
    which is not a transition worth investigating
    """
    by_name = {i.name: i for i in nl.instances}

    graph = Graph.of(nl)
    seen: set[str] = set()
    for flop in register.flops:
        inst = by_name[flop]
        for net in data_nets(lookup(inst.cell), inst.connections):
            seen |= {d for d in graph.support(net) if d in nl.ports}
    return sorted(p for p in seen - exclude if nl.ports[p] == "input")


def explore(
    nl: Netlist,
    register: Register,
    simulator: Simulator | None = None,
    max_states: int = MAX_STATES,
) -> StateMachine | None:
    """BFS of a register's reachable states"""
    if register.width > 16:
        return None
    held = quiescent(nl, register)
    inputs = _relevant_inputs(nl, register, set(held))
    if len(inputs) > MAX_INPUTS:
        return None

    simulator = simulator or Simulator(nl)
    start = reset_state(nl, register)
    machine = StateMachine(register.name, register.width, inputs, start)

    combos = list(product((0, 1), repeat=len(inputs)))
    queue = [start]
    seen = {start}
    while queue:
        state = queue.pop(0)
        machine.states.append(state)
        constant: dict[str, int] | None = None

        for index, combo in enumerate(combos):
            vector = {**held, **dict(zip(inputs, combo))}
            simulator.reset()
            load_state(simulator, register, state)
            settled = simulator.settle(vector)

            values = {
                p: settled[p]
                for p, d in nl.ports.items()
                if d == "output" and p in settled
            }
            constant = (
                values
                if constant is None
                else {p: v for p, v in constant.items() if values.get(p) == v}
            )

            simulator.reset()
            load_state(simulator, register, state)
            simulator.step(vector)
            nxt = read_state(simulator, register)
            machine.transitions[(state, index)] = nxt
            if nxt not in seen:
                if len(seen) >= max_states:
                    return None  # too many states: this is probably data, not control
                seen.add(nxt)
                queue.append(nxt)

        machine.moore_outputs[state] = constant or {}
    return machine


def find_state_machines(
    nl: Netlist, registers: list[Register], density: float = 0.75
) -> list[StateMachine]:
    """Registers whose reachable states are sparse enough to be control logic"""
    simulator = Simulator(nl)
    found = []
    for register in registers:
        machine = explore(nl, register, simulator)
        if machine is None or len(machine.states) < 2:
            continue
        if machine.density < density:
            found.append(machine)
    return found



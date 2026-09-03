"""Solving: find input sequences that assert a given output.

Sweeps the register state space (or, for `solve`, a shift-loadable subset of
it) looking for a word-level relation, or a satisfying assignment.
"""

from __future__ import annotations
from dataclasses import dataclass
from itertools import product

from ..netlist import Netlist
from ..sim import Simulator
from ..sim.state import find_shift_mode, load_state, read_state
from .registers import Register, find_registers


def sweep(
    simulator: Simulator, registers: list[Register], output: str, inputs: dict[str, int]
):
    """Every combination of register values -> the output bit it produces"""
    widths = [r.width for r in registers]
    for values in product(*(range(1 << w) for w in widths)):
        for reg, value in zip(registers, values):
            load_state(simulator, reg, value)
        yield values, simulator.settle(inputs)[output]


@dataclass
class Predicate:
    """A recognised relation between the registers and a one-bit output"""

    output: str
    operator: str  # which OPERATORS entry the comparison is over, if any
    constant: int | None  # the value compared against, for "== k" relations
    text: str

    def verilog(self, bus_name: str, width: int) -> str | None:
        """The RTL form given the bus that already carries the operator result"""
        if self.constant is None:
            return None
        return f"({bus_name} == {width}'d{self.constant})"


def identify(
    simulator: Simulator, registers: list[Register], output: str, inputs: dict[str, int]
):
    """Name the function of `output` over the register contents if possible"""
    if sum(r.width for r in registers) > 20:
        return None, "input space too large to sweep exhaustively"

    truth = dict(sweep(simulator, registers, output, inputs))
    hits = {k for k, v in truth.items() if v}
    if not hits:
        return None, f"{output} is never asserted"

    left, right = (r.name for r in registers) if len(registers) == 2 else ("", "")
    if len(registers) == 2:
        for operator, symbol, combine in (
            ("sum", "+", lambda a, b: a + b),
            ("difference", "-", lambda a, b: a - b),
        ):
            values = {combine(a, b) for a, b in hits}
            if len(values) != 1:
                continue
            k = values.pop()
            if hits == {(a, b) for a, b in truth if combine(a, b) == k}:
                return (
                    Predicate(
                        output,
                        operator,
                        k,
                        f"{output} = ({left} {symbol} {right} == {k})",
                    ),
                    None,
                )
        for relation, symbol in (
            (lambda a, b: a > b, ">"),
            (lambda a, b: a == b, "=="),
        ):
            if hits == {(a, b) for a, b in truth if relation(a, b)}:
                return Predicate(
                    output, "", None, f"{output} = ({left} {symbol} {right})"
                ), None

    return (
        None,
        f"{output} asserted for {len(hits)} of {len(truth)} states. no known operator matches",
    )


def _load(simulator, nl: Netlist, registers, values, mode) -> list[int]:
    """Shift `values` in serially, then read the registers back

    TODO(dedupe): duplicated from gdsx.sim.state._load. Both are private
    helpers of their own module, per R3's rule for a helper two concerns
    share; the real fix is a shared public primitive, not attempted here.
    """
    simulator.reset()
    width = max(r.width for r in registers)
    order = range(width - 1, -1, -1) if mode.msb_first else range(width)
    for bit in order:
        vector = {**mode.controls, "clk": 0}
        for reg, value in zip(registers, values):
            if reg.serial_input:
                vector[reg.serial_input] = (value >> bit) & 1
        simulator.step(vector)
    return [read_state(simulator, r) for r in registers]


def solve(nl: Netlist, output: str, limit: int = 10):
    """Find input sequences that assert `output`, and verify them by simulation

    Returns (mode, solutions) where each solution is (register values, verified)
    """
    registers = [r for r in find_registers(nl) if r.serial_input and r.width > 1]
    mode = find_shift_mode(nl, registers)
    if mode is None:
        return None, []

    simulator = Simulator(nl)
    hits = [
        values
        for values, out in sweep(simulator, registers, output, mode.controls)
        if out
    ]

    solutions = []
    for values in hits[:limit]:
        _load(simulator, nl, registers, values, mode)
        verified = simulator.settle({**mode.controls, "clk": 0})[output] == 1
        solutions.append((values, verified))
    return mode, solutions

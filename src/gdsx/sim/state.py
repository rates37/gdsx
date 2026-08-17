"""Forcing and reading register state on a `Simulator`, and finding the
control values that let a design's shift registers be loaded serially.
"""

from __future__ import annotations
from dataclasses import dataclass
from itertools import product
from typing import TYPE_CHECKING

from ..netlist import Netlist
from .simulator import Simulator

if TYPE_CHECKING:  # types only, so this is not a runtime dependency on analysis/
    from ..analysis.registers import Register


def load_state(simulator: Simulator, register: Register, value: int) -> None:
    """Force a register's flops to hold `value` (bit i in flops[i])"""
    for i, flop in enumerate(register.flops):
        simulator.state[flop] = (value >> i) & 1


@dataclass
class ShiftMode:
    """How to drive the design so the shift registers actually load"""

    controls: dict[str, int]  # non-data input ports and the values to hold
    msb_first: bool

    @property
    def description(self) -> str:
        held = ", ".join(f"{p}={v}" for p, v in sorted(self.controls.items()))
        return f"hold {held}; feed bits {'MSB' if self.msb_first else 'LSB'} first"


PROBES = (
    0b10110001,
    0b01001110,
)  # asymmetric bit ordering, so a reversed load is obvious


def find_shift_mode(nl: Netlist, registers: list[Register]) -> ShiftMode | None:
    """Discover the control values and bit order that load the registers

    Try every control combination and keep the one that demonstrably shifts a
    known probe value in.
    """
    simulator = Simulator(nl)
    data_ports = {r.serial_input for r in registers if r.serial_input}
    controls = sorted(
        p for p, d in nl.ports.items() if d == "input" and p not in data_ports
    )
    controls = [p for p in controls if p != "clk"]

    for settings in product((0, 1), repeat=len(controls)):
        held = dict(zip(controls, settings))
        for msb_first in (True, False):
            mode = ShiftMode(held, msb_first)
            if _load(simulator, nl, registers, PROBES, mode) == list(
                PROBES[: len(registers)]
            ):
                return mode
    return None


def _load(simulator, nl: Netlist, registers, values, mode: ShiftMode) -> list[int]:
    """Shift `values` in serially, then read the registers back"""
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


def read_state(simulator: Simulator, register: Register) -> int:
    return sum(simulator.state[f] << i for i, f in enumerate(register.flops))

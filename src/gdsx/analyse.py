"""
Three steps:

  * Registers (structural, exact): Flops sharing a control signature (cell
    type, clock, async reset) are candidates for one register; splitting each
    control group by data flow separates registers that just share a clock

  * Buses (functional): Drive the register state directly and look for nets
    carrying bit k of some word-level operation on it. Random vectors implement
    filtering, since a few hundred reject essentially every wrong hypothesis at
    a lower cost. Survivors are confirmed over the whole state space when that
    is affordable, and flagged `sampled` when it is not.

  * Operators: Whatever produces the widest bus is one unit and whatever
    consumes it is another
"""

from __future__ import annotations

from .functions import data_nets, is_sequential

from .analysis.registers import Register, Block, Analysis, find_registers, _survey
from .analysis.bitorder import (
    indexed_ports,
    probe_positions,
    check_order,
    resolve_bit_order,
)
from .sim.state import load_state, read_state, ShiftMode, find_shift_mode
from .analysis.solve import Predicate, identify, sweep, solve
from .analysis.datapath import (
    Bus,
    OPERATORS,
    find_bus,
    truth_vectors,
    sampled_vectors,
    OPERATOR_VERILOG,
    prove_bus,
    find_buses,
    EVIDENCE,
    OPERATOR_UNITS,
    split_datapath,
    analyse,
)

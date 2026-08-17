"""Design analysis: register recovery, bit-order recovery, solving, datapath recovery."""

from .registers import Register, Block, Analysis, find_registers
from .bitorder import indexed_ports, probe_positions, check_order, resolve_bit_order
from .solve import Predicate, identify, sweep, solve
from .datapath import (
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

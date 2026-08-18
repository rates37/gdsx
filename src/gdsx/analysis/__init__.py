"""Design analysis: register recovery, bit-order recovery, solving, datapath
recovery, backward justification."""

from .registers import Register, Block, Analysis, find_registers, describe
from .grouping import mutual
from .constraints import Constraint, System, from_sensitivity
from .weights import Weight, infer
from .decode import Orbit, OrbitKind, selects, orbit
from .layout import Layout, LayoutEdge, LayoutNode, MAX_NODES, TooManyNodes, layered
from .justify import (
    Choice,
    Requirements,
    Unenumerable,
    conflict,
    forced_by,
    requirements,
)
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

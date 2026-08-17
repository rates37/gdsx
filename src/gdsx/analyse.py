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
import warnings

from .core.graph import Graph
from .functions import data_nets, is_sequential
from .netlist import Netlist

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


def support(nl: Netlist, net: str) -> set[str]:
    """Everything the net depends on, stopping at flop outputs and ports
    Returns a mix of instance names (flip flops) and net names (ports/constants)

    Deprecated: use `core.graph.Graph.support`.
    """
    warnings.warn(
        "gdsx.analyse.support is deprecated; use gdsx.core.graph.Graph.support",
        DeprecationWarning,
        stacklevel=2,
    )
    return Graph.of(nl).support(net)


def cone_nets(nl: Netlist, nets: set[str], stop: set[str]) -> set[str]:
    """Every net feeding `nets`, walking back but never through `stop`

    Deprecated: use `core.graph.Graph.cone`.
    """
    warnings.warn(
        "gdsx.analyse.cone_nets is deprecated; use gdsx.core.graph.Graph.cone",
        DeprecationWarning,
        stacklevel=2,
    )
    return Graph.of(nl).cone(nets, stop=frozenset(stop))


def cone_instances(nl: Netlist, nets: set[str], stop: set[str]) -> set[str]:
    """Instances driving `nets`, walking back but never through `stop`

    Deprecated: use `core.graph.Graph.cone(..., returns="instances")`.
    """
    warnings.warn(
        "gdsx.analyse.cone_instances is deprecated; "
        'use gdsx.core.graph.Graph.cone(..., returns="instances")',
        DeprecationWarning,
        stacklevel=2,
    )
    return Graph.of(nl).cone(nets, stop=frozenset(stop), returns="instances")

"""Datapath recovery: find word-level buses and the operators that produce them.

Drive the register state directly and look for nets carrying bit k of some
word-level operation on it. Random vectors implement filtering, since a few
hundred reject essentially every wrong hypothesis at a lower cost. Survivors
are confirmed over the whole state space when that is affordable, and
flagged `sampled` when it is not.

Whatever produces the widest bus is one unit and whatever consumes it is
another.
"""

from __future__ import annotations
import random
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from itertools import product
from pathlib import Path

from ..core.graph import Graph
from ..functions import clock_nets, data_nets, is_sequential, lookup, output_net
from ..netlist import Netlist, to_cone_verilog
from ..sim import Simulator
from ..sim.state import load_state
from .. import verify
from .registers import Analysis, Block, Register, describe, find_registers
from .solve import identify


@dataclass
class Bus:
    """An internal multi-bit value"""

    name: str
    nets: list[str]  # uses LSB first
    inverted: list[bool]  # per bit
    description: str
    # how the claim was established: "sat" (proven for all inputs at any
    # width), "exhaustive" (swept the whole state space), or "sampled"
    # (matched random vectors only)
    tier: str = "sampled"

    @property
    def width(self) -> int:
        return len(self.nets)

    @property
    def proven(self) -> bool:
        return self.tier in ("sat", "exhaustive")


def _probe(
    simulator: Simulator, registers: list[Register], inputs: dict[str, int], combos
):
    """Run the design for each register-value combination, recording every net

    Returns mapping {net: bytes} where byte i is that net's value for combos[i]. Byte
    strings make the later matching a C-speed comparison rather than a Python
    loop
    """
    nets = sorted(simulator.netlist.nets)
    columns = {net: bytearray(len(combos)) for net in nets}
    for i, values in enumerate(combos):
        for reg, value in zip(registers, values):
            load_state(simulator, reg, value)
        settled = simulator.settle(inputs)
        for net in nets:
            columns[net][i] = settled.get(net, 0)
    return {net: bytes(col) for net, col in columns.items()}


def truth_vectors(
    simulator: Simulator, registers: list[Register], inputs: dict[str, int]
):
    """Every net's behaviour over the whole register state space"""
    combos = list(product(*(range(1 << r.width) for r in registers)))
    return combos, _probe(simulator, registers, inputs, combos)


def sampled_vectors(
    simulator: Simulator,
    registers: list[Register],
    inputs: dict[str, int],
    count: int = 256,
    seed: int = 0,
):
    """Every net's behaviour over random register values

    This is the filter stage: a few hundred vectors reject essentially every
    wrong hypothesis, and unlike the exhaustive sweep the cost is not cooked.
    NOTE: Whatever survives still has to be confirmed.
    """
    rng = random.Random(seed)
    combos = [
        tuple(rng.randrange(1 << r.width) for r in registers) for _ in range(count)
    ]
    return combos, _probe(simulator, registers, inputs, combos)


# Word-level operations to look for. Each maps the register values
# to an integer
OPERATORS = {
    "sum": ("+", lambda values: sum(values)),
    "difference": ("-", lambda values: values[0] - values[1]),
    "and": ("&", lambda values: values[0] & values[1]),
    "or": ("|", lambda values: values[0] | values[1]),
    "xor": ("^", lambda values: values[0] ^ values[1]),
}


def find_bus(
    combos, vectors, compute, width: int, name: str, description: str
) -> Bus | None:
    """Look for nets carrying bits 0..width-1 of `compute(values)`"""
    by_vector: dict[bytes, str] = {}
    for net, vector in sorted(vectors.items()):
        by_vector.setdefault(vector, net)

    nets, inverted = [], []
    for k in range(width):
        want = bytes((compute(values) >> k) & 1 for values in combos)
        if want in by_vector:
            nets.append(by_vector[want])
            inverted.append(False)
            continue
        anti = bytes(1 - b for b in want)
        if anti not in by_vector:
            return None
        nets.append(by_vector[anti])
        inverted.append(True)
    return Bus(name, nets, inverted, description)


def _is_intermediate(nl: Netlist, inner: Bus, outer: Bus) -> bool:
    """True if `inner` is a step on the way to `outer` rather than a result"""
    if inner.width >= outer.width:
        return False
    upstream = Graph.of(nl).cone(set(outer.nets))
    # Nets the two buses share are part of `outer` already (a ripple adder's
    # sum bit 0 is its propagate bit 0), so they are not evidence either way.
    return (set(inner.nets) - set(outer.nets)) <= upstream


# Verilog for each operator, over operand words `a` and `b`.
OPERATOR_VERILOG = {
    "sum": "a + b",
    "difference": "a - b",
    "and": "a & b",
    "or": "a | b",
    "xor": "a ^ b",
}


def _reference_module(
    bus: Bus, registers: list[Register], extra_inputs: list[str]
) -> str:
    """A behavioural model of the candidate bus, for the miter to compare against"""
    operand_ports = [
        f"{chr(ord('a') + i)}_{b}"
        for i, r in enumerate(registers)
        for b in range(r.width)
    ]
    out_ports = [f"y_{k}" for k in range(bus.width)]
    ports = operand_ports + extra_inputs + out_ports

    lines = [f"module bus_ref ({', '.join(ports)});"]
    lines.append(f"  input {', '.join(operand_ports + extra_inputs)};")
    lines.append(f"  output {', '.join(out_ports)};")
    for i, reg in enumerate(registers):
        letter = chr(ord("a") + i)
        bits = ", ".join(f"{letter}_{b}" for b in reversed(range(reg.width)))
        lines.append(f"  wire [{reg.width - 1}:0] {letter} = {{{bits}}};")
    lines.append(f"  wire [{bus.width - 1}:0] r = {OPERATOR_VERILOG[bus.name]};")
    for k, inverted in enumerate(bus.inverted):
        lines.append(f"  assign y_{k} = {'~' if inverted else ''}r[{k}];")
    lines += ["endmodule", ""]
    return "\n".join(lines)


def prove_bus(nl: Netlist, bus: Bus, registers: list[Register], workdir) -> bool:
    """Prove the gates producing `bus` really implement its operator, at any width.

    Cuts the cone between the register outputs and the bus nets out into its own
    module, and miters it against a behavioral model. Because the cone is
    combinational this is one SAT query
    """

    by_name = {i.name: i for i in nl.instances}
    rename: dict[str, str] = {}
    for i, reg in enumerate(registers):
        letter = chr(ord("a") + i)
        for bit, flop in enumerate(reg.flops):
            net = output_net(lookup(by_name[flop].cell), by_name[flop].connections)
            if net is None:
                return False
            rename[net] = f"{letter}_{bit}"
    for k, net in enumerate(bus.nets):
        rename[net] = f"y_{k}"

    instances = Graph.of(nl).cone(
        set(bus.nets), stop=frozenset(set(rename) - set(bus.nets)), returns="instances"
    )
    if not instances:
        return False

    gate = to_cone_verilog(nl, "bus_gate", instances, rename, bus.nets)
    extra = sorted(_free_inputs(nl, instances) - set(rename))
    result = verify.combinational_equivalence(
        gate,
        "bus_gate",
        _reference_module(bus, registers, extra),
        "bus_ref",
        workdir,
        tag=bus.name,
    )
    return result.proven


@contextmanager
def _scratch(workdir: Path | None):
    if workdir is not None:
        workdir.mkdir(parents=True, exist_ok=True)
        yield workdir
    else:
        with tempfile.TemporaryDirectory() as tmp:
            yield Path(tmp)


def _confirm(nl, bus, registers, compute, sweep_data, workdir) -> str | None:
    """Establish a filtered candidate, or reject it. Returns the tier earned.

    SAT first: it is both stronger (all inputs, any width) and faster than
    sweeping. If yosys is missing or the proof does not come back clean, fall
    back to the sweep where it is affordable
    """

    if verify.available():
        with _scratch(workdir) as directory:
            try:
                if prove_bus(nl, bus, registers, directory):
                    return "sat"
            except (OSError, verify.YosysMissing):
                pass

    full_combos, full_vectors = sweep_data()
    if full_combos is None:
        return "sampled"
    confirmed = find_bus(
        full_combos, full_vectors, compute, bus.width, bus.name, bus.description
    )
    if confirmed is None or confirmed.nets != bus.nets:
        return None
    return "exhaustive"


def _free_inputs(nl: Netlist, instances: set[str]) -> set[str]:
    """Nets the cone reads but does not drive (i.e. its input nets)"""
    driven, read = set(), set()
    for inst in nl.instances:
        if inst.name not in instances:
            continue
        cell = lookup(inst.cell)
        driven |= {inst.connections[p] for p in cell.functions if p in inst.connections}
        read |= {inst.connections[p] for p in cell.inputs if p in inst.connections}
    return read - driven - nl.power_nets


def find_buses(
    nl: Netlist,
    simulator: Simulator,
    registers: list[Register],
    inputs: dict[str, int],
    min_width: int | None = None,
    workdir: Path | None = None,
) -> list[Bus]:
    """Discover word-level buses without being told what to look for.

    Filter with random vectors, then confirm survivors by SAT where yosys is
    available, by sweeping the state space where it is not and that is
    affordable, and otherwise not at all (reported at the `sampled` tier).

    `min_width` defaults to the operand width: an adder's internals do
    compute `a0 & b0` and `a0 ^ b0`, so without a floor every adder looks like
    it also contains a 2-bit AND. A bus that only ever matched the sample is
    reported at the `sampled` tier.
    """
    if len(registers) != 2:
        return []
    operand_width = max(r.width for r in registers)
    floor = operand_width if min_width is None else min_width

    combos, vectors = sampled_vectors(simulator, registers, inputs)

    # Only pay for the exhaustive sweep if SAT cannot do the confirming, which
    # for a design of any size it usually can
    affordable = sum(r.width for r in registers) <= 20
    swept: tuple | None = None

    def sweep_data():
        nonlocal swept
        if swept is None:
            swept = (
                truth_vectors(simulator, registers, inputs)
                if affordable
                else (None, None)
            )
        return swept

    found = []
    for name, (symbol, compute) in OPERATORS.items():
        description = f"{registers[0].name} {symbol} {registers[1].name}"
        for width in range(operand_width + 1, floor - 1, -1):
            top = {(compute(values) >> (width - 1)) & 1 for values in combos}
            if len(top) == 1:
                continue  # constant top bit: the result is narrower
            bus = find_bus(combos, vectors, compute, width, name, description)
            if bus is None:
                continue
            tier = _confirm(nl, bus, registers, compute, sweep_data, workdir)
            if tier is None:
                continue  # the random sample lied, drop it
            bus.tier = tier
            found.append(bus)
            break

    found.sort(key=lambda b: -b.width)
    return [
        bus
        for bus in found
        if not any(
            _is_intermediate(nl, bus, other) for other in found if other is not bus
        )
    ]


# How a bus claim was established
EVIDENCE = {
    "sat": "proven by SAT",
    "exhaustive": "proven by sweep",
    "sampled": "random vectors only",
}


# What to call the logic that produces each kind of bus
OPERATOR_UNITS = {
    "sum": "adder",
    "difference": "subtractor",
    "and": "bitwise AND",
    "or": "bitwise OR",
    "xor": "bitwise XOR",
}


def split_datapath(
    nl: Netlist, registers: list[Register], output: str, inputs: dict[str, int]
):
    """Find the widest internal bus, then split the gates around it"""
    if len(registers) != 2:
        return None

    simulator = Simulator(nl)
    buses = find_buses(nl, simulator, registers, inputs)
    if not buses:
        return None
    bus = buses[0]

    flop_outputs = {
        output_net(lookup(inst.cell), inst.connections)
        for inst in nl.instances
        if is_sequential(inst.cell)
    } - {None}
    graph = Graph.of(nl)
    producer = graph.cone(
        set(bus.nets), stop=frozenset(flop_outputs), returns="instances"
    )
    consumer = (
        graph.cone(
            {output},
            stop=frozenset(set(bus.nets) | flop_outputs),
            returns="instances",
        )
        - producer
    )
    return bus, producer, consumer


def analyse(nl: Netlist, inputs: dict[str, int] | None = None) -> Analysis:
    result = Analysis()
    result.registers = find_registers(nl)

    simulator = Simulator(nl)
    inputs = inputs or {p: 0 for p, d in nl.ports.items() if d == "input"}

    graph = Graph.of(nl)
    by_name = {i.name: i for i in nl.instances}
    flop_outputs = {
        output_net(lookup(by_name[f].cell), by_name[f].connections)
        for reg in result.registers
        for f in reg.flops
    } - {None}
    for reg in result.registers:
        feeding = set().union(
            *(
                data_nets(lookup(by_name[f].cell), by_name[f].connections)
                for f in reg.flops
            )
        )
        support_gates = graph.cone(
            feeding, stop=frozenset(flop_outputs), returns="instances"
        )
        result.blocks.append(
            Block(
                reg.name,
                describe(reg),
                set(reg.flops) | support_gates,
            )
        )

    clocks = set().union(
        *(
            clock_nets(lookup(by_name[f].cell), by_name[f].connections)
            for reg in result.registers
            for f in reg.flops
        )
    )
    clock_tree = graph.cone(clocks, stop=frozenset(flop_outputs), returns="instances")
    if clock_tree:
        result.blocks.append(
            Block("clock_tree", "buffers driving the flop clocks", clock_tree)
        )

    outputs = [p for p, d in nl.ports.items() if d == "output"]
    datapath = [r for r in result.registers if r.width > 1]
    for out in outputs:
        described, note = identify(simulator, datapath, out, inputs)
        if described:
            result.operators.append(described.text)
            result.predicates.append(described)
        if note:
            result.notes.append(note)

        split = split_datapath(nl, datapath, out, inputs)
        if split is None:
            continue
        bus, producer, consumer = split
        result.buses.append(bus)
        unit = OPERATOR_UNITS.get(bus.name, bus.name)
        result.blocks.append(
            Block(
                bus.name,
                f"{bus.width}-bit {unit} -> {bus.description}, {EVIDENCE[bus.tier]}",
                producer,
            )
        )
        result.blocks.append(
            Block(f"cmp_{out}", f"comparator on {bus.name} -> {out}", consumer)
        )
    return result

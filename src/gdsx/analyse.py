"""Analysis of netlist, attempt to simplify/abstract

Two steps:

  * Registers: Flops that feed each other in a line are a shift register,
        bit order known from the chain order
  * Operators: Once we know which flops hold which bits, drive the register
        state directly and sweep the whole input space
"""

from __future__ import annotations
from dataclasses import dataclass, field
from itertools import product

from .functions import FlopFunction, is_sequential, lookup
from .netlist import Netlist
from .sim import Simulator


@dataclass
class Register:
    """A group of flip-flops that shift as a unit. `flops` is in bit order, LSB first"""

    name: str
    flops: list[str]
    serial_input: str | None = None

    @property
    def width(self) -> int:
        return len(self.flops)


@dataclass
class Block:
    """A named piece of the design, with the gates that make it up"""

    name: str
    description: str
    instances: set[str] = field(default_factory=set)


@dataclass
class Analysis:
    registers: list[Register] = field(default_factory=list)
    blocks: list[Block] = field(default_factory=list)
    operators: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _drivers(nl: Netlist) -> dict[str, tuple[str, str]]:
    """returns mapping of net -> (instance name, output pin) that drives it"""
    out = {}
    for inst in nl.instances:
        fn = lookup(inst.cell)
        if fn and not is_sequential(inst.cell):
            out[inst.connections[fn.output]] = (inst.name, fn.output)
        elif isinstance(fn, FlopFunction):
            out[inst.connections[fn.output]] = (inst.name, fn.output)
    return out


def support(nl: Netlist, net: str) -> set[str]:
    """Everything the net depends on, stopping at flop outputs and ports
    Returns a mix of instance names (flip flops) and net names (ports/constants)
    """
    by_name = {i.name: i for i in nl.instances}
    drivers = _drivers(nl)
    seen: set[str] = set()
    result: set[str] = set()
    stack = [net]
    while stack:
        n = stack.pop()
        if n in seen or n in nl.power_nets:
            continue
        seen.add(n)
        if n not in drivers:
            result.add(n)  # a port, or an undriven net
            continue
        inst_name, _ = drivers[n]
        inst = by_name[inst_name]
        if is_sequential(inst.cell):
            result.add(inst_name)
            continue
        fn = lookup(inst.cell)
        stack.extend(inst.connections[p] for p in fn.inputs)
    return result


def find_registers(nl: Netlist) -> list[Register]:
    """Chain flops into registers by following each flop's data dependency"""
    flops = [i for i in nl.instances if is_sequential(i.cell)]
    names = {f.name for f in flops}

    # Which FF feeds each FF's D input, and which ports it sees
    feeder: dict[str, str | None] = {}
    ports_seen: dict[str, set[str]] = {}
    for f in flops:
        fn = lookup(f.cell)
        deps = support(nl, f.connections[fn.data])
        # A FF feeding its own D is an enable/hold path
        upstream = (deps & names) - {f.name}
        feeder[f.name] = next(iter(upstream)) if len(upstream) == 1 else None
        ports_seen[f.name] = {d for d in deps if d in nl.ports}

    # A chain head is a flop nothing else feeds from
    followers: dict[str, list[str]] = {}
    for name, src in feeder.items():
        if src:
            followers.setdefault(src, []).append(name)

    # Control ports (clock enables, resets) reach every flop
    # a data port that only one flop sees is that chain's serial input
    common = set.intersection(*ports_seen.values()) if ports_seen else set()

    heads = [
        f.name for f in flops if feeder[f.name] is None or feeder[f.name] not in names
    ]
    registers = []
    for head in sorted(heads):
        chain = [head]
        while True:
            nxt = followers.get(chain[-1], [])
            if len(nxt) != 1:
                break  # fan-out to two flops: not a linear shift chain
            chain.append(nxt[0])
        serial = sorted(ports_seen[head] - common)
        registers.append(
            Register(
                name=f"reg_{serial[0]}" if len(serial) == 1 else f"reg_{head}",
                flops=chain,
                serial_input=serial[0] if len(serial) == 1 else None,
            )
        )
    return registers


# functional identification


def load_state(simulator: Simulator, register: Register, value: int) -> None:
    """Force a register's flops to hold `value` (bit i in flops[i])"""
    for i, flop in enumerate(register.flops):
        simulator.state[flop] = (value >> i) & 1


def sweep(
    simulator: Simulator, registers: list[Register], output: str, inputs: dict[str, int]
):
    """Every combination of register values -> the output bit it produces"""
    widths = [r.width for r in registers]
    for values in product(*(range(1 << w) for w in widths)):
        for reg, value in zip(registers, values):
            load_state(simulator, reg, value)
        yield values, simulator.settle(inputs)[output]


def identify(
    simulator: Simulator, registers: list[Register], output: str, inputs: dict[str, int]
):
    """Name the function of `output` over the register contents, if possible"""
    if sum(r.width for r in registers) > 20:
        return None, "input space too large to sweep exhaustively"

    truth = dict(sweep(simulator, registers, output, inputs))
    hits = {k for k, v in truth.items() if v}
    if not hits:
        return None, f"{output} is never asserted"

    if len(registers) == 2:
        sums = {a + b for a, b in hits}
        if len(sums) == 1:
            k = sums.pop()
            expected = {(a, b) for a, b in truth if a + b == k}
            if hits == expected:
                return (
                    f"{output} = ({registers[0].name} + {registers[1].name} == {k})",
                    None,
                )
        diffs = {a - b for a, b in hits}
        if len(diffs) == 1:
            k = diffs.pop()
            expected = {(a, b) for a, b in truth if a - b == k}
            if hits == expected:
                return (
                    f"{output} = ({registers[0].name} - {registers[1].name} == {k})",
                    None,
                )
        if hits == {(a, b) for a, b in truth if a > b}:
            return f"{output} = ({registers[0].name} > {registers[1].name})", None
        if hits == {(a, b) for a, b in truth if a == b}:
            return f"{output} = ({registers[0].name} == {registers[1].name})", None

    return (
        None,
        f"{output} asserted for {len(hits)} of {len(truth)} states. no known operator matches",
    )


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


# datapath recovery functions (ignore)


@dataclass
class Bus:
    """An internal multi-bit value"""

    name: str
    nets: list[str]  # uses LSB first
    inverted: list[bool]  # per bit
    description: str

    @property
    def width(self) -> int:
        return len(self.nets)


def truth_vectors(
    simulator: Simulator, registers: list[Register], inputs: dict[str, int]
):
    """Every net's behaviour over the whole register state space

    Returns (combos, {net: bytes}) where each byte string is the net's value at
    the matching entry of `combos`.
    """
    combos = list(product(*(range(1 << r.width) for r in registers)))
    nets = sorted(simulator.netlist.nets)
    columns = {net: bytearray(len(combos)) for net in nets}
    for i, values in enumerate(combos):
        for reg, value in zip(registers, values):
            load_state(simulator, reg, value)
        settled = simulator.settle(inputs)
        for net in nets:
            columns[net][i] = settled.get(net, 0)
    return combos, {net: bytes(col) for net, col in columns.items()}


def find_bus(
    combos, vectors, bit_value, width: int, name: str, description: str
) -> Bus | None:
    """Look for nets behaving like bits [0..width-1] of `bit_value(values, k)`"""
    nets, inverted = [], []
    for k in range(width):
        want = bytes(bit_value(values, k) for values in combos)
        anti = bytes(1 - b for b in want)
        match = next((n for n, v in sorted(vectors.items()) if v == want), None)
        if match is not None:
            nets.append(match)
            inverted.append(False)
            continue
        match = next((n for n, v in sorted(vectors.items()) if v == anti), None)
        if match is None:
            return None
        nets.append(match)
        inverted.append(True)
    return Bus(name, nets, inverted, description)


def cone_instances(nl: Netlist, nets: set[str], stop: set[str]) -> set[str]:
    """Instances driving `nets`, walking back but never through `stop`"""
    drivers = _drivers(nl)
    by_name = {i.name: i for i in nl.instances}
    seen: set[str] = set()
    found: set[str] = set()
    stack = list(nets)
    while stack:
        net = stack.pop()
        if net in seen or net in stop or net in nl.power_nets or net not in drivers:
            continue
        seen.add(net)
        inst = by_name[drivers[net][0]]
        found.add(inst.name)
        if is_sequential(inst.cell):
            continue
        fn = lookup(inst.cell)
        stack.extend(inst.connections[p] for p in fn.inputs)
    return found


def split_datapath(
    nl: Netlist, registers: list[Register], output: str, inputs: dict[str, int]
):
    # heavily hard coded to the warmup puzzle
    if len(registers) != 2 or sum(r.width for r in registers) > 20:
        return None

    simulator = Simulator(nl)
    combos, vectors = truth_vectors(simulator, registers, inputs)

    width = max(r.width for r in registers) + 1
    bus = find_bus(
        combos,
        vectors,
        lambda values, k: (sum(values) >> k) & 1,
        width,
        "sum",
        f"{registers[0].name} + {registers[1].name}",
    )
    if bus is None:
        return None

    flop_outputs = {
        inst.connections[lookup(inst.cell).output]
        for inst in nl.instances
        if is_sequential(inst.cell)
    }
    adder = cone_instances(nl, set(bus.nets), flop_outputs)
    comparator = cone_instances(nl, {output}, set(bus.nets) | flop_outputs) - adder
    return bus, adder, comparator


def analyse(nl: Netlist, inputs: dict[str, int] | None = None) -> Analysis:
    result = Analysis()
    result.registers = find_registers(nl)

    simulator = Simulator(nl)
    inputs = inputs or {p: 0 for p, d in nl.ports.items() if d == "input"}

    by_name = {i.name: i for i in nl.instances}
    flop_outputs = {
        by_name[f].connections[lookup(by_name[f].cell).output]
        for reg in result.registers
        for f in reg.flops
    }
    for reg in result.registers:
        data_nets = {
            by_name[f].connections[lookup(by_name[f].cell).data] for f in reg.flops
        }
        support_gates = cone_instances(nl, data_nets, flop_outputs)
        result.blocks.append(
            Block(
                reg.name,
                f"{reg.width}-bit shift register",
                set(reg.flops) | support_gates,
            )
        )

    clock_nets = {
        by_name[f].connections[lookup(by_name[f].cell).clock]
        for reg in result.registers
        for f in reg.flops
    }
    clock_tree = cone_instances(nl, clock_nets, flop_outputs)
    if clock_tree:
        result.blocks.append(
            Block("clock_tree", "buffers driving the flop clocks", clock_tree)
        )

    outputs = [p for p, d in nl.ports.items() if d == "output"]
    datapath = [r for r in result.registers if r.width > 1]
    for out in outputs:
        described, note = identify(simulator, datapath, out, inputs)
        if described:
            result.operators.append(described)
        if note:
            result.notes.append(note)

        split = split_datapath(nl, datapath, out, inputs)
        if split is None:
            continue
        bus, adder, comparator = split
        result.blocks.append(
            Block(bus.name, f"{bus.width}-bit adder -> {bus.description}", adder)
        )
        result.blocks.append(
            Block(
                f"cmp_{out}", f"equality comparator on {bus.name} -> {out}", comparator
            )
        )
    return result

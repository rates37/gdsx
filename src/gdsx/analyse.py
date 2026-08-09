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
import random
from dataclasses import dataclass, field
from itertools import product

from .functions import (
    async_nets,
    base_name,
    clock_nets,
    data_nets,
    is_sequential,
    lookup,
    output_net,
)
from .netlist import Netlist
from .sim import Simulator


@dataclass
class Register:
    """A group of flip-flops that act as a unit.

    `flops` is in bit order, LSB first

    `ordered` says whether that bit order is real: a shift chain or a carry
    chain gives each flop a distinct depth in the group's dependency graph, but
    a parallel-load register's bits are order indistinguishable
    """

    name: str
    flops: list[str]
    serial_input: str | None = None
    kind: str = "register"
    ordered: bool = True

    @property
    def width(self) -> int:
        return len(self.flops)

    @property
    def description(self) -> str:
        order = "" if self.ordered else ", bit order unknown"
        return f"{self.width}-bit {self.kind}{order}"


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
    """returns mapping of net -> (instance name, output pin) that drives it

    A cell can drive several nets: `fa` produces SUM and COUT, a `dfbbp` both Q
    and Q_N.
    """
    out = {}
    for inst in nl.instances:
        cell = lookup(inst.cell)
        if cell is None:
            continue
        for pin in cell.functions:
            if pin in inst.connections:
                out[inst.connections[pin]] = (inst.name, pin)
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
        cell = lookup(inst.cell)
        stack.extend(inst.connections[p] for p in cell.inputs if p in inst.connections)
    return result


@dataclass
class _FlopInfo:
    signature: tuple  # what controls this flop: cell type, clock, async nets
    depends: set[str]  # FFs directly in its next-state cone
    ports: set[str]  # top-level ports in its next-state cone


def _survey(nl: Netlist) -> dict[str, _FlopInfo]:
    flops = [i for i in nl.instances if is_sequential(i.cell)]
    names = {f.name for f in flops}
    info = {}
    for f in flops:
        cell = lookup(f.cell)
        # next_state may involve several pins,
        # so the data cone is the union over all of them
        deps = set().union(
            *(support(nl, net) for net in data_nets(cell, f.connections))
        )
        info[f.name] = _FlopInfo(
            signature=(
                base_name(f.cell),
                frozenset(clock_nets(cell, f.connections)),
                frozenset(async_nets(cell, f.connections)),
            ),
            depends=deps & names,
            ports={d for d in deps if d in nl.ports},
        )
    return info


def _components(members: list[str], info: dict[str, _FlopInfo]) -> list[list[str]]:
    """Split FFs that share control into groups that actually talk to each other"""
    inside = set(members)
    adjacency = {m: (info[m].depends & inside) - {m} for m in members}
    for m, linked in list(adjacency.items()):
        for other in linked:
            adjacency[other] = adjacency[other] | {m}  # treat as undirected

    seen: set[str] = set()
    groups = []
    for start in sorted(members):
        if start in seen:
            continue
        stack, group = [start], []
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            group.append(node)
            stack.extend(sorted(adjacency[node] - seen))
        groups.append(sorted(group))
    return groups


def _transitive_depth(group: list[str], info: dict[str, _FlopInfo]) -> dict[str, int]:
    """How many other flops of the group each flop transitively depends on"""
    inside = set(group)
    reach: dict[str, set[str]] = {}

    def walk(node: str, path: frozenset) -> set[str]:
        if node in reach:
            return reach[node]
        if node in path:
            return set()  # a cycle: counters depend on themselves
        out = set()
        for dep in info[node].depends & inside:
            out.add(dep)
            out |= walk(dep, path | {node})
        reach[node] = out
        return out

    return {f: len(walk(f, frozenset()) - {f}) for f in group}


def _classify(group: list[str], info: dict[str, _FlopInfo]) -> str:
    inside = set(group)
    forward = {f: (info[f].depends & inside) - {f} for f in group}
    if len(group) > 1 and not any(forward.values()):
        return "parallel register"  # bits never talk, only shared control links them
    if all(len(v) <= 1 for v in forward.values()):
        return "shift register"
    if any(f in info[f].depends for f in group):
        return "feedback register"  # counter, accumulator, LFSR
    return "register"


def _group_name(
    group: list[str], info: dict[str, _FlopInfo], common: set[str]
) -> str | None:
    """Name a parallel register after the data ports its bits load from"""
    private = [sorted(info[f].ports - common) for f in group]
    if not all(len(p) == 1 for p in private):
        return None
    names = [p[0] for p in private]
    prefix = names[0]
    for name in names[1:]:
        while not name.startswith(prefix):
            prefix = prefix[:-1]
    prefix = prefix.rstrip("_[")
    return f"reg_{prefix}" if prefix else None


def find_registers(nl: Netlist) -> list[Register]:
    """Group flops into registers.

    Flops that share a control signature (same cell type, clock and async
    reset) are candidates for one register. That over-groups (warm up
    design's two shift registers share everything), so each group is
    then split into the parts that actually exchange data
    """
    info = _survey(nl)
    if not info:
        return []

    by_signature: dict[tuple, list[str]] = {}
    for name, flop in info.items():
        by_signature.setdefault(flop.signature, []).append(name)

    # Control ports reach every flop, a data port only one flop sees is that
    # group's serial input
    common = set.intersection(*(f.ports for f in info.values()))

    registers = []
    for signature in sorted(by_signature, key=str):
        members = by_signature[signature]
        groups = _components(members, info)
        if len(groups) > 1 and all(len(g) == 1 for g in groups):
            # none of these bits feed each other, so shared control is the only evidence there is
            # -> they are one parallel-load register
            groups = [sorted(members)]

        for group in groups:
            depth = _transitive_depth(group, info)
            ordered = len(set(depth.values())) == len(group)
            flops = sorted(group, key=lambda f: (depth[f], f))

            kind = _classify(group, info)
            head = flops[0]
            private = sorted(info[head].ports - common)
            serial = (
                private[0] if len(private) == 1 and kind == "shift register" else None
            )
            name = f"reg_{serial}" if serial else _group_name(group, info, common)
            registers.append(
                Register(
                    name=name or f"reg_{head}",
                    flops=flops,
                    serial_input=serial,
                    kind=kind,
                    ordered=ordered,
                )
            )
    registers.sort(
        key=lambda r: r.name
    )  # sort to give stable output so testing is deterministic
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
    #: "proven" over the whole state space, or "sampled" from random vectors
    tier: str = "sampled"

    @property
    def width(self) -> int:
        return len(self.nets)


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


def cone_nets(nl: Netlist, nets: set[str], stop: set[str]) -> set[str]:
    """Every net feeding `nets`, walking back but never through `stop`"""
    drivers = _drivers(nl)
    by_name = {i.name: i for i in nl.instances}
    seen: set[str] = set()
    stack = list(nets)
    while stack:
        net = stack.pop()
        if net in seen or net in stop or net not in drivers:
            continue
        seen.add(net)
        inst = by_name[drivers[net][0]]
        if is_sequential(inst.cell):
            continue
        cell = lookup(inst.cell)
        stack.extend(inst.connections[p] for p in cell.inputs if p in inst.connections)
    return seen


def _is_intermediate(nl: Netlist, inner: Bus, outer: Bus) -> bool:
    """True if `inner` is a step on the way to `outer` rather than a result"""
    if inner.width >= outer.width:
        return False
    upstream = cone_nets(nl, set(outer.nets), set())
    # Nets the two buses share are part of `outer` already (a ripple adder's
    # sum bit 0 is its propagate bit 0), so they are not evidence either way.
    return (set(inner.nets) - set(outer.nets)) <= upstream


def find_buses(
    nl: Netlist,
    simulator: Simulator,
    registers: list[Register],
    inputs: dict[str, int],
    min_width: int | None = None,
) -> list[Bus]:
    """Discover word-level buses

    Filter with random vectors, then confirm survivors exhaustively when the
    state space is small enough. A bus that only ever matched the sample is
    reported at the `sampled` tier.

    `min_width` defaults to the operand width: an adder's internals do
    compute `a0 & b0` and `a0 ^ b0`, so without a floor every adder looks like
    it also contains a 2-bit AND
    """
    if len(registers) != 2:
        return []
    operand_width = max(r.width for r in registers)
    floor = operand_width if min_width is None else min_width

    combos, vectors = sampled_vectors(simulator, registers, inputs)
    exhaustive = sum(r.width for r in registers) <= 20
    full_combos, full_vectors = (
        truth_vectors(simulator, registers, inputs) if exhaustive else (None, None)
    )

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
            if not exhaustive:
                bus.tier = "sampled"
                found.append(bus)
                break
            confirmed = find_bus(
                full_combos, full_vectors, compute, width, name, description
            )
            if confirmed is not None:  # the random sample did not lie
                confirmed.tier = "proven"
                found.append(confirmed)
                break

    found.sort(key=lambda b: -b.width)
    return [
        bus
        for bus in found
        if not any(
            _is_intermediate(nl, bus, other) for other in found if other is not bus
        )
    ]


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
        cell = lookup(inst.cell)
        stack.extend(inst.connections[p] for p in cell.inputs if p in inst.connections)
    return found


# What to call the logic that produces each kind of bus.
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
    """Find the widest internal bus, then split the gates around it
    """
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
    producer = cone_instances(nl, set(bus.nets), flop_outputs)
    consumer = cone_instances(nl, {output}, set(bus.nets) | flop_outputs) - producer
    return bus, producer, consumer


def analyse(nl: Netlist, inputs: dict[str, int] | None = None) -> Analysis:
    result = Analysis()
    result.registers = find_registers(nl)

    simulator = Simulator(nl)
    inputs = inputs or {p: 0 for p, d in nl.ports.items() if d == "input"}

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
        support_gates = cone_instances(nl, feeding, flop_outputs)
        result.blocks.append(
            Block(
                reg.name,
                reg.description,
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
    clock_tree = cone_instances(nl, clocks, flop_outputs)
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
        bus, producer, consumer = split
        unit = OPERATOR_UNITS.get(bus.name, bus.name)
        result.blocks.append(
            Block(bus.name, f"{bus.width}-bit {unit} -> {bus.description}", producer)
        )
        result.blocks.append(
            Block(f"cmp_{out}", f"comparator on {bus.name} -> {out}", consumer)
        )
    return result

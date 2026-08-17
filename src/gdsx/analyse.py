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
import re
import tempfile
import warnings
from contextlib import contextmanager
from dataclasses import dataclass, field
from itertools import product
from pathlib import Path

from .core.graph import Graph
from .functions import (
    async_nets,
    base_name,
    clock_nets,
    data_nets,
    is_sequential,
    lookup,
    output_net,
)
from .netlist import Netlist, to_cone_verilog
from .sim import Simulator
from . import verify


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
    # where the bit order came from: "topology" (depth in the group's own
    # dependency graph), "probe" (recovered by one-hot probing, then checked),
    # or "" when there is no order to justify
    order_evidence: str = "topology"

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
    predicates: list[Predicate] = field(default_factory=list)
    buses: list[Bus] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


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
    return Graph(nl).support(net)


@dataclass
class _FlopInfo:
    signature: tuple  # what controls this flop: cell type, clock, async nets
    depends: set[str]  # FFs directly in its next-state cone
    ports: set[str]  # top-level ports in its next-state cone


def _roots(graph: Graph, nets: set[str]) -> frozenset:
    """What ultimately drives these nets: ports, or the flops behind them"""
    return frozenset().union(*(graph.support(n) for n in nets)) if nets else frozenset()


def _survey(nl: Netlist) -> dict[str, _FlopInfo]:
    graph = Graph(nl)
    flops = [i for i in nl.instances if is_sequential(i.cell)]
    names = {f.name for f in flops}
    info = {}
    for f in flops:
        cell = lookup(f.cell)
        # next_state may involve several pins,
        # so the data cone is the union over all of them
        deps = set().union(
            *(graph.support(net) for net in data_nets(cell, f.connections))
        )
        # Signature on what drives the clock and reset, not on the net itself.
        # A buffered clock tree gives every few flops their own clock net, which
        # would otherwise split one register into a group per buffer
        info[f.name] = _FlopInfo(
            signature=(
                base_name(f.cell),
                _roots(graph, clock_nets(cell, f.connections)),
                _roots(graph, async_nets(cell, f.connections)),
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
        for dep in sorted(info[node].depends & inside):
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
    used_names: set[str] = set()
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
            name = name or f"reg_{head}"
            while name in used_names:  # two groups can want the same data port
                name += "_"
            used_names.add(name)
            registers.append(
                Register(
                    name=name,
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


_INDEXED = re.compile(r"^(.*?)[_\[](\d+)\]?$")


def indexed_ports(nl: Netlist, direction: str = "output") -> dict[str, dict[int, str]]:
    """Ports that look like the bits of one word: `q_3`, `O[3]`, `sum_12`

    The index in a layout label is the designer's own, so it is the one piece of
    bit-order information that does not have to be inferred
    """
    families: dict[str, dict[int, str]] = {}
    for port, kind in nl.ports.items():
        if kind != direction:
            continue
        match = _INDEXED.match(port)
        if match:
            families.setdefault(match.group(1), {})[int(match.group(2))] = port
    return {base: bits for base, bits in families.items() if len(bits) > 1}


def probe_positions(
    nl: Netlist, register: Register, inputs: dict[str, int] | None = None
):
    """word -> {flop: bit index}, for the words each flop lands on cleanly

    Per word, because one flop legitimately reaches several. E.g., bit 2 of a register
    is bit 2 of the register's own output *and* three bits of the adder it
    feeds. The register word is the one where it moves a single bit
    """
    families = indexed_ports(nl)
    if not families:
        return {}

    simulator = Simulator(nl)
    inputs = inputs or {p: 0 for p, d in nl.ports.items() if d == "input"}
    simulator.state = dict.fromkeys(simulator.state, 0)
    baseline = simulator.settle(inputs)

    positions: dict[str, dict[str, int]] = {base: {} for base in families}
    for flop in register.flops:
        simulator.state = dict.fromkeys(simulator.state, 0)
        simulator.state[flop] = 1
        settled = simulator.settle(inputs)
        for base, bits in families.items():
            moved = [i for i, port in bits.items() if settled[port] != baseline[port]]
            if len(moved) == 1:
                positions[base][flop] = moved[0]
    return {base: found for base, found in positions.items() if found}


def check_order(nl: Netlist, register: Register, base: str, samples: int = 64) -> bool:
    """Confirm a recovered bit order: load a value, read it back off the word"""
    bits = indexed_ports(nl).get(base)
    if not bits or len(bits) < register.width:
        return False
    simulator = Simulator(nl)
    inputs = {p: 0 for p, d in nl.ports.items() if d == "input"}

    span = 1 << register.width
    values = (
        range(span)
        if register.width <= 8
        else [random.Random(0).randrange(span) for _ in range(samples)]
    )
    for value in values:
        load_state(simulator, register, value)
        settled = simulator.settle(inputs)
        read = sum(settled[bits[i]] << i for i in range(register.width))
        if read != value:
            return False
    return True


def _from_positions(register: Register, positions: dict[str, dict[str, int]]):
    """Split the group by which output word each flop lands on, in bit order"""
    usable = {
        base: found
        for base, found in positions.items()
        if len(found) >= 2 and set(found.values()) == set(range(len(found)))
    }

    remaining = set(register.flops)
    made = []
    for base, found in sorted(usable.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        if not set(found) <= remaining:
            continue  # overlaps a word already taken; skip rather than guess
        remaining -= set(found)
        made.append(
            Register(
                name=base,
                flops=[flop for flop, _ in sorted(found.items(), key=lambda kv: kv[1])],
                serial_input=register.serial_input,
                kind=register.kind,
                ordered=True,
                order_evidence="probe",
            )
        )
    return made, sorted(remaining)


def resolve_bit_order(nl: Netlist, registers: list[Register]) -> list[Register]:
    """Give an order to registers topology could not order"""
    resolved: list[Register] = []
    for register in registers:
        if register.ordered or register.width < 2:
            resolved.append(register)
            continue

        candidates, leftover = _from_positions(register, probe_positions(nl, register))

        if candidates and all(check_order(nl, c, c.name) for c in candidates):
            resolved.extend(candidates)
            if leftover:
                # whatever did not land on a word is still a group. it just has
                # no order, and saying so is the honest option
                resolved.append(
                    Register(
                        name=f"reg_{leftover[0]}",
                        flops=leftover,
                        serial_input=register.serial_input,
                        kind=register.kind,
                        ordered=False,
                        order_evidence="",
                    )
                )
        else:
            resolved.append(register)
    resolved.sort(key=lambda r: r.name)
    return resolved


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


def cone_nets(nl: Netlist, nets: set[str], stop: set[str]) -> set[str]:
    """Every net feeding `nets`, walking back but never through `stop`

    Deprecated: use `core.graph.Graph.cone`.
    """
    warnings.warn(
        "gdsx.analyse.cone_nets is deprecated; use gdsx.core.graph.Graph.cone",
        DeprecationWarning,
        stacklevel=2,
    )
    return Graph(nl).cone(nets, stop=frozenset(stop))


def _is_intermediate(nl: Netlist, inner: Bus, outer: Bus) -> bool:
    """True if `inner` is a step on the way to `outer` rather than a result"""
    if inner.width >= outer.width:
        return False
    upstream = Graph(nl).cone(set(outer.nets))
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

    instances = Graph(nl).cone(
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
    return Graph(nl).cone(nets, stop=frozenset(stop), returns="instances")


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
    graph = Graph(nl)
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

    graph = Graph(nl)
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

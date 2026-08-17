"""Find out what each port is for, giving (approximate) signature of an unknown design.

The classifications:

* clock/reset: reaches the clock or the asynchronous set/clear pins
  of the flops. Structural, exact, and includes the polarity that asserts.
* gate: holding it at one value freezes the design: nothing in it changes,
  whatever else happens. An enable, a chip select, a pause.
* data: changing it changes the state. Most ports.
* combinational: reaches the outputs but no flop at all, so it selects or
  modifies rather than being stored. An ALU opcode.
* unused: reaches nothing. Worth knowing, and it happens.

For outputs: how many cycles after an input moves the output can move, which is
pipeline depth measured rather than assumed, and whether it is registered or
falls straight out of the logic
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from .core.graph import Graph
from .functions import async_nets, clock_nets, is_sequential, lookup
from .netlist import Netlist
from .sim import Simulator

CYCLES = 12


@dataclass
class Port:
    name: str
    direction: str
    kind: str
    evidence: str = ""
    reach: int = 0  # flops it can affect
    asserted: int | None = None  # for resets and gates: the value that acts
    latency: int | None = None  # for outputs: cycles from an input change

    def __str__(self) -> str:
        detail = f" ({self.evidence})" if self.evidence else ""
        return f"{self.name:14s} {self.direction:6s} {self.kind}{detail}"


def _control_nets(nl: Netlist) -> tuple[set[str], set[str]]:
    """(ports driving flop clock pins, ports driving flop set/clear pins)

    Traced back to the ports: a clock is buffered before it reaches anything,
    so the port itself is never on a flop pin, and looking only at the pins
    finds the buffer outputs and calls the actual clock input inert
    """
    graph = Graph(nl)
    clocks: set[str] = set()
    resets: set[str] = set()
    for inst in nl.instances:
        cell = lookup(inst.cell)
        if cell is None or not cell.is_sequential:
            continue
        for net in clock_nets(cell, inst.connections):
            clocks |= {r for r in graph.support(net) if r in nl.ports}
        for net in async_nets(cell, inst.connections):
            resets |= {r for r in graph.support(net) if r in nl.ports}
    return clocks, resets


def _reach(nl: Netlist) -> dict[str, int]:
    """How many flops each port can affect, by forward reachability."""
    graph = Graph(nl)
    counts = {}
    flop_inputs: dict[str, set[str]] = {}
    for inst in nl.instances:
        if not is_sequential(inst.cell):
            continue
        cell = lookup(inst.cell)
        for pin in cell.inputs:
            if pin in inst.connections:
                flop_inputs.setdefault(inst.connections[pin], set()).add(inst.name)

    for port, direction in nl.ports.items():
        if direction != "input":
            continue
        seen = {port}
        for level in graph.fanout(port, depth=24, through_flops=False):
            seen |= set(level)
        counts[port] = len({f for net in seen for f in flop_inputs.get(net, ())})
    return counts


def _state(sim: Simulator) -> tuple:
    return tuple(sim.state[name] for name in sorted(sim.state))


def reset_polarity(nl: Netlist, port: str, cycles: int = CYCLES) -> int | None:
    """Which value of a reset port actually resets"""
    for value in (0, 1):
        rng = random.Random(7)
        others = [
            p for p, d in nl.ports.items() if d == "input" and p not in (port, "clk")
        ]
        sim = Simulator(nl)
        sim.step({**{p: rng.getrandbits(1) for p in others}, port: value, "clk": 0})
        held = _state(sim)
        for _ in range(cycles):
            sim.step({**{p: rng.getrandbits(1) for p in others}, port: value, "clk": 0})
            if _state(sim) != held:
                break
        else:
            return value
    return None


def _quiet(nl: Netlist) -> dict[str, int]:
    """Inputs held where the design is allowed to run"""
    _, resets = _control_nets(nl)
    quiet = {}
    for name, direction in nl.ports.items():
        if direction != "input" or name == "clk":
            continue
        if name in resets:
            asserted = reset_polarity(nl, name)
            quiet[name] = 1 - asserted if asserted is not None else 1
    return quiet


def _drive(sim: Simulator, vector: dict[str, int]) -> None:
    sim.step({**vector, "clk": 0})


def _freezes(
    nl: Netlist,
    port: str,
    value: int,
    quiet: dict[str, int],
    cycles: int = CYCLES,
    seed: int = 0,
) -> bool:
    """True if holding `port` at `value` stops the design changing"""
    rng = random.Random(seed)
    others = [p for p, d in nl.ports.items() if d == "input" and p not in (port, "clk")]

    def vector(bit: int | None) -> dict[str, int]:
        drawn = {p: quiet.get(p, rng.getrandbits(1)) for p in others}
        drawn[port] = rng.getrandbits(1) if bit is None else bit
        return drawn

    sim = Simulator(nl)
    for _ in range(cycles):
        _drive(sim, vector(None))

    before = _state(sim)
    for _ in range(cycles):
        _drive(sim, vector(value))
        if _state(sim) != before:
            return False  # it moved at some point, even if it came back
    return True


def _moves(
    nl: Netlist, port: str, quiet: dict[str, int], cycles: int = CYCLES, seed: int = 1
) -> bool:
    """True if toggling this port changes what the design does"""

    def run(driven: int) -> list[tuple]:
        rng = random.Random(seed)
        others = [
            p for p, d in nl.ports.items() if d == "input" and p not in (port, "clk")
        ]
        sim = Simulator(nl)
        trace = []
        for _ in range(cycles):
            drawn = {p: quiet.get(p, rng.getrandbits(1)) for p in others}
            drawn[port] = driven
            _drive(sim, drawn)
            trace.append(_state(sim))
        return trace

    return run(0) != run(1)


def inputs(nl: Netlist, cycles: int = CYCLES, facts=None) -> list[Port]:
    """Classify every input port"""
    clocks, resets = _control_nets(nl)
    reach = _reach(nl)
    quiet = _quiet(nl)
    stated = facts.port_kinds() if facts is not None else {}
    graph = Graph(nl)
    found = []

    for name, direction in sorted(nl.ports.items()):
        if direction != "input":
            continue
        port = Port(name, direction, "data", reach=reach.get(name, 0))

        if name in stated:
            port.kind, port.evidence = stated[name], "asserted"
            # The kind was stated; the active level was not, and everything
            # downstream needs it -- `outputs` holds gates open to measure
            # latency, and a reset held the wrong way round freezes the design.
            if port.kind == "reset":
                port.asserted = reset_polarity(nl, name, cycles)
            elif port.kind == "gate":
                frozen = [
                    value
                    for value in (0, 1)
                    if _freezes(nl, name, value, quiet, cycles)
                ]
                port.asserted = 1 - frozen[0] if len(frozen) == 1 else None
        elif name in clocks:
            port.kind, port.evidence = "clock", "drives the flop clock pins"
        elif name in resets:
            port.kind = "reset"
            port.asserted = reset_polarity(nl, name, cycles)
            port.evidence = "drives the flop set/clear pins" + (
                f", asserted {port.asserted}" if port.asserted is not None else ""
            )
        elif port.reach == 0:
            port.kind, port.evidence = "combinational", "reaches no flop"
            if not any(
                name in graph.support(out)
                for out in nl.ports
                if nl.ports[out] == "output"
            ):
                port.kind, port.evidence = "unused", "reaches nothing"
        else:
            frozen = [
                value for value in (0, 1) if _freezes(nl, name, value, quiet, cycles)
            ]
            # A gate stops the *design*, so it has to reach most of it. Holding a
            # one-bit data input at a constant also freezes a one-bit shift
            # register, and that is not an enable -- the reach is what separates
            # them, since an enable gates everything and data feeds a corner.
            if len(frozen) == 1 and port.reach < 0.9 * max(reach.values(), default=0):
                frozen = []
            if len(frozen) == 1:
                port.kind = "gate"
                port.asserted = 1 - frozen[0]
                port.evidence = f"held at {frozen[0]} nothing changes"
            elif not _moves(nl, name, quiet, cycles):
                port.kind, port.evidence = "inert", "toggling it changes nothing"
            else:
                port.evidence = f"reaches {port.reach} flops"
        found.append(port)
    return found


def outputs(
    nl: Netlist, data: list[str] | None = None, cycles: int = CYCLES, facts=None
) -> list[Port]:
    """How many clock edges after an input moves each output can move

    Zero means the output falls straight out of the logic. One means a register
    in the way, three means three, etc. That is pipeline depth measured rather than
    assumed.
    """
    quiet = _quiet(nl)
    classified = inputs(nl, cycles, facts)
    # Hold the gates open as well as the resets: latency measured on a design
    # whose enable is random is the latency plus however long it took to be
    # enabled, which is not a property of the design.
    for port in classified:
        if port.kind == "gate" and port.asserted is not None:
            quiet[port.name] = port.asserted
    sources = data or [
        p.name for p in classified if p.kind in ("data", "gate", "combinational")
    ]
    names = [p for p, d in nl.ports.items() if d == "output"]
    found = {name: Port(name, "output", "output") for name in names}
    others = [p for p, d in nl.ports.items() if d == "input" and p != "clk"]

    for port in sources:
        rng_a, rng_b = random.Random(3), random.Random(3)
        baseline, pulsed = Simulator(nl), Simulator(nl)

        def vector(rng, bit):
            drawn = {p: quiet.get(p, rng.getrandbits(1)) for p in others}
            drawn[port] = bit
            return {**drawn, "clk": 0}

        for cycle in range(cycles):
            low = vector(rng_a, 0)
            high = vector(rng_b, 1 if cycle == 0 else 0)

            # before the edge: anything that differs now is combinational
            if cycle == 0:
                settled_low = baseline.settle(low)
                settled_high = pulsed.settle(high)
                for name in names:
                    if settled_low.get(name) != settled_high.get(name):
                        found[name].latency = 0
                        found[
                            name
                        ].evidence = f"changes with {port}, no register in the way"

            baseline.step(low)
            pulsed.step(high)
            for name in names:
                if found[name].latency is not None:
                    continue
                if baseline.settle(low).get(name) != pulsed.settle(high).get(name):
                    found[name].latency = cycle + 1
                    found[name].evidence = f"{cycle + 1} edges after {port}"

    for port in found.values():
        if port.latency is None:
            port.kind = "static"
            port.evidence = f"never moved in {cycles} cycles of random driving"
        elif port.latency == 0:
            port.kind = "combinational"
        else:
            port.kind = "registered"
    return sorted(found.values(), key=lambda p: p.name)


@dataclass
class Interface:
    inputs: list[Port] = field(default_factory=list)
    outputs: list[Port] = field(default_factory=list)


def describe(nl: Netlist, cycles: int = CYCLES, facts=None) -> Interface:
    found = inputs(nl, cycles, facts)
    return Interface(inputs=found, outputs=outputs(nl, None, cycles, facts))


def report(interface: Interface) -> str:
    lines = ["inputs", ""]
    for port in interface.inputs:
        lines.append(f"  {port}")
    lines += ["", "outputs", ""]
    for port in interface.outputs:
        latency = "" if port.latency is None else f"  [{port.latency} cycle latency]"
        lines.append(f"  {port.name:14s} {port.kind}{latency}")
    lines += [
        "",
        "  Clock and reset are structural; the rest is measured by driving the",
        "  design. A port called 'data' is one that changes the state and cannot",
        "  be shown to freeze it, and 'static' means an output that did not move",
        "  in the window -- a UART's byte needs a whole frame, so widen it.",
    ]
    return "\n".join(lines)

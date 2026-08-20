"""Render the recovered structure as RTL and prove the rendering

The output is a hybrid. Registers, buses and predicates that the
analysis established become `always` blocks and continuous assignments. Every
gate not accounted for stays exactly where it was
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import verify
from .analyse import Analysis, Bus, Register, OPERATOR_VERILOG
from .analysis.registers import describe
from .core.graph import Graph
from .functions import clock_nets, generic_name, lookup, output_net
from .liberty import variables
from .netlist import Netlist, _chunks, ident


@dataclass
class Lift:
    verilog: str
    lifted: set[str] = field(default_factory=set)  # instances replaced by RTL
    kept: set[str] = field(default_factory=set)  # instances still emitted as gates
    statements: list[str] = field(default_factory=list)  # what was recovered

    @property
    def coverage(self) -> float:
        total = len(self.lifted) + len(self.kept)
        return len(self.lifted) / total if total else 0.0


def _concat(nets: list[str]) -> str:
    # Nets are stored LSB first, but Verilog concatenation is MSB first
    return "{" + ", ".join(ident(n) for n in reversed(nets)) + "}"


def _edge_and_guard(nl: Netlist, register: Register) -> tuple[str, str, str] | None:
    """(clock edge, reset edge, reset condition) for a register"""
    by_name = {i.name: i for i in nl.instances}
    inst = by_name[register.flops[0]]
    cell = lookup(inst.cell)
    if cell is None or not cell.is_sequential:
        return None

    clocks = clock_nets(cell, inst.connections)
    if len(clocks) != 1:
        return None  # a gated or derived clock: leave this register as gates
    clock = next(iter(clocks))

    seq = cell.sequential
    if seq.clear is None or seq.preset is not None:
        return None  # only the plain async-clear shape is lifted for now

    resolved = _resolve(seq.clear, inst.connections)
    if resolved is None:
        return None
    reset_net, active_low = resolved
    edge = f"negedge {reset_net}" if active_low else f"posedge {reset_net}"
    guard = f"!{reset_net}" if active_low else reset_net
    return f"posedge {clock}", edge, guard


def _resolve(expr, connections: dict[str, str]) -> tuple[str, bool] | None:
    """A one-signal condition as (net, active_low), or None if it is complex"""
    if expr[0] == "var" and expr[1] in connections:
        return connections[expr[1]], False
    if expr[0] == "not" and expr[1][0] == "var" and expr[1][1] in connections:
        return connections[expr[1][1]], True
    return None


def _next_state_nets(nl: Netlist, register: Register) -> list[str] | None:
    """The net feeding each bit's next state, LSB first"""
    by_name = {i.name: i for i in nl.instances}
    nets = []
    for flop in register.flops:
        inst = by_name[flop]
        cell = lookup(inst.cell)
        pins = variables(cell.sequential.next_state)
        if len(pins) != 1:
            return None  # a scan or enable flop: its next state is not one net
        pin = next(iter(pins))
        if pin not in inst.connections:
            return None
        nets.append(inst.connections[pin])
    return nets


def _output_nets(nl: Netlist, register: Register) -> list[str] | None:
    by_name = {i.name: i for i in nl.instances}
    nets = [
        output_net(lookup(by_name[f].cell), by_name[f].connections)
        for f in register.flops
    ]
    return None if any(n is None for n in nets) else nets


def build(nl: Netlist, analysis: Analysis) -> Lift:
    """Turn what the analysis recovered into RTL. Keep the rest as gates"""
    lift = Lift(verilog="")
    graph = Graph.of(nl)
    body: list[str] = []
    driven: set[str] = set()  # nets the lifted RTL now drives
    vectors: dict[str, str] = {}  # register name -> declared vector
    buses: dict[str, Bus] = {}  # operator name -> Bus object

    # registers:
    for register in analysis.registers:
        clocking = _edge_and_guard(nl, register)
        data = _next_state_nets(nl, register)
        outputs = _output_nets(nl, register)
        if clocking is None or data is None or outputs is None or not register.ordered:
            continue
        clock_edge, reset_edge, guard = clocking

        width = register.width
        body += [
            f"  // {describe(register)}",
            f"  reg [{width - 1}:0] {register.name};",
            f"  always @({clock_edge} or {reset_edge})",
            f"    if ({guard}) {register.name} <= {width}'d0;",
            f"    else {register.name} <= {_concat(data)};",
            f"  assign {_concat(outputs)} = {register.name};",
            "",
        ]
        lift.lifted |= set(register.flops)
        driven |= set(outputs)
        vectors[register.name] = register.name
        lift.statements.append(f"{register.name}: {describe(register)}")

    # buses
    datapath = [r for r in analysis.registers if r.name in vectors and r.width > 1]
    for bus in analysis.buses:
        if len(datapath) != 2 or bus.name not in OPERATOR_VERILOG:
            continue
        if any(bus.inverted):
            continue  # an inverted bit needs per-bit assigns; not worth it yet
        expression = (
            OPERATOR_VERILOG[bus.name]
            .replace("a", datapath[0].name)
            .replace("b", datapath[1].name)
        )
        body += [
            f"  // {bus.width}-bit {bus.name}, {bus.tier}",
            f"  wire [{bus.width - 1}:0] {bus.name} = {expression};",
            f"  assign {_concat(bus.nets)} = {bus.name};",
            "",
        ]
        produced = graph.cone(
            set(bus.nets), stop=frozenset(driven), returns="instances"
        )
        lift.lifted |= produced
        driven |= set(bus.nets)
        vectors[bus.name] = bus.name
        buses[bus.name] = bus
        lift.statements.append(f"{bus.name}: {bus.width}-bit {bus.name} ({bus.tier})")

    # predicates over the outputs
    for predicate in analysis.predicates:
        if predicate.output in driven or predicate.constant is None:
            continue
        if predicate.operator in buses:
            bus = buses[predicate.operator]
            rendered = predicate.verilog(bus.name, bus.width)
        elif len(datapath) == 2 and predicate.operator in OPERATOR_VERILOG:
            # The operator was never materialised as a bus, synthesis folded it
            # into the comparison, so state it over the registers directly
            width = max(r.width for r in datapath) + 1
            operation = (
                OPERATOR_VERILOG[predicate.operator]
                .replace("a", datapath[0].name)
                .replace("b", datapath[1].name)
            )
            rendered = f"(({operation}) == {width}'d{predicate.constant})"
        else:
            continue
        if rendered is None:
            continue
        body += [f"  assign {ident(predicate.output)} = {rendered};", ""]
        lift.lifted |= graph.cone(
            {predicate.output}, stop=frozenset(driven), returns="instances"
        )
        driven.add(predicate.output)
        lift.statements.append(predicate.text)

    lift.kept = {i.name for i in nl.instances} - lift.lifted
    lift.verilog = _render(nl, lift, body, driven, vectors)
    return lift


def _render(nl: Netlist, lift: Lift, body: list[str], driven: set[str], vectors) -> str:
    ports = sorted(nl.ports)
    gates = []
    used: set[str] = set(driven)
    for inst in nl.instances:
        if inst.name not in lift.kept:
            continue
        cell = lookup(inst.cell)
        if cell is None:
            gates.append(f"  // no library description for {inst.cell} ({inst.name})")
            continue
        pins = [
            p for p in list(cell.inputs) + list(cell.outputs) if p in inst.connections
        ]
        used |= {inst.connections[p] for p in pins}
        conns = ", ".join(f".{p}({ident(inst.connections[p])})" for p in pins)
        gates.append(f"  {generic_name(inst.cell)} {inst.name} ({conns});")

    # Only declare nets something actually refers to
    for line in body:
        used |= {
            # bus bits are named `q[0]`, so the pattern has to admit the
            # subscript or every bus net goes undeclared
            token
            for token in re.findall(r"[A-Za-z_]\w*(?:\[\d+\])?", line)
            if token in nl.nets
        }
    wires = sorted(used - set(ports) - nl.power_nets - set(vectors))

    lines = [
        "// generated by gdsx -- recovered RTL",
        "// Registers, buses and predicates below were recovered and proven;",
        "// anything still shown as a gate instance was not recognised.",
        f"module {nl.top} ({', '.join(ident(p) for p in ports)});",
    ]
    for port in ports:
        lines.append(f"  {nl.ports[port]} {ident(port)};")
    for chunk in _chunks(wires, 8):
        lines.append("  wire " + ", ".join(ident(w) for w in chunk) + ";")
    lines.append("")
    lines += body
    if gates:
        lines.append("  // not recovered -- still gates")
        lines += gates
    lines += ["", "endmodule", ""]
    return "\n".join(lines)


def prove(nl: Netlist, lift: Lift, workdir: Path) -> verify.EquivalenceResult:
    """Prove the recovered RTL equivalent to the gate netlist it came from"""
    from .netlist import to_generic_verilog

    workdir.mkdir(parents=True, exist_ok=True)
    rtl = workdir / f"{nl.top}.rtl.v"
    rtl.write_text(lift.verilog)
    gates = workdir / f"{nl.top}.generic.v"
    gates.write_text(to_generic_verilog(nl))
    return verify.equivalence(rtl, gates, nl.top, workdir, seq=4)

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from gdsx.analysis.registers import Analysis, Block, Register
from gdsx.analysis.solve import Predicate
from gdsx.core.netlist import Ref
from gdsx.core.serial import to_dict
from gdsx.fsm import StateMachine
from gdsx.guards import Guard, Guards
from gdsx.idiom import Chain, Match, Matches
from gdsx.interface import Interface, Port
from gdsx.physical.draw import Placement
from gdsx.physical.placement import Band, Ordering, Row
from gdsx.region import Box, Region
from gdsx.core.netlist import Netlist
from gdsx.sequential import Role
from gdsx.sim.state import ShiftMode
from gdsx.xref import Xref


def test_a_set_field_becomes_a_sorted_list():
    @dataclass
    class HasSet:
        members: set

    assert to_dict(HasSet({"b", "a", "c"})) == {"members": ["a", "b", "c"]}


def test_an_enum_field_becomes_its_value():
    class Kind(Enum):
        A = "a-value"

    @dataclass
    class HasEnum:
        kind: Kind

    assert to_dict(HasEnum(Kind.A)) == {"kind": "a-value"}


def test_a_path_field_becomes_a_string():
    @dataclass
    class HasPath:
        where: Path

    assert to_dict(HasPath(Path("a/b.txt"))) == {"where": "a/b.txt"}


def test_nested_dataclasses_and_a_frozen_set_are_deterministic():
    @dataclass
    class Inner:
        tags: frozenset

    @dataclass
    class Outer:
        inner: Inner
        items: list

    out = to_dict(Outer(Inner(frozenset({"z", "y"})), [Inner(frozenset({"1", "0"}))]))
    assert out == {"inner": {"tags": ["y", "z"]}, "items": [{"tags": ["0", "1"]}]}


def _round_trips(obj) -> None:
    text = json.dumps(to_dict(obj))
    assert json.loads(text) == to_dict(obj)


def test_guards_round_trip():
    guard = Guard(flop="dfrtp_1", net="en", value=1)
    result = Guards(guards=[guard], candidates=["en", "clk"], flops=["dfrtp_1"])
    _round_trips(result)


def test_region_round_trips():
    nl = Netlist(top="t")
    region = Region(
        box=Box(0.0, 0.0, 1.0, 1.0),
        inside=["a"],
        netlist=nl,
        total_cells=1,
        inputs=["a"],
        outputs=["b"],
        internal=[],
    )
    _round_trips(region)


def test_xref_round_trips():
    result = Xref(
        net="n1",
        drivers=[Ref("inv_1", "Y", "sky130_fd_sc_hd__inv_1", "output")],
        readers=[],
        port=None,
    )
    _round_trips(result)


def test_interface_round_trips():
    port = Port(name="clk", direction="input", kind="clock")
    _round_trips(Interface(inputs=[port], outputs=[]))


def test_sequential_role_round_trips():
    role = Role(register="r1", kind="counter", evidence="feedback", feeds=["r2"])
    _round_trips(role)


def test_registers_analysis_round_trips():
    reg = Register(name="r1", flops=["f1", "f2"])
    block = Block(name="r1", description="2-bit register", instances={"f1", "f2"})
    _round_trips(Analysis(registers=[reg], blocks=[block]))


def test_state_machine_round_trips():
    machine = StateMachine(
        register="r1", width=2, inputs=["go"], reset_state=0, states=[0, 1]
    )
    _round_trips(machine)


def test_placement_ordering_round_trips():
    _round_trips(Row(y=1.0, count=3))
    _round_trips(Band(axis="x", lo=0.0, hi=1.0, members=["a", "b"]))
    _round_trips(Ordering("r1", "x", 0.9, 4, 0, 6))


def test_draw_placement_round_trips():
    _round_trips(Placement(name="inv_1", x=1.0, y=2.0, cell="sky130_fd_sc_hd__inv_1"))


def test_solve_predicate_round_trips():
    _round_trips(Predicate(output="eq", operator="==", constant=3, text="a == 3"))


def test_idiom_matches_round_trip():
    match = Match(net="n1", idiom="half_adder_sum", leaves=("a", "b"))
    chain = Chain(carries=["c1", "c2"])
    _round_trips(Matches(found=[match], every=[match], considered=4))
    _round_trips(chain)


def test_shift_mode_round_trips():
    _round_trips(ShiftMode(controls={"load": 0, "rst_n": 1}, msb_first=True))

from __future__ import annotations

import json

import pytest
import rtl_fixtures
from gdsx import analyse, xref
from gdsx.netlist import Instance, Netlist

needs_yosys = pytest.mark.skipif(
    not rtl_fixtures.yosys_available(), reason="yosys not installed"
)


def chain() -> Netlist:
    """a -> inv -> n1 -> nand2 <- b, nand2 -> y"""
    nl = Netlist(top="chain", power_nets={"VGND", "VPWR"})
    nl.instances = [
        Instance("inv_1", "sky130_fd_sc_hd__inv_1", {"A": "a", "Y": "n1"}),
        Instance(
            "nand2_1", "sky130_fd_sc_hd__nand2_1", {"A": "n1", "B": "b", "Y": "y"}
        ),
        Instance("buf_1", "sky130_fd_sc_hd__buf_1", {"A": "y", "X": "z"}),
    ]
    for inst in nl.instances:
        for pin, net in inst.connections.items():
            nl.nets.setdefault(net, []).append(f"{inst.name}/{pin}")
    nl.ports = {"a": "input", "b": "input", "z": "output"}
    return nl


def test_refs_separates_drivers_from_readers():
    found = xref.refs(chain(), "n1")
    assert [r.instance for r in found.drivers] == ["inv_1"]
    assert [r.instance for r in found.readers] == ["nand2_1"]
    assert not found.undriven and not found.multiply_driven


def test_an_input_port_is_not_reported_undriven():
    assert not xref.refs(chain(), "a").undriven
    assert xref.refs(chain(), "a").port == "input"


def test_undriven_and_multiply_driven_are_flagged():
    nl = chain()
    nl.instances.append(
        Instance("inv_2", "sky130_fd_sc_hd__inv_1", {"A": "b", "Y": "y"})
    )
    nl.nets["y"].append("inv_2/Y")
    assert xref.refs(nl, "y").multiply_driven

    nl.instances.append(
        Instance("inv_3", "sky130_fd_sc_hd__inv_1", {"A": "dangling", "Y": "q"})
    )
    nl.nets["dangling"] = ["inv_3/A"]
    assert xref.refs(nl, "dangling").undriven


def test_cones_walk_in_the_right_direction():
    nl = chain()
    assert xref.fanin(nl, "y", depth=3) == [["b", "n1"], ["a"]]
    assert xref.fanout(nl, "a", depth=3) == [["n1"], ["y"], ["z"]]


@needs_yosys
def test_cones_stop_at_flops_unless_told_otherwise(tmp_path):
    nl = rtl_fixtures.from_verilog(rtl_fixtures.COUNTER, "counter", tmp_path)
    shallow = xref.fanin(nl, "q_3", depth=6, through_flops=False)
    deep = xref.fanin(nl, "q_3", depth=6, through_flops=True)
    assert sum(len(level) for level in deep) > sum(len(level) for level in shallow)


def test_a_slice_is_the_intersection_of_both_directions():
    nl = chain()
    # b reaches y and z, but nothing on the path from a to y depends on the buffer
    assert xref.between(nl, {"a"}, {"y"}) == {"inv_1", "nand2_1"}
    assert xref.between(nl, {"b"}, {"y"}) == {"nand2_1"}
    assert xref.between(nl, {"a"}, {"a"}) == set()


def test_sub_netlist_gets_its_own_ports():
    nl = chain()
    carved = xref.sub_netlist(nl, {"nand2_1"}, "just_the_nand")

    assert carved.top == "just_the_nand"
    assert [i.name for i in carved.instances] == ["nand2_1"]
    assert carved.ports["n1"] == "input"
    assert carved.ports["b"] == "input"
    assert carved.ports["y"] == "output"  # the buffer outside still reads it


def test_a_slice_round_trips_through_json():
    """One command's output has to be another command's input"""
    carved = xref.sub_netlist(chain(), {"inv_1", "nand2_1"}, "front")
    again = json.loads(json.dumps(carved.to_dict()))

    assert again == carved.to_dict()


@needs_yosys
def test_slicing_a_real_design_keeps_it_analysable(tmp_path):
    """The slice has to survive being analysed, or it is not a netlist."""

    nl = rtl_fixtures.from_verilog(rtl_fixtures.TWO_REGISTERS, "two_regs", tmp_path)
    instances = xref.between(nl, {"a_in", "b_in"}, {"eq"})
    carved = xref.sub_netlist(nl, instances)

    assert 0 < len(carved.instances) <= len(nl.instances)
    registers = analyse.find_registers(carved)
    assert sum(r.width for r in registers) == 8  # both four-bit registers survive

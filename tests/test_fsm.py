"""State-machine recovery by reachability"""

import pytest

import rtl_fixtures
from gdsx import analyse, fsm

needs_yosys = pytest.mark.skipif(
    not rtl_fixtures.yosys_available(), reason="yosys not installed"
)


def machines_of(source, top, tmp_path):
    nl = rtl_fixtures.from_verilog(source, top, tmp_path)
    return nl, fsm.find_state_machines(nl, analyse.find_registers(nl))


@needs_yosys
def test_traffic_light_is_recovered(tmp_path):
    _, machines = machines_of(rtl_fixtures.TRAFFIC, "traffic", tmp_path)
    (machine,) = machines
    assert len(machine.states) == 3
    assert machine.density < 0.5  # a sparse encoding, as control logic is

    reset, green, amber = machine.states
    assert reset == machine.reset_state

    # RED holds while req is low and advances when it is high
    assert machine.transitions[(reset, 0)] == reset
    assert machine.transitions[(reset, 1)] == green
    # the other two states advance unconditionally
    assert machine.successors(green) == {amber}
    assert machine.successors(amber) == {reset}


@needs_yosys
def test_outputs_are_attributed_to_states(tmp_path):
    """go and warn are Moore outputs: constant throughout each state"""
    nl, (machine,) = machines_of(rtl_fixtures.TRAFFIC, "traffic", tmp_path)
    reset, green, amber = machine.states
    assert machine.moore_outputs[reset] == {"go": 0, "warn": 0}
    assert machine.moore_outputs[green] == {"go": 1, "warn": 0}
    assert machine.moore_outputs[amber] == {"go": 0, "warn": 1}


@needs_yosys
def test_reset_line_is_held_inactive_during_exploration(tmp_path):
    """Undriven inputs default to 0, which asserts an active-low reset"""
    nl = rtl_fixtures.from_verilog(rtl_fixtures.TRAFFIC, "traffic", tmp_path)
    register = analyse.find_registers(nl)[0]
    assert fsm.quiescent(nl, register) == {"rst_n": 1}
    # and the reset line is not enumerated as a transition input
    assert "rst_n" not in fsm.explore(nl, register).inputs


@needs_yosys
def test_data_registers_are_not_reported_as_state_machines(tmp_path):
    """A shift register reaches every one of its states"""
    for source, top in (
        (rtl_fixtures.SHIFT_REGISTER, "shifter"),
        (rtl_fixtures.COUNTER, "counter"),
        (rtl_fixtures.PARALLEL_LOAD, "parallel_load"),
    ):
        _, machines = machines_of(source, top, tmp_path)
        assert machines == [], f"{top} was mistaken for control logic"


def test_the_sample_design_has_no_control_logic(sample_netlist):
    """It is pure datapath. the tool should not report an FSM"""
    registers = analyse.find_registers(sample_netlist)
    assert fsm.find_state_machines(sample_netlist, registers) == []


@needs_yosys
def test_table_renders(tmp_path):
    _, (machine,) = machines_of(rtl_fixtures.TRAFFIC, "traffic", tmp_path)
    table = fsm.to_table(machine)
    assert "3-state machine" in table
    assert "(reset)" in table
    assert "req" in table
    assert table.count("->") == 2 * len(machine.states)  # one per input combination

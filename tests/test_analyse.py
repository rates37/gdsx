import pytest
import rtl_fixtures
from gdsx import analyse
from gdsx.functions import is_sequential
from gdsx.sim import Simulator

needs_yosys = pytest.mark.skipif(
    not rtl_fixtures.yosys_available(), reason="yosys not installed"
)


def registers_of(source, top, tmp_path):
    return analyse.find_registers(rtl_fixtures.from_verilog(source, top, tmp_path))


@needs_yosys
def test_counter_is_one_register_not_eight_flops(tmp_path):
    (reg,) = registers_of(rtl_fixtures.COUNTER, "counter", tmp_path)
    assert reg.width == 8
    assert reg.kind == "feedback register"
    assert reg.ordered


@needs_yosys
def test_accumulator_groups(tmp_path):
    (reg,) = registers_of(rtl_fixtures.ACCUMULATOR, "accumulator", tmp_path)
    assert reg.width == 4
    assert reg.kind == "feedback register"


@needs_yosys
def test_parallel_load_groups_but_admits_it_cannot_order(tmp_path):
    (reg,) = registers_of(rtl_fixtures.PARALLEL_LOAD, "parallel_load", tmp_path)
    assert reg.width == 8
    assert reg.kind == "parallel register"
    assert not reg.ordered
    assert "bit order unknown" in reg.description


@needs_yosys
def test_synthesised_shift_register_matches_the_extracted_one(tmp_path):
    (reg,) = registers_of(rtl_fixtures.SHIFT_REGISTER, "shifter", tmp_path)
    assert reg.width == 8
    assert reg.kind == "shift register"
    assert reg.ordered and reg.serial_input == "si"


@needs_yosys
def test_registers_sharing_control_are_still_split(tmp_path):
    regs = registers_of(rtl_fixtures.TWO_REGISTERS, "two_regs", tmp_path)
    assert [r.width for r in regs] == [4, 4]
    assert {r.serial_input for r in regs} == {"a_in", "b_in"}


@needs_yosys
def test_operator_identified_on_a_synthesised_design(tmp_path):
    """End to end on a design the tool has never seen: 4-bit sum == 20."""
    nl = rtl_fixtures.from_verilog(rtl_fixtures.TWO_REGISTERS, "two_regs", tmp_path)
    result = analyse.analyse(nl)
    assert result.operators == ["eq = (reg_a_in + reg_b_in == 20)"]


def test_two_eight_bit_shift_registers_are_recovered(sample_netlist):
    registers = analyse.find_registers(sample_netlist)
    assert len(registers) == 2
    assert {r.name for r in registers} == {"reg_A", "reg_B"}
    for reg in registers:
        assert reg.width == 8
        assert reg.serial_input in ("A", "B")
        assert len(set(reg.flops)) == 8  # no FF counted twice


def test_registers_cover_every_flop(sample_netlist):
    flops = {i.name for i in sample_netlist.instances if is_sequential(i.cell)}
    grouped = {f for r in analyse.find_registers(sample_netlist) for f in r.flops}
    assert grouped == flops


def test_operator_is_identified(sample_netlist):
    result = analyse.analyse(sample_netlist)
    assert result.operators == ["S = (reg_A + reg_B == 496)"]
    assert result.notes == []


def test_shift_mode_is_discovered(sample_netlist):
    registers = [r for r in analyse.find_registers(sample_netlist) if r.serial_input]
    mode = analyse.find_shift_mode(sample_netlist, registers)
    assert mode is not None
    assert mode.controls == {"en": 1, "rst_n": 1}
    assert mode.msb_first


def test_solutions_are_verified_by_simulation(sample_netlist):
    mode, solutions = analyse.solve(sample_netlist, "S", limit=5)
    assert mode is not None
    assert len(solutions) == 5
    for values, verified in solutions:
        assert sum(values) == 496
        assert verified


# bus discovery with no hypothesis about what the design does

WIDE = """
module wide(input clk, input rst_n, input a_in, input b_in, output [12:0] s);
  reg [11:0] a, b;
  always @(posedge clk or negedge rst_n)
    if (!rst_n) begin a <= 0; b <= 0; end
    else begin a <= {a[10:0], a_in}; b <= {b[10:0], b_in}; end
  assign s = a + b;
endmodule
"""

XOR_DESIGN = """
module xorreg(input clk, input rst_n, input a_in, input b_in, output [3:0] z);
  reg [3:0] a, b;
  always @(posedge clk or negedge rst_n)
    if (!rst_n) begin a <= 0; b <= 0; end
    else begin a <= {a[2:0], a_in}; b <= {b[2:0], b_in}; end
  assign z = a ^ b;
endmodule
"""


def buses_of(nl, inputs):
    registers = [r for r in analyse.find_registers(nl) if r.width > 1]
    return analyse.find_buses(nl, Simulator(nl), registers, inputs)


def test_sum_bus_found_without_being_told_to_look_for_a_sum(sample_netlist):
    (bus,) = buses_of(sample_netlist, {"en": 1, "rst_n": 1})
    assert (bus.name, bus.width) == ("sum", 9)
    assert bus.proven


def test_adder_internals_are_not_reported_as_separate_operators(sample_netlist):
    """A ripple adder does compute a&b and a^b, but as intermediates"""
    registers = [r for r in analyse.find_registers(sample_netlist) if r.width > 1]
    every = analyse.find_buses(
        sample_netlist,
        Simulator(sample_netlist),
        registers,
        {"en": 1, "rst_n": 1},
        min_width=2,
    )
    assert [b.name for b in every] == ["sum"]


@needs_yosys
def test_a_non_adder_is_identified_as_itself(tmp_path):
    nl = rtl_fixtures.from_verilog(XOR_DESIGN, "xorreg", tmp_path)
    (bus,) = buses_of(nl, {})
    assert bus.name == "xor" and bus.width == 4


@needs_yosys
def test_bus_discovery_works_past_the_enumeration_ceiling(tmp_path):
    """24 bits of state: inefficient to sweep, but SAT will be fine"""
    nl = rtl_fixtures.from_verilog(WIDE, "wide", tmp_path)
    (bus,) = buses_of(nl, {})
    assert (bus.name, bus.width) == ("sum", 13)
    assert bus.tier == "sat"


@needs_yosys
def test_bus_is_proven_by_sat_not_enumeration(sample_netlist, tmp_path):
    """The cone really is handed to yosys and really does come back proven"""
    registers = [r for r in analyse.find_registers(sample_netlist) if r.width > 1]
    (bus,) = buses_of(sample_netlist, {"en": 1, "rst_n": 1})
    assert bus.tier == "sat"
    assert analyse.prove_bus(sample_netlist, bus, registers, tmp_path)


@needs_yosys
def test_sat_rejects_a_bus_that_is_not_what_it_claims(sample_netlist, tmp_path):
    """Negative control: mislabel the operator and the proof must fail"""
    registers = [r for r in analyse.find_registers(sample_netlist) if r.width > 1]
    (bus,) = buses_of(sample_netlist, {"en": 1, "rst_n": 1})
    bus.name = "xor"  # the sum bus is emphatically not a bitwise xor
    assert not analyse.prove_bus(sample_netlist, bus, registers, tmp_path)


@needs_yosys
def test_falls_back_to_the_sweep_without_yosys(sample_netlist, monkeypatch):
    """With no prover confirmation still happens by enumeration"""
    monkeypatch.setattr("gdsx.verify.available", lambda: False)
    (bus,) = buses_of(sample_netlist, {"en": 1, "rst_n": 1})
    assert bus.tier == "exhaustive"
    assert bus.proven


def test_sum_bus_is_recovered(sample_netlist):
    registers = [r for r in analyse.find_registers(sample_netlist) if r.width > 1]
    bus, adder, comparator = analyse.split_datapath(
        sample_netlist, registers, "S", {"en": 1, "rst_n": 1}
    )
    assert bus.width == 9  # 8-bit adder operands -> 9-bit sum
    assert len(set(bus.nets)) == 9
    assert not any(bus.inverted)
    assert len(adder) == 41
    # 496 == 9'b111110000 so equality is two and4bb plus one and3
    assert len(comparator) == 3
    assert not (adder & comparator)


def test_blocks_account_for_every_cell(sample_netlist):
    result = analyse.analyse(sample_netlist)
    covered = [i for b in result.blocks for i in b.instances]
    assert len(covered) == len(set(covered)), "a cell was claimed by two blocks"
    assert set(covered) == {i.name for i in sample_netlist.instances}


def test_blocks_are_named(sample_netlist):
    result = analyse.analyse(sample_netlist)
    described = {b.name: b.description for b in result.blocks}
    assert described["reg_A"].startswith("8-bit shift register")
    assert described["sum"].startswith("9-bit adder")
    assert "comparator" in described["cmp_S"]


def test_support_stops_at_flops_and_ports(sample_netlist):
    deps = analyse.support(sample_netlist, "S")
    flops = {d for d in deps if d.startswith("dfrtp")}
    assert len(flops) == 16
    assert deps - flops == set()

import pytest
import rtl_fixtures
from gdsx import analyse
from gdsx.functions import is_sequential

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

from gdsx import analyse
from gdsx.functions import is_sequential


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


def test_support_stops_at_flops_and_ports(sample_netlist):
    deps = analyse.support(sample_netlist, "S")
    flops = {d for d in deps if d.startswith("dfrtp")}
    assert len(flops) == 16
    assert deps - flops == set()

"""RTL lifting, and the proof that the lift is faithful."""

import pytest

import rtl_fixtures
from gdsx import analyse, lift, verify

needs_yosys = pytest.mark.skipif(not verify.available(), reason="yosys not installed")


def lift_of(nl):
    return lift.build(nl, analyse.analyse(nl))


def test_sample_lifts_registers_bus_and_predicate(sample_netlist):
    result = lift_of(sample_netlist)
    assert result.statements == [
        "reg_A: 8-bit shift register",
        "reg_B: 8-bit shift register",
        "sum: 9-bit sum (sat)",
        "S = (reg_A + reg_B == 496)",
    ]
    assert "reg [7:0] reg_A;" in result.verilog
    assert "wire [8:0] sum = reg_A + reg_B;" in result.verilog
    assert "assign S = (sum == 9'd496);" in result.verilog


def test_lift_is_a_hybrid_not_an_all_or_nothing(sample_netlist):
    """What was not recovered stays as gates rather than being dropped"""
    result = lift_of(sample_netlist)
    assert result.lifted and result.kept
    assert result.lifted & result.kept == set()
    assert len(result.lifted) + len(result.kept) == len(sample_netlist.instances)
    for name in sorted(result.kept)[:5]:
        assert f" {name} (" in result.verilog  # still instantiated
    for name in sorted(result.lifted)[:5]:
        assert f" {name} (" not in result.verilog  # replaced by RTL


def test_reset_polarity_comes_from_liberty(sample_netlist):
    """dfrtp resets on a low RESET_B, so the lift must say negedge"""
    assert "negedge rst_n" in lift_of(sample_netlist).verilog
    assert "if (!rst_n)" in lift_of(sample_netlist).verilog


@needs_yosys
def test_lifted_rtl_is_proven_equivalent(sample_netlist, tmp_path):
    """Proof it is the same circuit"""
    result = lift_of(sample_netlist)
    proof = lift.prove(sample_netlist, result, tmp_path)
    assert proof.proven, proof.summary
    assert "Induction step proven" in proof.log


@needs_yosys
def test_a_wrong_lift_is_rejected(sample_netlist, tmp_path):
    """Negative control: the proof must be able to fail if not the same"""
    result = lift_of(sample_netlist)
    result.verilog = result.verilog.replace("9'd496", "9'd495")
    assert not lift.prove(sample_netlist, result, tmp_path).proven


@needs_yosys
def test_full_recovery_when_everything_is_recognised(tmp_path):
    """two_regs has nothing but registers and a predicate, so nothing is left"""
    nl = rtl_fixtures.from_verilog(rtl_fixtures.TWO_REGISTERS, "two_regs", tmp_path)
    result = lift_of(nl)
    assert result.kept == set()
    assert result.coverage == 1.0
    assert lift.prove(nl, result, tmp_path).proven


@needs_yosys
def test_partial_recovery_still_proves(tmp_path):
    """A counter's register lifts, its increment logic does not"""
    nl = rtl_fixtures.from_verilog(rtl_fixtures.COUNTER, "counter", tmp_path)
    result = lift_of(nl)
    assert 0 < result.coverage < 1
    assert "feedback register" in result.verilog
    assert lift.prove(nl, result, tmp_path).proven


@needs_yosys
def test_nothing_recovered_still_produces_a_valid_netlist(tmp_path):
    """Bits with no recoverable order are not lifted. output must still be sound"""
    nl = rtl_fixtures.from_verilog(
        rtl_fixtures.PARALLEL_LOAD, "parallel_load", tmp_path
    )
    result = lift_of(nl)
    assert result.coverage == 0.0
    assert lift.prove(nl, result, tmp_path).proven


def test_emitted_rtl_declares_every_net_it_uses(sample_netlist):
    """An undeclared net becomes an implicit wire and silently changes meaning"""
    text = lift_of(sample_netlist).verilog
    declared = set()
    for line in text.splitlines():
        stripped = line.strip()
        for prefix in ("wire ", "input ", "output ", "reg "):
            if stripped.startswith(prefix):
                body = stripped[len(prefix) :].split("=")[0].rstrip(";")
                declared |= {n.strip() for n in body.replace("[8:0]", "").split(",")}
    for net in sample_netlist.nets:
        if net in sample_netlist.power_nets:
            continue
        if f"({net})" in text or f" {net}," in text:
            assert net in declared or net in sample_netlist.ports, f"{net} undeclared"

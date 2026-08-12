"""Formal equivalence against the reference RTL
Needs yosys on PATH"""

import shutil
from pathlib import Path

import pytest

from gdsx import analyse, lift, liberty, netlist, primitives, verify

REFERENCE = Path(__file__).resolve().parents[1] / "samples" / "sample.v"

needs_yosys = pytest.mark.skipif(
    shutil.which("yosys") is None, reason="yosys not installed"
)


def test_primitives_cover_every_generic_used(sample_netlist):
    text = primitives.verilog()
    for line in netlist.to_generic_verilog(sample_netlist).splitlines():
        if line.startswith("  ") and "(" in line and not line.startswith("  wire"):
            generic = line.strip().split()[0]
            assert f"module {generic} (" in text


def test_every_describable_cell_emits_a_module():
    text = primitives.verilog()
    describable = [c for c in liberty.library().values() if c.has_behaviour]
    assert len(describable) > 100
    for cell in describable:
        assert f"assign {sorted(cell.functions)[0]} =" in text or cell.is_sequential


@needs_yosys
def test_extracted_netlist_is_equivalent_to_the_reference(sample_netlist, tmp_path):
    out = tmp_path / "verify"
    generic = next(
        p
        for p in netlist.write_all(sample_netlist, out)
        if p.name.endswith(".generic.v")
    )
    result = verify.equivalence(generic, REFERENCE, sample_netlist.top, out)
    assert result.proven, result.summary
    assert "Induction step proven" in result.log


@needs_yosys
def test_a_wrong_reference_is_rejected(sample_netlist, tmp_path):
    out = tmp_path / "wrong"
    out.mkdir(parents=True)
    bad = out / "wrong.v"
    bad.write_text(REFERENCE.read_text().replace("9'd496", "9'd495"))

    generic = next(
        p
        for p in netlist.write_all(sample_netlist, out)
        if p.name.endswith(".generic.v")
    )
    result = verify.equivalence(generic, bad, sample_netlist.top, out)
    assert not result.proven


@needs_yosys
def test_structural_equivalence_proves_a_netlist_against_itself(
    sample_netlist, tmp_path
):
    """Two designs that share their internal points"""
    nl = sample_netlist
    paths = netlist.write_all(nl, tmp_path / "out")
    generic = next(p for p in paths if p.name.endswith(".generic.v"))

    result = verify.structural_equivalence(generic, generic, nl.top, tmp_path / "work")
    assert result.proven, result.log
    assert (
        "0 are unproven" in result.log
    )  # every cut point actually resolved not just skipped


@needs_yosys
def test_structural_equivalence_rejects_a_local_mismatch(sample_netlist, tmp_path):
    """One wrong gate, same wiring everywhere else"""
    generic = next(
        p
        for p in netlist.write_all(sample_netlist, tmp_path / "out")
        if p.name.endswith(".generic.v")
    )
    text = generic.read_text()
    assert "AND2 and2_2_1 (" in text
    corrupted = tmp_path / "corrupted.generic.v"
    corrupted.write_text(text.replace("AND2 and2_2_1 (", "OR2 and2_2_1 (", 1))

    result = verify.structural_equivalence(
        corrupted, generic, sample_netlist.top, tmp_path / "work"
    )
    assert not result.proven
    assert "are unproven" in result.log or "FAIL" in result.log


@needs_yosys
def test_structural_equivalence_proves_a_real_lift_against_its_gates(
    sample_netlist, tmp_path
):
    """Intended use: a lift.py rung checked against the netlist it came from"""
    result = lift.build(sample_netlist, analyse.analyse(sample_netlist))
    assert result.lifted, "the lift produced no RTL to check against"

    workdir = tmp_path / "lifted"
    workdir.mkdir()
    rtl = workdir / f"{sample_netlist.top}.rtl.v"
    rtl.write_text(result.verilog)
    gates = workdir / f"{sample_netlist.top}.generic.v"
    gates.write_text(netlist.to_generic_verilog(sample_netlist))

    proof = verify.structural_equivalence(
        rtl, gates, sample_netlist.top, workdir / "work"
    )
    assert proof.proven, proof.log


@needs_yosys
def test_structural_equivalence_rejects_a_wrong_lift(sample_netlist, tmp_path):
    """Same setup as above but the lifted RTL's constant is wrong"""
    result = lift.build(sample_netlist, analyse.analyse(sample_netlist))
    assert "9'd496" in result.verilog, "test assumes the sample's known constant"

    workdir = tmp_path / "wrong_lift"
    workdir.mkdir()
    rtl = workdir / f"{sample_netlist.top}.rtl.v"
    rtl.write_text(result.verilog.replace("9'd496", "9'd495"))
    gates = workdir / f"{sample_netlist.top}.generic.v"
    gates.write_text(netlist.to_generic_verilog(sample_netlist))

    proof = verify.structural_equivalence(
        rtl, gates, sample_netlist.top, workdir / "work"
    )
    assert not proof.proven

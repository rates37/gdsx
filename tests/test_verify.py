"""Formal equivalence against the reference RTL
Needs yosys on PATH"""

import shutil
from pathlib import Path

import pytest

from gdsx import netlist, primitives, verify
from gdsx.functions import COMBINATIONAL

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


def test_minterm_expansion_is_exhaustive():
    for fn in COMBINATIONAL.values():
        assert primitives._minterms(fn) not in ("", None)


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

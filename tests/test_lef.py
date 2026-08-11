from pathlib import Path

import fixtures
import pytest

from gdsx import analyse, lef, loader, netlist
from gdsx.connectivity import trace
from gdsx.pins import PinOracle, abstract_shapes, bond_pins

LEF_FILE = Path(__file__).resolve().parents[1] / "samples" / "sample_cells.lef"


@pytest.fixture(scope="session")
def macros():
    return lef.read(LEF_FILE)


@pytest.fixture(scope="session")
def abstract(tmp_path_factory, tech, macros):
    """The sample with every library cell emptied -- pins must come from LEF."""
    path = fixtures.abstract(tmp_path_factory.mktemp("abstract") / "abstract.gds")
    design = loader.load(path, tech)
    return design, netlist.build(design, macros=macros)


# the parser, against real PDK LEF


def test_parses_real_pdk_lef(macros):
    assert len(macros) == 18
    nand2 = macros["sky130_fd_sc_hd__nand2_2"]
    assert (nand2.width, nand2.height) == (2300, 2720)  # microns -> dbu
    assert set(nand2.pins) == {"A", "B", "Y", "VGND", "VPWR"}


def test_pin_directions_and_geometry(macros):
    nand2 = macros["sky130_fd_sc_hd__nand2_2"]
    assert nand2.pins["A"].direction == "input"
    assert nand2.pins["Y"].direction == "output"
    assert nand2.pins["VPWR"].direction == "power"  # from USE POWER, not the name

    rect = nand2.pins["A"].rects[0]
    assert (rect.layer, rect.left, rect.bottom, rect.right, rect.top) == (
        "li1",
        1015,
        1075,
        1765,
        1325,
    )
    assert rect.center == (1390, 1200)


def test_a_pin_may_have_several_rectangles(macros):
    """Y is one node spread over five rectangles -- they must all be kept."""
    assert len(macros["sky130_fd_sc_hd__nand2_2"].pins["Y"].rects) == 5


def test_obstructions_and_unknown_sections_are_ignored():
    text = """
    MACRO cell
      SIZE 1.0 BY 2.0 ;
      PIN A
        DIRECTION INPUT ;
        PORT
          LAYER li1 ;
            RECT 0.1 0.2 0.3 0.4 ;
        END
      END A
      OBS
        LAYER met1 ;
          RECT 0.0 0.0 1.0 2.0 ;
      END
    END cell
    """
    (cell,) = lef.parse(text).values()
    assert set(cell.pins) == {"A"}  # the OBS rectangle is not a pin
    assert len(cell.pins["A"].rects) == 1


# extraction from an abstract design


def test_oracle_falls_back_to_lef(abstract):
    design, _ = abstract
    oracle = PinOracle(design, lef.read(LEF_FILE, design.dbu))
    pins = oracle.pins("sky130_fd_sc_hd__nand2_2")
    assert {p.name for p in pins} == {"A", "B", "Y", "VGND", "VPWR"}
    assert oracle.source["sky130_fd_sc_hd__nand2_2"] == "lef"


def test_self_contained_design_still_prefers_its_own_labels(sample, macros):
    """First hit wins: the GDS knows better than an abstract."""
    oracle = PinOracle(sample, macros)
    oracle.pins("sky130_fd_sc_hd__nand2_2")
    assert oracle.source["sky130_fd_sc_hd__nand2_2"] == "gds"
    assert oracle.rects("sky130_fd_sc_hd__nand2_2") == []  # no injection needed


def test_inspect_reports_the_source(abstract, macros):
    design, _ = abstract
    assert loader.inspect(design, macros).pin_source == "LEF abstracts"
    assert loader.inspect(design).pin_source == "unavailable"
    assert not loader.inspect(design).self_contained


def test_abstract_extraction_matches_the_self_contained_one(abstract, sample_netlist):
    """The whole point of N7: same design, same netlist, pins from LEF."""
    _, nl = abstract
    assert len(nl.instances) == len(sample_netlist.instances)
    assert nl.floating == [] and nl.conflicts == []

    partition = {frozenset(refs) for refs in nl.nets.values()}
    expected = {frozenset(refs) for refs in sample_netlist.nets.values()}
    assert partition == expected


def test_abstract_ports_and_power_are_recovered(abstract, sample_netlist):
    _, nl = abstract
    assert nl.ports == sample_netlist.ports
    assert nl.power_nets == sample_netlist.power_nets == {"VPWR", "VGND"}


def test_multi_rect_pins_are_bonded(abstract):
    """Without bonding, a pin drawn as several rects looks like several nets"""
    design, nl = abstract
    oracle = PinOracle(design, lef.read(LEF_FILE, design.dbu))


    conn = trace(design, abstract_shapes(design, oracle))
    assert bond_pins(design, conn, oracle) > 0
    assert nl.conflicts == []


def test_the_analysis_runs_on_the_abstract_design(abstract):
    _, nl = abstract
    result = analyse.analyse(nl)
    assert result.operators == ["S = (reg_A + reg_B == 496)"]
    assert {r.name for r in result.registers} == {"reg_A", "reg_B"}


def test_instance_names_are_stable_across_a_gds_rewrite(abstract, sample_netlist):
    """Two cells share a placement point here; the transform must break the tie."""
    _, nl = abstract
    assert [i.name for i in nl.instances] == [i.name for i in sample_netlist.instances]

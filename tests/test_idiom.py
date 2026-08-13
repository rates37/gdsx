from __future__ import annotations

import pytest
import rtl_fixtures
from gdsx import idiom, synth
from gdsx.netlist import Instance, Netlist

needs_yosys = pytest.mark.skipif(not synth.yosys_available(), reason="yosys not installed")

AND2, OR2, XOR2, NAND2 = 0b1000, 0b1110, 0b0110, 0b0111
MAJORITY3 = idiom.table_of(3, lambda a, b, c: a + b + c >= 2)
XOR3 = idiom.table_of(3, lambda a, b, c: a ^ b ^ c)
AND3 = idiom.table_of(3, lambda a, b, c: a & b & c)


def test_npn_collapses_negation_and_permutation():
    assert idiom.npn(AND2, 2) == idiom.npn(NAND2, 2) == idiom.npn(OR2, 2)
    assert idiom.npn(XOR2, 2) != idiom.npn(AND2, 2)
    assert idiom.npn(MAJORITY3, 3) != idiom.npn(AND3, 3) != idiom.npn(XOR3, 3)


def test_npn_is_invariant_to_input_order():
    mux = idiom.table_of(3, lambda a, b, s: b if s else a)
    swapped = idiom.permute(mux, 3, (2, 1, 0))
    assert idiom.npn(mux, 3) == idiom.npn(swapped, 3)


def test_the_library_names_classes_and_not_gates():
    for width, _ in idiom.library():
        assert width >= 3
    # definitions that share a canonical form share a name
    names = list(idiom.library().values())
    assert any(" / " in name for name in names)


def full_adder() -> Netlist:
    """One full adder out of two half adders"""
    nl = Netlist(top="fa", power_nets={"VGND", "VPWR"})
    nl.instances = [
        Instance("xor2_1", "sky130_fd_sc_hd__xor2_1", {"A": "a", "B": "b", "X": "p"}),
        Instance("xor2_2", "sky130_fd_sc_hd__xor2_1", {"A": "p", "B": "cin", "X": "sum"}),
        Instance("and2_1", "sky130_fd_sc_hd__and2_1", {"A": "a", "B": "b", "X": "g"}),
        Instance("and2_2", "sky130_fd_sc_hd__and2_1", {"A": "p", "B": "cin", "X": "pc"}),
        Instance("or2_1", "sky130_fd_sc_hd__or2_1", {"A": "g", "B": "pc", "X": "cout"}),
    ]
    for inst in nl.instances:
        for pin, net in inst.connections.items():
            nl.nets.setdefault(net, []).append(f"{inst.name}/{pin}")
    nl.ports = {"a": "input", "b": "input", "cin": "input", "sum": "output", "cout": "output"}
    return nl


def test_a_truth_table_is_evaluated_through_the_library():
    nl = full_adder()
    assert idiom.evaluate_cone(nl, "sum", ["a", "b", "cin"]) == XOR3
    assert idiom.evaluate_cone(nl, "cout", ["a", "b", "cin"]) == MAJORITY3


def test_a_full_adder_is_recognised_however_it_is_built():
    matches = {m.net: m.idiom for m in idiom.match(full_adder()).found}
    assert matches["sum"] == "adder sum / parity"
    assert matches["cout"] == "adder carry (majority)"


def test_cuts_are_bounded_and_include_the_leaves():
    found = idiom.cuts(full_adder(), limit=3)
    assert all(len(cut) <= 3 for cuts in found.values() for cut in cuts)
    assert frozenset({"a", "b", "cin"}) in found["sum"]
    assert found["a"] == [frozenset({"a"})]  # a port is its own only cut


@needs_yosys
def test_an_adder_is_found_as_a_carry_chain(tmp_path):
    source = """
    module wide(input [11:0] a, input [11:0] b, output [12:0] s);
      assign s = a + b;
    endmodule
    """
    nl = rtl_fixtures.from_verilog(source, "wide", tmp_path)
    chains = idiom.carry_chains(idiom.match(nl))
    assert chains, "no carry chain found in a twelve-bit adder"
    assert max(chain.width for chain in chains) >= 6


@needs_yosys
def test_matching_survives_resynthesis(tmp_path):
    source = """
    module small(input [3:0] a, input [3:0] b, output [4:0] s);
      assign s = a + b;
    endmodule
    """
    widths = []
    for recipe in (synth.RECIPES[0], synth.RECIPES[4]):  # default and nand-only
        nl = rtl_fixtures.from_verilog(source, "small", tmp_path / recipe.name, recipe)
        chains = idiom.carry_chains(idiom.match(nl))
        widths.append(max((chain.width for chain in chains), default=0))

    # The carry chain survives both mappings. The *count* of matched adder bits
    # does not -- a nand-only mapping leaves fewer nets where a three-input
    # function is visible at all -- which is worth knowing and is why the chain,
    # not the tally, is the claim worth making.
    assert all(width >= 3 for width in widths), widths


@needs_yosys
def test_a_wider_cut_never_finds_less(tmp_path):
    source = """
    module small(input [3:0] a, input [3:0] b, output [4:0] s);
      assign s = a + b;
    endmodule
    """
    nl = rtl_fixtures.from_verilog(source, "small", tmp_path)
    narrow = sum(1 for m in idiom.match(nl, 4).found if m.idiom.startswith("adder"))
    wide = sum(1 for m in idiom.match(nl, 5).found if m.idiom.startswith("adder"))
    assert wide >= narrow
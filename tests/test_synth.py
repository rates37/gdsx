from __future__ import annotations

import pytest
import rtl_fixtures
from gdsx import synth

needs_yosys = pytest.mark.skipif(
    not synth.yosys_available(), reason="yosys not installed"
)

HIERARCHICAL = """
module adder8(input [7:0] a, input [7:0] b, output [8:0] s);
  assign s = a + b;
endmodule
module ctl(input clk, input rst_n, input go, output reg busy);
  always @(posedge clk or negedge rst_n)
    if (!rst_n) busy <= 1'b0; else busy <= go & ~busy;
endmodule
module top(input clk, input rst_n, input go, input [7:0] a, input [7:0] b,
           output [8:0] s, output busy);
  adder8 u_add(.a(a), .b(b), .s(s));
  ctl    u_ctl(.clk(clk), .rst_n(rst_n), .go(go), .busy(busy));
endmodule
"""


@needs_yosys
def test_every_recipe_produces_a_netlist(tmp_path):
    """The point of the recipes is that they differ but all must be mappable"""
    sizes = {}
    for recipe in synth.RECIPES:
        nl = synth.from_verilog(rtl_fixtures.COUNTER, "counter", tmp_path, recipe)
        assert nl.instances
        sizes[recipe.name] = len(nl.instances)
    assert len(set(sizes.values())) > 1, f"recipes gave identical netlists: {sizes}"


@needs_yosys
def test_nonzero_reset_produces_dfstp(tmp_path):
    """4'b1010: bits 1 and 3 set, bits 0 and 2 reset -- two of each flop kind"""
    nl = synth.from_verilog(rtl_fixtures.NONZERO_RESET, "nonzero_reset", tmp_path)
    cells = [inst.cell for inst in nl.instances]
    assert cells.count("sky130_fd_sc_hd__dfstp_2") == 2
    assert cells.count("sky130_fd_sc_hd__dfrtp_2") == 2


@needs_yosys
def test_cell_names_really_are_gone(tmp_path):
    """Flattening a hierarchical design loses the module boundaries as expected"""
    design = synth.synthesize(HIERARCHICAL, "top", tmp_path)
    cells = design["modules"]["top"]["cells"]
    named = [n for n, c in cells.items() if c.get("attributes", {}).get("module")]
    assert len(named) < len(cells) // 2

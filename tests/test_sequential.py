from __future__ import annotations

from pathlib import Path
import pytest
import rtl_fixtures
from gdsx import analyse, sequential, synth

needs_yosys = pytest.mark.skipif(
    not synth.yosys_available(), reason="yosys not installed"
)

DESIGNS = Path(__file__).resolve().parent / "designs"

PIPELINE = """
module pipe(input clk, input rst_n, input [7:0] d, output [7:0] q);
  reg [7:0] a, b, c;
  always @(posedge clk or negedge rst_n)
    if (!rst_n) begin a <= 0; b <= 0; c <= 0; end
    else begin a <= d; b <= a; c <= b; end
  assign q = c;
endmodule
"""

ACCUMULATOR = """
module acc(input clk, input rst_n, input [7:0] d, output [7:0] q);
  reg [7:0] total;
  always @(posedge clk or negedge rst_n)
    if (!rst_n) total <= 0; else total <= total + d;
  assign q = total;
endmodule
"""


def roles_of(source: str, top: str, tmp_path) -> dict[str, str]:
    nl = rtl_fixtures.from_verilog(source, top, tmp_path)
    registers = analyse.resolve_bit_order(nl, analyse.find_registers(nl))
    return {role.register: role.kind for role in sequential.classify(nl, registers)}


@needs_yosys
def test_a_counter_is_a_counter(tmp_path):
    kinds = roles_of(rtl_fixtures.COUNTER, "counter", tmp_path)
    assert set(kinds.values()) == {"counter"}


@needs_yosys
def test_an_accumulator_is_told_from_a_counter(tmp_path):
    assert set(roles_of(ACCUMULATOR, "acc", tmp_path).values()) == {"accumulator"}


@needs_yosys
def test_a_crc_is_an_lfsr_and_not_a_counter(tmp_path):
    """An adder's sum bit and a parity tree are the same NPN class"""
    kinds = roles_of((DESIGNS / "crc16.v").read_text(), "crc16", tmp_path)
    assert set(kinds.values()) == {"LFSR"}


@needs_yosys
def test_a_shift_register_is_not_an_lfsr(tmp_path):
    kinds = roles_of(rtl_fixtures.SHIFT_REGISTER, "shifter", tmp_path)
    assert set(kinds.values()) == {"shift register"}


@needs_yosys
def test_cordic_comes_out_as_a_counter_and_two_accumulators(tmp_path):
    kinds = roles_of((DESIGNS / "cordic.v").read_text(), "cordic", tmp_path)
    assert kinds["step"] == "counter"
    assert kinds["x"] == kinds["y"] == "accumulator"


@needs_yosys
def test_a_narrow_counter_is_still_a_counter(tmp_path):
    source = """
    module tiny(input clk, input rst_n, input en, output [2:0] q);
      reg [2:0] c;
      always @(posedge clk or negedge rst_n)
        if (!rst_n) c <= 0; else if (en) c <= c + 1;
      assign q = c;
    endmodule
    """
    assert set(roles_of(source, "tiny", tmp_path).values()) == {"counter"}


@needs_yosys
def test_the_register_graph_records_who_feeds_whom(tmp_path):
    nl = rtl_fixtures.from_verilog(PIPELINE, "pipe", tmp_path)
    registers = analyse.resolve_bit_order(nl, analyse.find_registers(nl))
    edges = sequential.graph(nl, registers)

    assert set(edges) == {r.name for r in registers}
    assert any(sources for sources in edges.values()), "nothing feeds anything"

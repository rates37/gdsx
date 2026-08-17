from __future__ import annotations

import pytest
import rtl_fixtures
from gdsx import analyse, synth
from gdsx.analysis.registers import describe
from gdsx.sim import Simulator

needs_yosys = pytest.mark.skipif(
    not synth.yosys_available(), reason="yosys not installed"
)

# The output word is wired up backwards, so the recovered order has to be the
# reverse of the obvious one. Guards against a probe that is really returning
# "sorted by instance name" and looking clever.
REVERSED = """
module rev_load(input clk, input rst_n, input load, input [7:0] d, output [7:0] q);
  reg [7:0] r;
  always @(posedge clk or negedge rst_n)
    if (!rst_n) r <= 8'd0; else if (load) r <= d;
  assign q = {r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7]};
endmodule
"""

# Two parallel registers sharing a clock (indistinguishable to topology)
# feeding an adder. Both the split and the adder depend on the probe.
TWO_WORDS = """
module par_add(input clk, input rst_n, input load, input [3:0] da, input [3:0] db,
               output [3:0] a_q, output [3:0] b_q, output [4:0] y);
  reg [3:0] a, b;
  always @(posedge clk or negedge rst_n)
    if (!rst_n) begin a <= 0; b <= 0; end
    else if (load) begin a <= da; b <= db; end
  assign a_q = a;
  assign b_q = b;
  assign y = a + b;
endmodule
"""

# Nothing observable but the sum, so there is no word to read the bits off
HIDDEN = """
module hidden(input clk, input rst_n, input load, input [3:0] da, input [3:0] db,
              output [4:0] y);
  reg [3:0] a, b;
  always @(posedge clk or negedge rst_n)
    if (!rst_n) begin a <= 0; b <= 0; end
    else if (load) begin a <= da; b <= db; end
  assign y = a + b;
endmodule
"""


def registers_of(source: str, top: str, tmp_path):
    nl = rtl_fixtures.from_verilog(source, top, tmp_path)
    return nl, analyse.find_registers(nl)


def test_indexed_ports_groups_bits_of_one_word():
    from gdsx.netlist import Netlist

    nl = Netlist(top="t")
    nl.ports = {
        "q_0": "output",
        "q_1": "output",
        "O[0]": "output",
        "O[1]": "output",
        "done": "output",
        "clk": "input",
    }
    families = analyse.indexed_ports(nl)
    assert set(families) == {"q", "O"}
    assert families["q"] == {0: "q_0", 1: "q_1"}


@needs_yosys
def test_parallel_register_gets_an_order(tmp_path):
    nl, registers = registers_of(rtl_fixtures.PARALLEL_LOAD, "parallel_load", tmp_path)
    assert not registers[0].ordered  # topology genuinely cannot tell

    (resolved,) = analyse.resolve_bit_order(nl, registers)
    assert resolved.ordered
    assert resolved.order_evidence == "probe"


@needs_yosys
def test_the_probe_recovers_a_reversed_order(tmp_path):
    nl, registers = registers_of(REVERSED, "rev_load", tmp_path)
    naive = list(registers[0].flops)

    (resolved,) = analyse.resolve_bit_order(nl, registers)
    assert resolved.ordered
    assert resolved.flops == naive[::-1]


@needs_yosys
def test_a_wrong_order_is_rejected(tmp_path):
    nl, registers = registers_of(rtl_fixtures.PARALLEL_LOAD, "parallel_load", tmp_path)
    (resolved,) = analyse.resolve_bit_order(nl, registers)

    swapped = list(resolved.flops)
    swapped[0], swapped[3] = swapped[3], swapped[0]
    wrong = analyse.Register("wrong", swapped, None, "parallel register", True, "probe")
    assert not analyse.check_order(nl, wrong, "q")
    assert analyse.check_order(nl, resolved, "q")


@needs_yosys
def test_two_registers_sharing_a_clock_are_split_and_then_add(tmp_path):
    nl, registers = registers_of(TWO_WORDS, "par_add", tmp_path)
    inputs = {p: 0 for p, d in nl.ports.items() if d == "input"}

    assert len(registers) == 1 and not registers[0].ordered
    assert not analyse.find_buses(
        nl, Simulator(nl), registers, inputs, workdir=tmp_path / "a"
    )

    resolved = analyse.resolve_bit_order(nl, registers)
    assert [r.name for r in resolved] == ["a_q", "b_q"]
    assert all(r.width == 4 and r.ordered for r in resolved)

    buses = analyse.find_buses(
        nl, Simulator(nl), resolved, inputs, workdir=tmp_path / "b"
    )
    assert [(b.name, b.width, b.tier) for b in buses] == [("sum", 5, "sat")]


@needs_yosys
def test_a_register_with_nothing_to_read_it_off_stays_unordered(tmp_path):
    nl, registers = registers_of(HIDDEN, "hidden", tmp_path)
    resolved = analyse.resolve_bit_order(nl, registers)
    assert len(resolved) == 1
    assert not resolved[0].ordered
    assert "bit order unknown" in describe(resolved[0])

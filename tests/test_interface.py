from __future__ import annotations

from pathlib import Path

import pytest
import rtl_fixtures
from gdsx import interface, synth
from gdsx.core.context import Design

needs_yosys = pytest.mark.skipif(
    not synth.yosys_available(), reason="yosys not installed"
)

DESIGNS = Path(__file__).resolve().parent / "designs"

# Registered output two deep, so the latency has something to measure
PIPE = """
module pipe(input clk, input rst_n, input go, input d, output q);
  reg a, b;
  always @(posedge clk or negedge rst_n)
    if (!rst_n) begin a <= 0; b <= 0; end
    else if (go) begin a <= d; b <= a; end
  assign q = b;
endmodule
"""


def kinds_of(source: str, top: str, tmp_path) -> dict[str, str]:
    nl = rtl_fixtures.from_verilog(source, top, tmp_path)
    return {port.name: port.kind for port in interface.inputs(nl)}


@needs_yosys
def test_clock_and_reset_are_found_structurally(tmp_path):
    kinds = kinds_of(PIPE, "pipe", tmp_path)
    assert kinds["clk"] == "clock"
    assert kinds["rst_n"] == "reset"


@needs_yosys
def test_reset_polarity_is_measured(tmp_path):
    nl = rtl_fixtures.from_verilog(PIPE, "pipe", tmp_path)
    port = next(p for p in interface.inputs(nl) if p.name == "rst_n")
    assert port.asserted == 0  # active low, and nothing read the name


@needs_yosys
def test_an_enable_is_a_gate_and_data_is_not(tmp_path):
    kinds = kinds_of(PIPE, "pipe", tmp_path)
    assert kinds["go"] == "gate"
    assert kinds["d"] == "data"


@needs_yosys
def test_a_serial_input_at_its_idle_level_is_not_a_gate(tmp_path):
    """A UART waiting for a start bit is frozen; that does not make `rx` an enable

    Measurements are warmed up with the port driven randomly first, so the
    design is mid-frame rather than idle when the question is asked.
    """
    kinds = kinds_of((DESIGNS / "uart_rx.v").read_text(), "uart_rx", tmp_path)
    assert kinds["rx"] == "data"


@needs_yosys
def test_a_clock_is_still_a_clock_when_it_is_buffered(tmp_path):
    nl = Design.open(Path("samples/puzzle.gds")).netlist
    kinds = {port.name: port.kind for port in interface.inputs(nl)}
    assert kinds["clk"] == "clock"
    assert kinds["enable"] == "gate"
    assert kinds["I"] == "data"


@needs_yosys
def test_output_latency_counts_registers(tmp_path):
    nl = rtl_fixtures.from_verilog(PIPE, "pipe", tmp_path)
    (port,) = [p for p in interface.outputs(nl) if p.name == "q"]
    assert port.kind == "registered"
    assert port.latency == 2, "two flops between d and q"


@needs_yosys
def test_a_combinational_output_has_no_latency(tmp_path):
    source = """
    module comb(input clk, input rst_n, input a, input b, output y);
      reg keep;
      always @(posedge clk or negedge rst_n) if (!rst_n) keep <= 0; else keep <= a;
      assign y = a ^ b;
    endmodule
    """
    nl = rtl_fixtures.from_verilog(source, "comb", tmp_path)
    (port,) = [p for p in interface.outputs(nl) if p.name == "y"]
    assert port.kind == "combinational"
    assert port.latency == 0


@needs_yosys
def test_an_alu_result_is_one_register_deep(tmp_path):
    nl = rtl_fixtures.from_verilog((DESIGNS / "alu.v").read_text(), "alu", tmp_path)
    latencies = {
        p.latency for p in interface.outputs(nl) if p.name.startswith("result")
    }
    assert latencies <= {1, None}
    assert 1 in latencies

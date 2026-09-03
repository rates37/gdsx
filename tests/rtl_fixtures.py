"""The circuit shapes the analysis has to cope with.

The GDS fixtures in `fixtures.py` test extraction, they have to be
hand-drawn because the point is the geometry. The analysis needs the opposite:
many different circuit shapes (counters, accumulators, register files) where the
geometry is irrelevant. `gdsx.synth` synthesises them. This module supplies the
Verilog and re-exports the pieces the tests reach for
"""

from __future__ import annotations

from gdsx.synth import (  # noqa: F401
    GATE_MAP,
    GATES,
    from_verilog,
    synthesize,
    to_netlist,
    yosys_available,
)

# the circuit shapes the analysis needs to cope with

COUNTER = """
module counter(input clk, input rst_n, input en, output [7:0] q);
  reg [7:0] c;
  always @(posedge clk or negedge rst_n)
    if (!rst_n) c <= 8'd0; else if (en) c <= c + 1;
  assign q = c;
endmodule
"""

ACCUMULATOR = """
module accumulator(input clk, input rst_n, input [3:0] d, output [3:0] q);
  reg [3:0] acc;
  always @(posedge clk or negedge rst_n)
    if (!rst_n) acc <= 4'd0; else acc <= acc + d;
  assign q = acc;
endmodule
"""

NONZERO_RESET = """
module nonzero_reset(input clk, input rst_n, output [3:0] q);
  reg [3:0] r;
  always @(posedge clk or negedge rst_n)
    if (!rst_n) r <= 4'b1010; else r <= r + 4'd1;
  assign q = r;
endmodule
"""

PARALLEL_LOAD = """
module parallel_load(input clk, input rst_n, input load, input [7:0] d, output [7:0] q);
  reg [7:0] r;
  always @(posedge clk or negedge rst_n)
    if (!rst_n) r <= 8'd0; else if (load) r <= d;
  assign q = r;
endmodule
"""

SHIFT_REGISTER = """
module shifter(input clk, input rst_n, input en, input si, output [7:0] q);
  reg [7:0] r;
  always @(posedge clk or negedge rst_n)
    if (!rst_n) r <= 8'd0; else if (en) r <= {r[6:0], si};
  assign q = r;
endmodule
"""

TWO_REGISTERS = """
module two_regs(input clk, input rst_n, input a_in, input b_in, output eq);
  reg [3:0] a, b;
  always @(posedge clk or negedge rst_n)
    if (!rst_n) begin a <= 0; b <= 0; end
    else begin a <= {a[2:0], a_in}; b <= {b[2:0], b_in}; end
  assign eq = (a + b == 5'd20);
endmodule
"""

TRAFFIC = """
module traffic(input clk, input rst_n, input req, output go, output warn);
  localparam RED = 2'd0, GREEN = 2'd1, AMBER = 2'd2;
  reg [1:0] state;
  always @(posedge clk or negedge rst_n)
    if (!rst_n) state <= RED;
    else case (state)
      RED:     state <= req ? GREEN : RED;
      GREEN:   state <= AMBER;
      AMBER:   state <= RED;
      default: state <= RED;
    endcase
  assign go = (state == GREEN);
  assign warn = (state == AMBER);
endmodule
"""

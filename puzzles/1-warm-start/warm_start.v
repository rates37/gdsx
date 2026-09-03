// Puzzle 1 -- "Warm Start".  See docs/game/puzzle-pack.md section 1.
//
// Central idea: the initial state and the *actual* advance rate of a register
// are properties of the netlist. Both look like details you can assume, and
// both are wrong if you assume them.
//
//   * `acc` resets to 0xA5C, not to zero, so six of its twelve flops
//     synthesise as async-set flops (dfstp) and six as async-reset flops
//     (dfrtp). The reset value is readable from the cell mix alone.
//   * `ph` suppresses one accumulate in four, so the design advances on 375
//     of the first 500 cycles rather than on all 500. This is the trap.
//   * `acc` freezes once `cyc` reaches 500 and `success` latches there, so
//     the answer is stable at the moment the player is told to read it.

module warm_start (
    input  wire       clk, rst_n, enable,
    output wire [7:0] O,
    output wire       success
);
    reg [11:0] acc;      // resets to a non-zero constant
    reg [1:0]  ph;       // free-running phase, gates the accumulate
    reg [8:0]  cyc;      // saturating cycle counter

    wire done = (cyc == 9'd500);
    wire tick = enable & (ph != 2'd3) & ~done;

    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            acc <= 12'hA5C;
            ph  <= 2'd0;
            cyc <= 9'd0;
        end else begin
            ph  <= ph + 2'd1;
            if (!done) cyc <= cyc + 9'd1;
            if (tick)  acc <= acc + 12'd37;
        end

    assign O       = acc[11:4];
    assign success = done;
endmodule

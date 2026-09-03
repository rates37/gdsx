// Puzzle 3 -- "Gatekeeper".  See docs/game/puzzle-pack.md section 3.
//
// Central idea: a register's write enable is a Boolean function you can
// extract exactly, and the control path is a separate, much smaller, much
// more informative circuit than the data path.
//
//   * Eight 8-bit banks. Seven share one write command; `r5` does not, and
//     that asymmetry is one glance at a guard listing.
//   * `armed` is set by a *different* command word whose address field is 6,
//     not 5. A player who assumes `addr` always names the target register
//     never finds the arm command.
//   * `busy` is loaded by the arm command itself and counts down, so arming
//     and writing cannot be adjacent: arm at t, write no earlier than t+4.
//   * `success` is a one-way latch on `r5 == 0x5A`, so the data value is
//     forced even though the timing is not.

module gatekeeper (
    input  wire       clk, rst_n, enable,
    input  wire [3:0] mode,
    input  wire [2:0] addr,
    input  wire       go,
    input  wire [7:0] D,
    output wire [7:0] O,
    output reg        success
);
    reg [7:0] r0, r1, r2, r3, r4, r5, r6, r7;
    reg       armed;
    reg [1:0] busy;

    wire cmd_wr  = enable & go & (mode == 4'b1001);
    wire cmd_arm = enable & go & (mode == 4'b1011) & (addr == 3'd6);
    wire cmd_w5  = enable & go & (mode == 4'b0110) & (addr == 3'd5);

    wire [7:0] sel = 8'b1 << addr;
    wire       wr5 = cmd_w5 & armed & (busy == 2'd0);

    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            armed   <= 1'b0;
            busy    <= 2'd0;
            success <= 1'b0;
            r0 <= 8'd0; r1 <= 8'd0; r2 <= 8'd0; r3 <= 8'd0;
            r4 <= 8'd0; r5 <= 8'd0; r6 <= 8'd0; r7 <= 8'd0;
        end else begin
            if (cmd_wr | cmd_arm)   busy <= 2'd3;
            else if (busy != 2'd0)  busy <= busy - 2'd1;

            if (cmd_arm)  armed <= 1'b1;
            else if (wr5) armed <= 1'b0;          // one-shot

            if (cmd_wr & sel[0]) r0 <= D;
            if (cmd_wr & sel[1]) r1 <= D;
            if (cmd_wr & sel[2]) r2 <= D;
            if (cmd_wr & sel[3]) r3 <= D;
            if (cmd_wr & sel[4]) r4 <= D;
            if (wr5)             r5 <= D;
            if (cmd_wr & sel[6]) r6 <= D;
            if (cmd_wr & sel[7]) r7 <= D;

            if (r5 == 8'h5A) success <= 1'b1;     // one-way latch
        end

    assign O = (addr == 3'd0) ? r0 :
               (addr == 3'd1) ? r1 :
               (addr == 3'd2) ? r2 :
               (addr == 3'd3) ? r3 :
               (addr == 3'd4) ? r4 :
               (addr == 3'd5) ? r5 :
               (addr == 3'd6) ? r6 : r7;
endmodule
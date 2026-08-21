// Puzzle 2 -- "Polynomial".  See docs/game/puzzle-pack.md section 2.
//
// Central idea: an autonomous linear recurrence is fully characterised by its
// polynomial, and once you have the polynomial you can jump the state forward
// by an arbitrary number of steps algebraically instead of simulating it.
//
//   * `s` is a 32-bit Galois LFSR with the maximal-length mask 0xA3000000
//     (taps at 32, 30, 26 and 25; period 2**32 - 1). Galois form puts the
//     XOR gates *inside* the shift chain, so the taps are three xor2 cells
//     interrupting an otherwise clean Q -> D chain.
//   * The seed 0x13579BDF is non-zero, so 20 of the 32 flops synthesise as
//     async-set flops (dfstp) and 12 as async-reset flops (dfrtp). The seed
//     is readable from the cell mix alone, exactly as in puzzle 1.
//   * `sel` picks which byte of the state `O` shows, so the player can
//     observe all 32 bits and check their own model. Without it half the
//     state is unobservable and the puzzle becomes a guess.
//
// There is no `success` pin. The design is autonomous, so a lock would be a
// lie about what the circuit does; the answer is a parameter, not a stimulus.

module polynomial (
    input  wire       clk, rst_n, enable,
    input  wire [1:0] sel,
    output wire [7:0] O
);
    reg [31:0] s;

    always @(posedge clk or negedge rst_n)
        if (!rst_n)      s <= 32'h13579BDF;              // non-zero seed
        else if (enable) s <= (s >> 1) ^ (s[0] ? 32'hA300_0000 : 32'h0);

    assign O = s[{sel, 3'b000} +: 8];
endmodule
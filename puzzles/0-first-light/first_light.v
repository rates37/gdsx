// Puzzle 0 -- "First Light".  The guided puzzle.
//
// This one is not trying to be hard. It is the design the tutorial walks a
// player through, so every panel it teaches has to have something real and
// small to show:
//
//   * `code` is a six-stage shift register fed from `I`, so Q->D adjacency
//     recovers an unambiguous chain and the register decoder can classify it.
//   * `success` is a one-way latch -- once set it feeds itself -- so the
//     sticky-flops panel has exactly one entry and it is the lock.
//   * The unlock condition is a single equality against a constant, so
//     flattening the `success` cone yields six forced leaves and nothing
//     else: the requirements panel gives up the whole answer in one click,
//     which is the moment the tutorial is built around.
//   * `O` mirrors `code`, so the waveform shows the word arriving bit by bit
//     rather than the player having to take the shift register on trust.
//
// Six bits means the search space is 64 -- genuinely brute-forceable, and
// that is deliberate. The tutorial's point is that you can *derive* the
// answer instead, on a design small enough to check the derivation by eye.

module first_light (
    input  wire       clk,
    input  wire       rst_n,
    input  wire       I,
    output wire [5:0] O,
    output wire       success
);
    reg [5:0] code;     // six-stage shift register, MSB first
    reg       lock;     // one-way latch: the success pin

    // The unlock word. Alternating-ish rather than all-ones so that a player
    // who guesses "probably all 1s" is wrong, and so the flattened cone
    // shows both polarities.
    wire match = (code == 6'b101101);

    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            code <= 6'b000000;
            lock <= 1'b0;
        end else begin
            code <= {code[4:0], I};
            if (match) lock <= 1'b1;
        end

    assign O       = code;
    assign success = lock;
endmodule
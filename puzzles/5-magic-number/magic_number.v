// Puzzle 5 -- "Magic Number".  See docs/game/puzzle-pack.md section 5.
//
// Central idea: flattening a fan-in cone *structurally*, with polarity, reads
// a constant straight out of the gates. Sampling net values in a running
// design cannot distinguish the real comparator from the decoys; only the
// structure can.
//
//   * `sr` is a 32-stage shift register fed from `I`. The lock compares it
//     against one constant, and the polarity of the 32 leaves of that
//     comparator's AND tree *is* the constant.
//   * Four decoys, of four different kinds. Each is observable on `O`, so
//     synthesis keeps all of them (puzzle-pack.md section 0.3): an
//     unobservable decoy is deleted, and a decoy that drives nothing
//     announces itself as a decoy.
//   * `never` is the one decoy inside the `success` cone. Its set condition
//     is unreachable in fact -- at `cnt == 7` only eight bits have been
//     shifted in, so `sr[31:8]` still holds its reset value -- but the
//     argument is temporal, so no synthesiser folds it and it survives as
//     real gates for the player to disprove.

module magic_number (
    input  wire       clk, rst_n, enable,
    input  wire       I,
    output wire [7:0] O,
    output reg        success
);
    reg [31:0] sr;
    reg [5:0]  cnt;      // saturating at 63
    reg        never;

    wire match   = (sr == 32'h5EED_C0DE);   // the lock
    wire decoy_a = (sr == 32'hDEAD_BEEF);   // reachable path, dead term
    wire decoy_b = (sr[15:0] == 16'hBEEF);
    wire decoy_c = (sr[31:16] == 16'hC0DE);
    wire decoy_d = (^sr) ^ (^sr[15:0]);     // a parity identity, always equal to ^sr[31:16]

    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin sr <= 32'b0; cnt <= 6'b0; never <= 1'b0; success <= 1'b0; end
        else if (enable) begin
            sr  <= {sr[30:0], I};
            if (cnt != 6'd63) cnt <= cnt + 6'd1;

            // Unreachable in fact: at cnt==7 only 8 bits have been shifted in,
            // so sr[31:8] still holds its reset value of 0.  No synthesiser
            // will fold this, because the argument is temporal.
            if ((sr == 32'hFFFF_FFFF) & (cnt == 6'd7)) never <= 1'b1;

            if ((match & (cnt >= 6'd32)) | (decoy_a & never)) success <= 1'b1;
        end

    assign O = {decoy_a, decoy_b, decoy_c, decoy_d, sr[3:0]};   // observable => survives synthesis
endmodule
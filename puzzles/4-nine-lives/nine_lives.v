// Puzzle 4 -- "Nine Lives".  See docs/game/puzzle-pack.md section 4.
//
// Central idea: a one-hot state machine hands over its transition relation
// directly -- each state flop's D-cone is a list of (predecessor, symbol)
// pairs -- so the question stops being "what input?" and becomes "what is
// reachable?", which is a graph search.
//
//   * Nine one-hot state flops, resetting to S0 (so st[0] synthesises as an
//     async-set flop and st[8:1] as async-reset flops -- the reset value is
//     readable from the cell mix alone).
//   * Six of the nine states form the accepting backbone
//     S0 -2-> S1 -3-> S2 -1-> S3 -0-> S4 -2-> S8.
//   * The other three, S5/S6/S7, are a strongly connected component with no
//     edge out of it.  It is entered from S2 on symbol 3 -- the symbol that
//     was legal one state earlier -- and once inside, every symbol keeps you
//     inside.  A greedy forward walk falls in and nothing announces it.
//   * `lives` resets to 9 (4'b1001: two async-set flops, two async-reset)
//     and decrements on every symbol that is not the current state's good
//     symbol.  The accept condition needs `lives` still at 9, so in fact none
//     of the nine mistakes may be spent.  That term lives in the `success`
//     cone, not in the counter's own logic.
//   * `O` publishes `lives`, `locked` and a three-bit sink indicator the
//     whole time, so the evidence for the trap is on the bus from the first
//     cycle.  The sink has to reach `O` somehow: it changes nothing a player
//     can otherwise observe, so an unobservable sink is deleted by synthesis
//     before it ever reaches the layout.

module nine_lives (
    input  wire       clk, rst_n, enable,
    input  wire [1:0] I,
    output wire [7:0] O,
    output reg        success
);
    reg [8:0] st;        // one-hot, resets to 9'b0_0000_0001
    reg [3:0] lives;     // resets to 9
    reg       locked;

    // Symbol decode: the four letters of the alphabet.
    wire s0 = (I == 2'd0);
    wire s1 = (I == 2'd1);
    wire s2 = (I == 2'd2);
    wire s3 = (I == 2'd3);

    // `good` is the one-hot AND of the state with its own outgoing symbol.
    // S5/S6/S7 have none -- every symbol inside the sink is a mistake.
    // S8 is good on everything, so a solved machine stops spending lives.
    wire good  = (st[0] & s2) | (st[1] & s3) | (st[2] & s1)
               | (st[3] & s0) | (st[4] & s2) | st[8];
    wire spend = enable & ~locked & ~good;

    // Transition table, 9 x 4, written as the D of each state flop.  Every
    // (state, symbol) pair not named as an edge below holds its state.
    wire [8:0] nxt;
    assign nxt[0] =  st[0] & ~s2;
    assign nxt[1] = (st[0] &  s2) | (st[1] & ~s3);
    assign nxt[2] = (st[1] &  s3) | (st[2] & ~(s1 | s3));
    assign nxt[3] = (st[2] &  s1) | (st[3] & ~s0);
    assign nxt[4] = (st[3] &  s0) | (st[4] & ~s2);
    // The sink: S2 -3-> S5, then S5 -> S6 -> S7 -> S5 with no way out.
    assign nxt[5] = (st[2] &  s3) | (st[5] &  s2) | (st[6] & s1) | (st[7] & (s0 | s3));
    assign nxt[6] = (st[5] & (s0 | s3)) | (st[6] & s2) | (st[7] & s1);
    assign nxt[7] = (st[5] &  s1) | (st[6] & (s0 | s3)) | (st[7] & s2);
    // S8 is absorbing.
    assign nxt[8] = (st[4] &  s2) | st[8];

    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            st      <= 9'b0_0000_0001;
            lives   <= 4'd9;
            locked  <= 1'b0;
            success <= 1'b0;
        end else if (enable & ~locked) begin
            st <= nxt;
            if (spend)                   lives   <= lives - 4'd1;
            if (spend & (lives == 4'd1)) locked  <= 1'b1;
            if (st[8] & (lives == 4'd9)) success <= 1'b1;
        end

    // Observation bus.  Low nibble is `lives`, top bit is `locked`, and the
    // three bits between them number the sink state: 0 outside it, then 1, 2
    // and 3 for S5, S6 and S7.  Encoded rather than passed straight through
    // so that each sink flop keeps its own name and its own gates.
    wire in_sink = st[5] | st[6] | st[7];
    assign O = {locked, in_sink, st[6] | st[7], st[5] | st[7], lives};
endmodule
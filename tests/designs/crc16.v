// CRC-16-CCITT, one bit per clock
module crc16 (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        en,
    input  wire        d,
    output wire [15:0] crc
);
    reg [15:0] state;
    wire feedback = state[15] ^ d;

    always @(posedge clk or negedge rst_n)
        if (!rst_n) state <= 16'hFFFF;
        else if (en) begin
            state[0]      <= feedback;
            state[4:1]    <= state[3:0];
            state[5]      <= state[4] ^ feedback;
            state[11:6]   <= state[10:5];
            state[12]     <= state[11] ^ feedback;
            state[15:13]  <= state[14:12];
        end
    assign crc = state;
endmodule

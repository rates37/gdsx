// Registered ALU with an opcode
module alu (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [2:0]  op,
    input  wire [7:0]  a,
    input  wire [7:0]  b,
    output reg  [8:0]  result,
    output reg         zero
);
    reg [8:0] combinational;
    always @(*)
        case (op)
            3'd0: combinational = a + b;
            3'd1: combinational = a - b;
            3'd2: combinational = {1'b0, a & b};
            3'd3: combinational = {1'b0, a | b};
            3'd4: combinational = {1'b0, a ^ b};
            3'd5: combinational = {1'b0, a << 1};
            3'd6: combinational = {1'b0, a >> 1};
            default: combinational = {1'b0, a};
        endcase

    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin result <= 0; zero <= 0; end
        else begin result <= combinational; zero <= (combinational == 9'd0); end
endmodule

// One iterative CORDIC rotation stage registered.
module cordic (
    input  wire               clk,
    input  wire               rst_n,
    input  wire               start,
    input  wire signed [11:0] x_in,
    input  wire signed [11:0] y_in,
    output reg  signed [11:0] x,
    output reg  signed [11:0] y,
    output reg         [2:0]  step
);
    wire signed [11:0] shifted_x = x >>> step;
    wire signed [11:0] shifted_y = y >>> step;
    wire direction = ~y[11];

    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin x <= 0; y <= 0; step <= 0; end
        else if (start) begin x <= x_in; y <= y_in; step <= 0; end
        else if (step != 3'd7) begin
            x <= direction ? x - shifted_y : x + shifted_y;
            y <= direction ? y + shifted_x : y - shifted_x;
            step <= step + 1;
        end
endmodule

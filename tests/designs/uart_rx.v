// Standard 8N1 UART receiver: start-bit detect, mid-bit sampling, shift in.
module uart_rx #(parameter DIVISOR = 16) (
    input  wire       clk,
    input  wire       rst_n,
    input  wire       rx,
    output reg  [7:0] data,
    output reg        valid
);
    localparam IDLE = 2'd0, START = 2'd1, DATA = 2'd2, STOP = 2'd3;
    reg [1:0] state;
    reg [7:0] tick;
    reg [2:0] bit_index;
    reg [7:0] shifter;

    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            state <= IDLE; tick <= 0; bit_index <= 0; shifter <= 0;
            data <= 0; valid <= 0;
        end else begin
            valid <= 0;
            case (state)
                IDLE: begin
                    tick <= 0;
                    if (!rx) state <= START;
                end
                START:
                    if (tick == DIVISOR / 2) begin
                        tick <= 0;
                        state <= rx ? IDLE : DATA;
                        bit_index <= 0;
                    end else tick <= tick + 1;
                DATA:
                    if (tick == DIVISOR - 1) begin
                        tick <= 0;
                        shifter <= {rx, shifter[7:1]};
                        if (bit_index == 3'd7) state <= STOP;
                        else bit_index <= bit_index + 1;
                    end else tick <= tick + 1;
                STOP:
                    if (tick == DIVISOR - 1) begin
                        tick <= 0;
                        state <= IDLE;
                        data <= shifter;
                        valid <= 1;
                    end else tick <= tick + 1;
            endcase
        end
endmodule

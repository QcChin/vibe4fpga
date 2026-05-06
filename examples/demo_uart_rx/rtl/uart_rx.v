module uart_rx #(
    parameter int OVERSAMPLE = 16
) (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        tick,
    input  wire        rx_in,
    output reg  [7:0]  rx_data,
    output reg         rx_valid,
    input  wire        rx_ready,
    output reg         rx_frame_error
);

    // Internal parameter for counter width
    localparam int CNT_W = $clog2(OVERSAMPLE + 1);

    // FSM state encoding
    localparam [3:0] S_IDLE  = 4'd0,
                     S_START = 4'd1,
                     S_D0    = 4'd2,
                     S_D1    = 4'd3,
                     S_D2    = 4'd4,
                     S_D3    = 4'd5,
                     S_D4    = 4'd6,
                     S_D5    = 4'd7,
                     S_D6    = 4'd8,
                     S_D7    = 4'd9,
                     S_STOP  = 4'd10;

    // Mid-bit sample thresholds
    localparam [CNT_W-1:0] MID_START = (OVERSAMPLE / 2) - 1;
    localparam [CNT_W-1:0] MID_BIT   = OVERSAMPLE - 1;

    // 2-FF synchronizer for async rx_in
    logic rx_in_meta;
    logic rx_in_sync;

    // FSM state register
    logic [3:0] state;

    // Tick counter and shift register
    logic [CNT_W-1:0] tick_cnt;
    logic [7:0]       shift_reg;

    // 2-FF synchronizer
    always_ff @(posedge clk) begin
        if (!rst_n) begin
            rx_in_meta <= 1'b1;
            rx_in_sync <= 1'b1;
        end else begin
            rx_in_meta <= rx_in;
            rx_in_sync <= rx_in_meta;
        end
    end

    // Main FSM + datapath
    always_ff @(posedge clk) begin
        if (!rst_n) begin
            state          <= S_IDLE;
            rx_data        <= 8'd0;
            rx_valid       <= 1'b0;
            rx_frame_error <= 1'b0;
            tick_cnt       <= {CNT_W{1'b0}};
            shift_reg      <= 8'd0;
        end else begin
            // Default: deassert pulse outputs every cycle
            rx_valid       <= 1'b0;
            rx_frame_error <= 1'b0;

            case (state)
                S_IDLE: begin
                    tick_cnt  <= {CNT_W{1'b0}};
                    shift_reg <= shift_reg; // hold
                    if (rx_in_sync == 1'b0) begin
                        state    <= S_START;
                        tick_cnt <= {CNT_W{1'b0}};
                    end
                end

                S_START: begin
                    if (tick) begin
                        if (tick_cnt == MID_START) begin
                            // Mid-start-bit sample
                            if (rx_in_sync == 1'b0) begin
                                // Valid start bit confirmed
                                state    <= S_D0;
                                tick_cnt <= {CNT_W{1'b0}};
                            end else begin
                                // Glitch, return to idle
                                state    <= S_IDLE;
                                tick_cnt <= {CNT_W{1'b0}};
                            end
                        end else begin
                            tick_cnt <= tick_cnt + 1'b1;
                        end
                    end
                end

                S_D0: begin
                    if (tick) begin
                        if (tick_cnt == MID_BIT) begin
                            shift_reg <= {rx_in_sync, shift_reg[7:1]};
                            tick_cnt  <= {CNT_W{1'b0}};
                            state     <= S_D1;
                        end else begin
                            tick_cnt <= tick_cnt + 1'b1;
                        end
                    end
                end

                S_D1: begin
                    if (tick) begin
                        if (tick_cnt == MID_BIT) begin
                            shift_reg <= {rx_in_sync, shift_reg[7:1]};
                            tick_cnt  <= {CNT_W{1'b0}};
                            state     <= S_D2;
                        end else begin
                            tick_cnt <= tick_cnt + 1'b1;
                        end
                    end
                end

                S_D2: begin
                    if (tick) begin
                        if (tick_cnt == MID_BIT) begin
                            shift_reg <= {rx_in_sync, shift_reg[7:1]};
                            tick_cnt  <= {CNT_W{1'b0}};
                            state     <= S_D3;
                        end else begin
                            tick_cnt <= tick_cnt + 1'b1;
                        end
                    end
                end

                S_D3: begin
                    if (tick) begin
                        if (tick_cnt == MID_BIT) begin
                            shift_reg <= {rx_in_sync, shift_reg[7:1]};
                            tick_cnt  <= {CNT_W{1'b0}};
                            state     <= S_D4;
                        end else begin
                            tick_cnt <= tick_cnt + 1'b1;
                        end
                    end
                end

                S_D4: begin
                    if (tick) begin
                        if (tick_cnt == MID_BIT) begin
                            shift_reg <= {rx_in_sync, shift_reg[7:1]};
                            tick_cnt  <= {CNT_W{1'b0}};
                            state     <= S_D5;
                        end else begin
                            tick_cnt <= tick_cnt + 1'b1;
                        end
                    end
                end

                S_D5: begin
                    if (tick) begin
                        if (tick_cnt == MID_BIT) begin
                            shift_reg <= {rx_in_sync, shift_reg[7:1]};
                            tick_cnt  <= {CNT_W{1'b0}};
                            state     <= S_D6;
                        end else begin
                            tick_cnt <= tick_cnt + 1'b1;
                        end
                    end
                end

                S_D6: begin
                    if (tick) begin
                        if (tick_cnt == MID_BIT) begin
                            shift_reg <= {rx_in_sync, shift_reg[7:1]};
                            tick_cnt  <= {CNT_W{1'b0}};
                            state     <= S_D7;
                        end else begin
                            tick_cnt <= tick_cnt + 1'b1;
                        end
                    end
                end

                S_D7: begin
                    if (tick) begin
                        if (tick_cnt == MID_BIT) begin
                            shift_reg <= {rx_in_sync, shift_reg[7:1]};
                            tick_cnt  <= {CNT_W{1'b0}};
                            state     <= S_STOP;
                        end else begin
                            tick_cnt <= tick_cnt + 1'b1;
                        end
                    end
                end

                S_STOP: begin
                    if (tick) begin
                        if (tick_cnt == MID_BIT) begin
                            tick_cnt <= {CNT_W{1'b0}};
                            state    <= S_IDLE;
                            if (rx_in_sync == 1'b1) begin
                                // Valid stop bit
                                rx_data  <= shift_reg;
                                rx_valid <= 1'b1;
                            end else begin
                                // Framing error
                                rx_frame_error <= 1'b1;
                            end
                        end else begin
                            tick_cnt <= tick_cnt + 1'b1;
                        end
                    end
                end

                default: begin
                    state    <= S_IDLE;
                    tick_cnt <= {CNT_W{1'b0}};
                end
            endcase
        end
    end

    // Tie off rx_ready to avoid unused port warning (per spec: no internal use)
    wire unused_rx_ready = rx_ready;

`ifdef FORMAL
    // Basic SVA assertions
    // rx_valid and rx_frame_error should never be asserted simultaneously
    assert property (@(posedge clk) disable iff (!rst_n)
        !(rx_valid && rx_frame_error));

    // rx_valid is a single-cycle pulse
    assert property (@(posedge clk) disable iff (!rst_n)
        rx_valid |=> !rx_valid);

    // rx_frame_error is a single-cycle pulse
    assert property (@(posedge clk) disable iff (!rst_n)
        rx_frame_error |=> !rx_frame_error);

    // In IDLE state, tick_cnt should be 0
    assert property (@(posedge clk) disable iff (!rst_n)
        (state == S_IDLE) |-> (tick_cnt == 0));
`endif

endmodule

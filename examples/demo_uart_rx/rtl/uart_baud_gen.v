module uart_baud_gen #(
    parameter int CLK_HZ     = 50_000_000,
    parameter int BAUD_HZ    = 115200,
    parameter int OVERSAMPLE = 16
)(
    input  wire clk,
    input  wire rst_n,
    output reg  tick
);

    // Compute the clock divider value
    localparam int DIV   = CLK_HZ / (BAUD_HZ * OVERSAMPLE);
    localparam int CNT_W = (DIV > 1) ? $clog2(DIV) : 1;

    // Ensure DIV is at least 1 (synthesis-time check via generate)
    generate
        if (DIV < 1) begin : div_check
            // This will cause a synthesis/elaboration error if DIV < 1
            // by creating an illegal parameter situation
            $error("uart_baud_gen: DIV must be >= 1. Check CLK_HZ, BAUD_HZ, OVERSAMPLE parameters.");
        end
    endgenerate

    // Counter register
    logic [CNT_W-1:0] counter;

    // Sequential logic: counter and tick generation
    always_ff @(posedge clk) begin
        if (!rst_n) begin
            counter <= {CNT_W{1'b0}};
            tick    <= 1'b0;
        end else begin
            if (counter == CNT_W'(DIV - 1)) begin
                // Wrap counter and assert tick for one cycle
                counter <= {CNT_W{1'b0}};
                tick    <= 1'b1;
            end else begin
                // Increment counter, deassert tick
                counter <= counter + {{(CNT_W-1){1'b0}}, 1'b1};
                tick    <= 1'b0;
            end
        end
    end

`ifdef FORMAL
    // Formal verification assertions
    // tick should be a single-cycle pulse
    assert property (@(posedge clk) disable iff (!rst_n)
        tick |=> !tick || (counter == CNT_W'(DIV - 1)));

    // Counter should never exceed DIV-1
    assert property (@(posedge clk) disable iff (!rst_n)
        counter <= CNT_W'(DIV - 1));

    // After reset, counter and tick should be 0
    assert property (@(posedge clk)
        !rst_n |=> (counter == 0 && tick == 0));
`endif

endmodule

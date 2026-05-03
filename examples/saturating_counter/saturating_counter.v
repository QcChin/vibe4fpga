// saturating_counter.v
// A width-parameterised up/down counter that clamps at its bounds
// instead of wrapping. Used as the DUT in docs/demo-walkthrough.md.
//
// Style choices deliberately match the spec2rtl generator's output:
//   - explicit synchronous reset (active-low rst_n)
//   - combinational next-state, registered current-state
//   - no latches, no initial blocks, no X-assignment
module saturating_counter #(
    parameter int WIDTH = 4
) (
    input  wire                  clk,
    input  wire                  rst_n,
    input  wire                  enable,
    input  wire                  direction,    // 1 = up, 0 = down
    output reg  [WIDTH-1:0]      count,
    output wire                  at_max,
    output wire                  at_min
);
    localparam [WIDTH-1:0] MAX_VAL = {WIDTH{1'b1}};
    localparam [WIDTH-1:0] MIN_VAL = {WIDTH{1'b0}};

    assign at_max = (count == MAX_VAL);
    assign at_min = (count == MIN_VAL);

    reg [WIDTH-1:0] next_count;

    always @(*) begin
        next_count = count;
        if (enable) begin
            if (direction && !at_max)
                next_count = count + 1'b1;
            else if (!direction && !at_min)
                next_count = count - 1'b1;
        end
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)  count <= MIN_VAL;
        else         count <= next_count;
    end
endmodule

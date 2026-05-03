// tb_saturating_counter.sv
// Self-checking testbench for saturating_counter.  Designed to emit the
// PASS / FAIL markers recognised by eda-bridge-mcp's parse_sim_log(), so
// a single `iverilog` + `vvp` invocation can be scored without an LLM.
//
// Run manually:
//   iverilog -g2012 -o tb.vvp saturating_counter.v tb_saturating_counter.sv
//   vvp tb.vvp
//
// Or via the MCP:
//   eda-bridge-mcp.run_simulation(testbench="...tb_saturating_counter.sv",
//                                 source_files=["...saturating_counter.v"],
//                                 simulator="icarus")
`timescale 1ns/1ps

module tb_saturating_counter;
    localparam int WIDTH = 4;

    reg                 clk = 0;
    reg                 rst_n;
    reg                 enable;
    reg                 direction;
    wire [WIDTH-1:0]    count;
    wire                at_max, at_min;

    // 10ns period ≡ 100 MHz
    always #5 clk = ~clk;

    saturating_counter #(.WIDTH(WIDTH)) dut (
        .clk       (clk),
        .rst_n     (rst_n),
        .enable    (enable),
        .direction (direction),
        .count     (count),
        .at_max    (at_max),
        .at_min    (at_min)
    );

    int pass_count = 0;
    int fail_count = 0;

    task automatic check (input [WIDTH-1:0] expected, input string label);
        if (count === expected) begin
            pass_count++;
            $display("[%0t] PASS: %s (count=%0d)", $time, label, count);
        end else begin
            fail_count++;
            $display("[%0t] FAIL: %s (count=%0d, expected=%0d)",
                     $time, label, count, expected);
        end
    endtask

    // 20-cycle watchdog so a broken DUT can't hang iverilog forever.
    initial begin
        #2000;
        $display("FAIL: watchdog timeout");
        $finish;
    end

    initial begin
        $dumpfile("saturating_counter.vcd");
        $dumpvars(0, tb_saturating_counter);

        // Reset
        rst_n     = 0;
        enable    = 0;
        direction = 1;
        #12 rst_n = 1;
        check(4'h0, "reset clears count");

        // Count up to saturation
        enable    = 1;
        direction = 1;
        repeat (20) @(posedge clk);
        check(4'hF, "saturates at MAX_VAL (4'hF)");

        // at_max flag
        if (at_max && !at_min) pass_count++;
        else                    fail_count++;

        // Count down to saturation
        direction = 0;
        repeat (20) @(posedge clk);
        check(4'h0, "saturates at MIN_VAL (4'h0)");

        // at_min flag
        if (at_min && !at_max) pass_count++;
        else                    fail_count++;

        // Enable=0 must hold the count
        direction = 1;
        enable    = 0;
        repeat (5) @(posedge clk);
        check(4'h0, "enable=0 holds count");

        if (fail_count == 0) $display("All tests passed (%0d/%0d)",
                                      pass_count, pass_count);
        else                 $display("Test FAILED (%0d fail, %0d pass)",
                                      fail_count, pass_count);
        $finish;
    end
endmodule

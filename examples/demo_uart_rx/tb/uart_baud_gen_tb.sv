`timescale 1ns/1ps

module tb_uart_baud_gen;

    // =========================================================================
    // Parameters - use small DIV for fast simulation
    // CLK_HZ=50MHz, BAUD_HZ=115200, OVERSAMPLE=16 => DIV=27
    // =========================================================================
    localparam int CLK_HZ     = 50_000_000;
    localparam int BAUD_HZ    = 115_200;
    localparam int OVERSAMPLE = 16;
    localparam int DIV        = CLK_HZ / (BAUD_HZ * OVERSAMPLE); // = 27

    localparam real CLK_PERIOD = 20.0; // 50MHz => 20ns

    // =========================================================================
    // DUT signals
    // =========================================================================
    logic clk;
    logic rst_n;
    logic tick;

    // =========================================================================
    // DUT instantiation
    // =========================================================================
    uart_baud_gen #(
        .CLK_HZ     (CLK_HZ),
        .BAUD_HZ    (BAUD_HZ),
        .OVERSAMPLE (OVERSAMPLE)
    ) dut (
        .clk   (clk),
        .rst_n (rst_n),
        .tick  (tick)
    );

    // =========================================================================
    // Clock generation
    // =========================================================================
    initial clk = 1'b0;
    always #(CLK_PERIOD/2.0) clk = ~clk;

    // =========================================================================
    // Simulation timeout
    // =========================================================================
    // Expected run: ~200 DIV cycles * DIV clocks = 200*27 = 5400 cycles
    // Timeout at 10x = 54000 cycles
    initial begin
        #(CLK_PERIOD * 54000);
        $display("TIMEOUT: Simulation exceeded maximum time.");
        $finish;
    end

    // =========================================================================
    // Tracking variables
    // =========================================================================
    integer tick_count;
    integer cycle_count;
    integer last_tick_cycle;
    integer tick_period_measured;
    integer tick_high_cycles;
    integer errors;

    // =========================================================================
    // Covergroup
    // =========================================================================
    covergroup cg_baud_gen @(posedge clk);
        // Coverpoint for tick output
        cp_tick: coverpoint tick {
            bins tick_low  = {1'b0};
            bins tick_high = {1'b1};
        }

        // Coverpoint for rst_n
        cp_rst_n: coverpoint rst_n {
            bins reset_active   = {1'b0};
            bins reset_inactive = {1'b1};
        }

        // Cross coverage: tick vs reset
        cx_tick_rst: cross cp_tick, cp_rst_n;

        // Coverpoint for tick transitions
        cp_tick_posedge: coverpoint tick {
            bins rising  = (0 => 1);
            bins falling = (1 => 0);
        }
    endgroup

    cg_baud_gen cg_inst;

    // =========================================================================
    // SVA Assertions (simulation always blocks)
    // =========================================================================

    // Assertion 1: tick is a single-cycle pulse
    // After tick is high, next cycle it must be low (unless counter wraps again,
    // which can't happen since DIV >= 1 and counter resets)
    always @(posedge clk) begin
        if (rst_n) begin
            if ($past(tick, 1) && rst_n && $past(rst_n, 1)) begin
                if (tick !== 1'b0) begin
                    // Only valid if DIV > 1 (if DIV==1, tick is always high)
                    if (DIV > 1) begin
                        $display("ASSERTION FAIL [tick_single_cycle]: tick was high for more than 1 cycle at time %0t", $time);
                        errors = errors + 1;
                    end
                end
            end
        end
    end

    // Assertion 2: After reset deasserted, counter and tick start at 0
    // (checked in reset test)

    // Assertion 3: tick must be 0 when reset is active
    always @(posedge clk) begin
        if (!rst_n) begin
            @(posedge clk);
            if (tick !== 1'b0) begin
                $display("ASSERTION FAIL [reset_clears_tick]: tick not 0 after reset at time %0t", $time);
                errors = errors + 1;
            end
        end
    end

    // Assertion 4: tick period check (measured in test)

    `ifdef FORMAL
    // Formal: tick is single-cycle pulse
    assert property (@(posedge clk) disable iff (!rst_n)
        tick |=> !tick || (DIV == 1));

    // Formal: After reset, tick=0
    assert property (@(posedge clk)
        !rst_n |=> (tick == 0));
    `endif

    // =========================================================================
    // Task: wait N clock cycles
    // =========================================================================
    task wait_cycles(input integer n);
        repeat(n) @(posedge clk);
    endtask

    // =========================================================================
    // Task: apply reset
    // =========================================================================
    task apply_reset(input integer cycles);
        rst_n = 1'b0;
        repeat(cycles) @(posedge clk);
        @(negedge clk);
        rst_n = 1'b1;
    endtask

    // =========================================================================
    // Main test sequence
    // =========================================================================
    initial begin
        // Initialize
        errors       = 0;
        tick_count   = 0;
        cycle_count  = 0;
        last_tick_cycle = 0;
        tick_high_cycles = 0;

        cg_inst = new();

        rst_n = 1'b0;

        $display("=== TEST START: uart_baud_gen ===");
        $display("DIV = %0d, CLK_HZ = %0d, BAUD_HZ = %0d, OVERSAMPLE = %0d",
                 DIV, CLK_HZ, BAUD_HZ, OVERSAMPLE);

        // =====================================================================
        // Phase 1: Reset for >= 5 cycles
        // =====================================================================
        $display("\n--- Phase 1: Initial Reset (10 cycles) ---");
        repeat(10) @(posedge clk);

        // Verify tick is 0 during reset
        if (tick !== 1'b0) begin
            $display("FAIL [reset_recovery]: tick should be 0 during reset, got %b", tick);
            errors = errors + 1;
        end else begin
            $display("PASS: tick=0 during reset");
        end

        @(negedge clk);
        rst_n = 1'b1;
        $display("Reset deasserted at time %0t", $time);

        // =====================================================================
        // Phase 2: Basic functional test - verify tick period = DIV
        // =====================================================================
        $display("\n--- Phase 2: Basic Functional Test (tick_period_equals_DIV) ---");

        // Wait for first tick
        fork
            begin : wait_first_tick
                integer timeout_cnt;
                timeout_cnt = 0;
                while (tick !== 1'b1) begin
                    @(posedge clk);
                    timeout_cnt = timeout_cnt + 1;
                    if (timeout_cnt > DIV * 3) begin
                        $display("FAIL: Timeout waiting for first tick");
                        errors = errors + 1;
                        disable wait_first_tick;
                    end
                end
                $display("First tick observed at time %0t (after %0d cycles from rst deassertion)",
                         $time, timeout_cnt);
            end
        join

        // Measure period between ticks over multiple cycles
        begin
            integer tick_times [0:9];
            integer i;
            integer period_ok;

            period_ok = 1;
            i = 0;

            // Record time of current tick
            tick_times[0] = $time / CLK_PERIOD;

            // Wait for 9 more ticks
            for (i = 1; i < 10; i = i + 1) begin
                @(posedge clk);
                while (tick !== 1'b1) @(posedge clk);
                tick_times[i] = $time / CLK_PERIOD;
            end

            // Check periods
            for (i = 1; i < 10; i = i + 1) begin
                tick_period_measured = tick_times[i] - tick_times[i-1];
                if (tick_period_measured !== DIV) begin
                    $display("FAIL [tick_period_equals_DIV]: Period=%0d, expected=%0d (tick %0d)",
                             tick_period_measured, DIV, i);
                    errors = errors + 1;
                    period_ok = 0;
                end
            end

            if (period_ok) begin
                $display("PASS [tick_period_equals_DIV]: All 9 measured periods = %0d cycles", DIV);
            end
        end

        // =====================================================================
        // Phase 3: Single-cycle pulse verification
        // =====================================================================
        $display("\n--- Phase 3: Single-Cycle Pulse Test (tick_is_single_cycle_pulse) ---");

        begin
            integer pulse_errors;
            integer i;

            pulse_errors = 0;

            // Wait for next tick, then check it goes low next cycle
            for (i = 0; i < 20; i = i + 1) begin
                // Wait for tick to go high
                @(posedge clk);
                while (tick !== 1'b1) @(posedge clk);

                // Sample next cycle
                @(posedge clk);
                if (tick !== 1'b0) begin
                    if (DIV > 1) begin
                        $display("FAIL [tick_is_single_cycle_pulse]: tick still high after 1 cycle (iteration %0d)", i);
                        pulse_errors = pulse_errors + 1;
                        errors = errors + 1;
                    end
                end
            end

            if (pulse_errors == 0) begin
                $display("PASS [tick_is_single_cycle_pulse]: tick is single-cycle pulse (20 checks)");
            end
        end

        // =====================================================================
        // Phase 4: Reset recovery test
        // =====================================================================
        $display("\n--- Phase 4: Reset Recovery Test (reset_recovery) ---");

        begin
            integer cycles_after_rst;

            // Assert reset mid-operation (after a few cycles)
            wait_cycles(5);
            $display("Asserting reset mid-operation at time %0t", $time);
            rst_n = 1'b0;

            // Hold reset for 3 cycles
            repeat(3) @(posedge clk);

            // Check tick is 0 during reset
            if (tick !== 1'b0) begin
                $display("FAIL [reset_recovery]: tick not cleared during reset");
                errors = errors + 1;
            end else begin
                $display("PASS: tick=0 during mid-operation reset");
            end

            // Deassert reset
            @(negedge clk);
            rst_n = 1'b1;
            $display("Reset deasserted at time %0t", $time);

            // Verify tick comes back after exactly DIV cycles
            cycles_after_rst = 0;
            @(posedge clk); // first cycle after reset
            while (tick !== 1'b1) begin
                @(posedge clk);
                cycles_after_rst = cycles_after_rst + 1;
                if (cycles_after_rst > DIV * 2) begin
                    $display("FAIL [reset_recovery]: Timeout waiting for tick after reset");
                    errors = errors + 1;
                    cycles_after_rst = -1;
                    break;
                end
            end

            if (cycles_after_rst >= 0) begin
                // cycles_after_rst should be DIV-1 (we count from 1st posedge after rst)
                if (cycles_after_rst == (DIV - 1)) begin
                    $display("PASS [reset_recovery]: First tick after reset at cycle %0d (expected %0d)",
                             cycles_after_rst, DIV-1);
                end else begin
                    $display("FAIL [reset_recovery]: First tick at cycle %0d, expected %0d",
                             cycles_after_rst, DIV-1);
                    errors = errors + 1;
                end
            end
        end

        // =====================================================================
        // Phase 5: Boundary conditions
        // =====================================================================
        $display("\n--- Phase 5: Boundary Conditions ---");

        // Apply reset for exactly 1 cycle
        rst_n = 1'b0;
        @(posedge clk);
        @(negedge clk);
        rst_n = 1'b1;
        $display("Single-cycle reset applied");

        // Verify tick=0 immediately after reset
        @(posedge clk);
        if (tick !== 1'b0) begin
            $display("FAIL [boundary]: tick not 0 on first cycle after 1-cycle reset");
            errors = errors + 1;
        end else begin
            $display("PASS: tick=0 on first cycle after 1-cycle reset");
        end

        // Wait for tick and verify
        begin
            integer cnt;
            cnt = 0;
            while (tick !== 1'b1) begin
                @(posedge clk);
                cnt = cnt + 1;
                if (cnt > DIV * 2) begin
                    $display("FAIL [boundary]: No tick after single-cycle reset");
                    errors = errors + 1;
                    cnt = -1;
                    break;
                end
            end
            if (cnt >= 0)
                $display("PASS [boundary]: Tick received after single-cycle reset (cnt=%0d)", cnt);
        end

        // =====================================================================
        // Phase 6: Back-to-back / stress test
        // =====================================================================
        $display("\n--- Phase 6: Back-to-Back / Stress Test ---");

        begin
            integer tick_cnt;
            integer total_cycles;
            integer expected_ticks;
            integer tolerance;

            tick_cnt    = 0;
            total_cycles = DIV * 100;
            expected_ticks = 100;
            tolerance = 1; // allow 1 tick tolerance

            // Apply clean reset
            rst_n = 1'b0;
            repeat(5) @(posedge clk);
            @(negedge clk);
            rst_n = 1'b1;

            // Count ticks over 100*DIV cycles
            repeat(total_cycles) begin
                @(posedge clk);
                if (tick) tick_cnt = tick_cnt + 1;
            end

            if ((tick_cnt >= expected_ticks - tolerance) &&
                (tick_cnt <= expected_ticks + tolerance)) begin
                $display("PASS [stress]: %0d ticks in %0d cycles (expected ~%0d)",
                         tick_cnt, total_cycles, expected_ticks);
            end else begin
                $display("FAIL [stress]: %0d ticks in %0d cycles (expected ~%0d)",
                         tick_cnt, total_cycles, expected_ticks);
                errors = errors + 1;
            end
        end

        // =====================================================================
        // Phase 7: Multiple resets stress
        // =====================================================================
        $display("\n--- Phase 7: Multiple Reset Stress ---");

        begin
            integer i;
            for (i = 0; i < 10; i = i + 1) begin
                // Random-ish reset timing
                wait_cycles(i * 3 + 1);
                rst_n = 1'b0;
                wait_cycles(2);
                @(negedge clk);
                rst_n = 1'b1;

                // Verify tick=0 right after reset
                @(posedge clk);
                if (tick !== 1'b0) begin
                    $display("FAIL [multi_reset]: tick not 0 after reset iteration %0d", i);
                    errors = errors + 1;
                end
            end
            $display("PASS [multi_reset]: 10 reset cycles completed");
        end

        // =====================================================================
        // Final reset and coverage drain
        // =====================================================================
        $display("\n--- Final: Coverage drain ---");
        rst_n = 1'b0;
        repeat(5) @(posedge clk);
        @(negedge clk);
        rst_n = 1'b1;
        // Run for a few more DIV periods to improve coverage
        wait_cycles(DIV * 10);

        // =====================================================================
        // Results
        // =====================================================================
        $display("\n=== TEST COMPLETE ===");
        $display("Coverage: %0.1f%%", cg_inst.get_coverage());

        if (errors == 0) begin
            $display("RESULT: ALL TESTS PASSED");
        end else begin
            $display("RESULT: %0d ERRORS DETECTED", errors);
        end

        $finish;
    end

    // =========================================================================
    // Continuous tick monitoring (for assertions)
    // =========================================================================
    always @(posedge clk) begin
        if (rst_n) begin
            if (tick) begin
                tick_count = tick_count + 1;
            end
        end
    end

    // =========================================================================
    // Waveform dump
    // =========================================================================
    initial begin
        $dumpfile("tb_uart_baud_gen.vcd");
        $dumpvars(0, tb_uart_baud_gen);
    end

endmodule
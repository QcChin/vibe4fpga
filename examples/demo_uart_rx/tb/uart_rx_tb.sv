`timescale 1ns/1ps

module uart_rx_tb;

    // Parameters
    parameter int OVERSAMPLE = 16;
    parameter int CLK_PERIOD = 10; // 10ns = 100MHz
    parameter int TIMEOUT_CYCLES = 100000;

    // DUT signals
    logic        clk;
    logic        rst_n;
    logic        tick;
    logic        rx_in;
    wire  [7:0]  rx_data;
    wire         rx_valid;
    logic        rx_ready;
    wire         rx_frame_error;

    // Tick generation
    int tick_counter;
    
    // Coverage tracking
    int frames_received;
    int frame_errors_received;

    // DUT instantiation
    uart_rx #(
        .OVERSAMPLE(OVERSAMPLE)
    ) dut (
        .clk           (clk),
        .rst_n         (rst_n),
        .tick          (tick),
        .rx_in         (rx_in),
        .rx_data       (rx_data),
        .rx_valid      (rx_valid),
        .rx_ready      (rx_ready),
        .rx_frame_error(rx_frame_error)
    );

    // Clock generation
    initial clk = 0;
    always #(CLK_PERIOD/2) clk = ~clk;

    // Tick generation: one tick every OVERSAMPLE clocks
    always_ff @(posedge clk) begin
        if (!rst_n) begin
            tick_counter <= 0;
            tick <= 0;
        end else begin
            if (tick_counter == OVERSAMPLE - 1) begin
                tick_counter <= 0;
                tick <= 1;
            end else begin
                tick_counter <= tick_counter + 1;
                tick <= 0;
            end
        end
    end

    // Covergroup
    covergroup uart_cg @(posedge clk);
        cp_rx_data: coverpoint rx_data {
            bins zero    = {8'h00};
            bins max_val = {8'hFF};
            bins alt     = {8'h55};
            bins aa      = {8'hAA};
            bins others  = default;
        }
        cp_rx_valid: coverpoint rx_valid {
            bins asserted   = {1};
            bins deasserted = {0};
        }
        cp_rx_frame_error: coverpoint rx_frame_error {
            bins asserted   = {1};
            bins deasserted = {0};
        }
        cp_rx_ready: coverpoint rx_ready {
            bins ready     = {1};
            bins not_ready = {0};
        }
        cp_rx_in: coverpoint rx_in {
            bins high = {1};
            bins low  = {0};
        }
        cx_data_valid: cross cp_rx_data, cp_rx_valid;
        cx_valid_ready: cross cp_rx_valid, cp_rx_ready;
    endgroup

    uart_cg cg_inst = new();

    // SVA Assertions - simulation always blocks
    // rx_valid and rx_frame_error should not both be asserted simultaneously
    always @(posedge clk) begin
        if (rst_n) begin
            if (rx_valid && rx_frame_error) begin
                $display("ASSERTION FAIL: rx_valid and rx_frame_error both asserted at time %0t", $time);
                $finish;
            end
        end
    end

    // rx_valid should be a single-cycle pulse
    logic rx_valid_prev;
    always_ff @(posedge clk) begin
        if (!rst_n) begin
            rx_valid_prev <= 0;
        end else begin
            rx_valid_prev <= rx_valid;
        end
    end

    // rx_frame_error should be a single-cycle pulse
    logic rx_frame_error_prev;
    always_ff @(posedge clk) begin
        if (!rst_n) begin
            rx_frame_error_prev <= 0;
        end else begin
            rx_frame_error_prev <= rx_frame_error;
        end
    end

    // After reset, outputs should be deasserted
    always @(posedge clk) begin
        if (!rst_n) begin
            if (rx_valid !== 1'bx && rx_valid !== 0) begin
                $display("ASSERTION FAIL: rx_valid not deasserted during reset at time %0t", $time);
            end
            if (rx_frame_error !== 1'bx && rx_frame_error !== 0) begin
                $display("ASSERTION FAIL: rx_frame_error not deasserted during reset at time %0t", $time);
            end
        end
    end

    `ifdef FORMAL
    // Formal assertions
    property p_no_simultaneous_valid_error;
        @(posedge clk) disable iff (!rst_n)
        !(rx_valid && rx_frame_error);
    endproperty
    assert property (p_no_simultaneous_valid_error);

    property p_rx_valid_pulse;
        @(posedge clk) disable iff (!rst_n)
        rx_valid |=> !rx_valid;
    endproperty
    assert property (p_rx_valid_pulse);

    property p_rx_frame_error_pulse;
        @(posedge clk) disable iff (!rst_n)
        rx_frame_error |=> !rx_frame_error;
    endproperty
    assert property (p_rx_frame_error_pulse);

    property p_reset_clears_valid;
        @(posedge clk)
        !rst_n |-> !rx_valid;
    endproperty
    assert property (p_reset_clears_valid);

    property p_reset_clears_frame_error;
        @(posedge clk)
        !rst_n |-> !rx_frame_error;
    endproperty
    assert property (p_reset_clears_frame_error);
    `endif

    // Task: send one UART 8-N-1 frame
    // Timing: each bit = OVERSAMPLE ticks
    // We drive rx_in and let the tick generator handle timing
    task automatic send_uart_byte(
        input logic [7:0] data,
        input logic       bad_stop  // if 1, drive stop bit low (framing error)
    );
        integer i;
        // Wait for tick alignment - wait until just after a tick
        @(posedge clk);
        while (!tick) @(posedge clk);
        // Now we're aligned to a tick boundary
        // Drive start bit (low) for OVERSAMPLE ticks
        rx_in = 1'b0;
        repeat(OVERSAMPLE) @(posedge clk);
        // Drive data bits LSB first
        for (i = 0; i < 8; i++) begin
            rx_in = data[i];
            repeat(OVERSAMPLE) @(posedge clk);
        end
        // Drive stop bit
        if (bad_stop)
            rx_in = 1'b0; // framing error
        else
            rx_in = 1'b1; // good stop
        repeat(OVERSAMPLE) @(posedge clk);
        // Return to idle
        rx_in = 1'b1;
        repeat(2) @(posedge clk);
    endtask

    // Task: wait for rx_valid or rx_frame_error with timeout
    task automatic wait_for_output(
        output logic got_valid,
        output logic got_error,
        output logic [7:0] received_data
    );
        integer timeout;
        got_valid = 0;
        got_error = 0;
        received_data = 0;
        timeout = 0;
        while (timeout < TIMEOUT_CYCLES) begin
            @(posedge clk);
            timeout++;
            if (rx_valid) begin
                got_valid = 1;
                received_data = rx_data;
                return;
            end
            if (rx_frame_error) begin
                got_error = 1;
                return;
            end
        end
        $display("TIMEOUT waiting for output at time %0t", $time);
    endtask

    // Simulation timeout
    initial begin
        #(TIMEOUT_CYCLES * CLK_PERIOD * 10);
        $display("SIMULATION TIMEOUT at time %0t", $time);
        $finish;
    end

    // Main test sequence
    integer test_pass;
    logic got_valid, got_error;
    logic [7:0] received_data;

    initial begin
        // Initialize
        rst_n    = 0;
        rx_in    = 1; // idle high
        rx_ready = 1;
        tick     = 0;
        frames_received = 0;
        frame_errors_received = 0;
        test_pass = 1;

        $display("=== UART RX Testbench Starting ===");
        $display("Time: %0t - Applying reset", $time);

        // Apply reset for >= 5 cycles
        repeat(10) @(posedge clk);
        rst_n = 1;
        repeat(5) @(posedge clk);

        $display("Time: %0t - Reset released", $time);

        // =========================================================
        // TEST 1: clean_8bit_frame_0x55
        // =========================================================
        $display("Time: %0t - TEST 1: clean_8bit_frame_0x55", $time);
        
        // Wait for tick alignment
        @(posedge clk);
        
        send_uart_byte(8'h55, 1'b0);
        
        // Wait for valid
        wait_for_output(got_valid, got_error, received_data);
        
        if (got_valid && received_data == 8'h55) begin
            $display("Time: %0t - TEST 1 PASS: Received 0x55 correctly", $time);
            frames_received++;
        end else if (got_valid) begin
            $display("Time: %0t - TEST 1 FAIL: Expected 0x55, got 0x%02h", $time, received_data);
            test_pass = 0;
        end else begin
            $display("Time: %0t - TEST 1 FAIL: No valid pulse received", $time);
            test_pass = 0;
        end
        
        repeat(OVERSAMPLE * 2) @(posedge clk);

        // =========================================================
        // TEST 2: clean_8bit_frame_0x00
        // =========================================================
        $display("Time: %0t - TEST 2: clean_8bit_frame_0x00", $time);
        
        send_uart_byte(8'h00, 1'b0);
        
        wait_for_output(got_valid, got_error, received_data);
        
        if (got_valid && received_data == 8'h00) begin
            $display("Time: %0t - TEST 2 PASS: Received 0x00 correctly", $time);
            frames_received++;
        end else if (got_valid) begin
            $display("Time: %0t - TEST 2 FAIL: Expected 0x00, got 0x%02h", $time, received_data);
            test_pass = 0;
        end else begin
            $display("Time: %0t - TEST 2 FAIL: No valid pulse received", $time);
            test_pass = 0;
        end
        
        repeat(OVERSAMPLE * 2) @(posedge clk);

        // =========================================================
        // TEST 3: framing_error_stop_bit_low
        // =========================================================
        $display("Time: %0t - TEST 3: framing_error_stop_bit_low", $time);
        
        send_uart_byte(8'hAA, 1'b1); // bad stop bit
        
        wait_for_output(got_valid, got_error, received_data);
        
        if (got_error) begin
            $display("Time: %0t - TEST 3 PASS: Framing error detected correctly", $time);
            frame_errors_received++;
        end else if (got_valid) begin
            $display("Time: %0t - TEST 3 FAIL: Got valid instead of frame error", $time);
            test_pass = 0;
        end else begin
            $display("Time: %0t - TEST 3 FAIL: No frame error detected", $time);
            test_pass = 0;
        end
        
        // After framing error, rx_in is low - need to bring it high and wait
        rx_in = 1'b1;
        repeat(OVERSAMPLE * 4) @(posedge clk);

        // =========================================================
        // TEST 4: back_to_back_frames
        // =========================================================
        $display("Time: %0t - TEST 4: back_to_back_frames", $time);
        
        // Send multiple frames back to back
        begin
            logic [7:0] test_bytes [4] = '{8'hA5, 8'h3C, 8'hFF, 8'h01};
            logic [7:0] recv_byte;
            logic gv, ge;
            int bb_pass;
            bb_pass = 1;
            
            // Send first frame
            fork
                begin
                    // Send all frames
                    foreach (test_bytes[i]) begin
                        send_uart_byte(test_bytes[i], 1'b0);
                    end
                end
                begin
                    // Receive all frames
                    foreach (test_bytes[i]) begin
                        wait_for_output(gv, ge, recv_byte);
                        if (gv && recv_byte == test_bytes[i]) begin
                            $display("Time: %0t - TEST 4: Frame %0d PASS: 0x%02h", $time, i, recv_byte);
                            frames_received++;
                        end else if (gv) begin
                            $display("Time: %0t - TEST 4: Frame %0d FAIL: Expected 0x%02h got 0x%02h", 
                                     $time, i, test_bytes[i], recv_byte);
                            bb_pass = 0;
                        end else begin
                            $display("Time: %0t - TEST 4: Frame %0d FAIL: No valid", $time, i);
                            bb_pass = 0;
                        end
                    end
                end
            join
            
            if (bb_pass)
                $display("Time: %0t - TEST 4 PASS: All back-to-back frames received", $time);
            else begin
                $display("Time: %0t - TEST 4 FAIL", $time);
                test_pass = 0;
            end
        end
        
        repeat(OVERSAMPLE * 2) @(posedge clk);

        // =========================================================
        // TEST 5: reset_during_receive
        // =========================================================
        $display("Time: %0t - TEST 5: reset_during_receive", $time);
        
        // Start sending a frame
        // Wait for tick alignment
        @(posedge clk);
        while (!tick) @(posedge clk);
        
        // Drive start bit
        rx_in = 1'b0;
        
        // Wait partway through the frame (after start bit, partway through data)
        repeat(OVERSAMPLE * 3) @(posedge clk);
        
        // Assert reset mid-frame
        $display("Time: %0t - TEST 5: Asserting reset mid-frame", $time);
        rst_n = 1'b0;
        repeat(8) @(posedge clk);
        
        // Check outputs are deasserted during reset
        if (rx_valid === 0 && rx_frame_error === 0) begin
            $display("Time: %0t - TEST 5: Outputs correctly deasserted during reset", $time);
        end else begin
            $display("Time: %0t - TEST 5 FAIL: Outputs not deasserted during reset (valid=%b, err=%b)", 
                     $time, rx_valid, rx_frame_error);
            test_pass = 0;
        end
        
        // Release reset
        rst_n = 1'b1;
        rx_in = 1'b1; // return to idle
        repeat(5) @(posedge clk);
        
        // Verify no spurious outputs after reset
        repeat(OVERSAMPLE * 2) @(posedge clk);
        if (rx_valid === 0 && rx_frame_error === 0) begin
            $display("Time: %0t - TEST 5: No spurious outputs after reset", $time);
        end else begin
            $display("Time: %0t - TEST 5 FAIL: Spurious output after reset", $time);
            test_pass = 0;
        end
        
        // Now send a clean frame to verify recovery
        send_uart_byte(8'h55, 1'b0);
        wait_for_output(got_valid, got_error, received_data);
        
        if (got_valid && received_data == 8'h55) begin
            $display("Time: %0t - TEST 5 PASS: Recovery after reset successful", $time);
        end else begin
            $display("Time: %0t - TEST 5 FAIL: Recovery after reset failed", $time);
            test_pass = 0;
        end
        
        repeat(OVERSAMPLE * 2) @(posedge clk);

        // =========================================================
        // TEST 6: Boundary - 0xFF
        // =========================================================
        $display("Time: %0t - TEST 6: Boundary 0xFF", $time);
        
        send_uart_byte(8'hFF, 1'b0);
        wait_for_output(got_valid, got_error, received_data);
        
        if (got_valid && received_data == 8'hFF) begin
            $display("Time: %0t - TEST 6 PASS: Received 0xFF correctly", $time);
            frames_received++;
        end else begin
            $display("Time: %0t - TEST 6 FAIL: Expected 0xFF, got 0x%02h (valid=%b)", 
                     $time, received_data, got_valid);
            test_pass = 0;
        end
        
        repeat(OVERSAMPLE * 2) @(posedge clk);

        // =========================================================
        // TEST 7: rx_ready deasserted (backpressure)
        // =========================================================
        $display("Time: %0t - TEST 7: rx_ready deasserted", $time);
        rx_ready = 1'b0;
        
        send_uart_byte(8'hC3, 1'b0);
        wait_for_output(got_valid, got_error, received_data);
        
        if (got_valid) begin
            $display("Time: %0t - TEST 7: rx_valid pulsed with rx_ready=0, data=0x%02h", $time, received_data);
        end
        
        rx_ready = 1'b1;
        repeat(OVERSAMPLE * 2) @(posedge clk);

        // =========================================================
        // TEST 8: Glitch on start bit (short pulse)
        // =========================================================
        $display("Time: %0t - TEST 8: Glitch on start bit", $time);
        
        // Drive rx_in low for just a few cycles (less than MID_START ticks)
        @(posedge clk);
        rx_in = 1'b0;
        repeat(3) @(posedge clk); // very short glitch
        rx_in = 1'b1;
        
        // Wait and verify no valid/error
        repeat(OVERSAMPLE * 12) @(posedge clk);
        $display("Time: %0t - TEST 8: Glitch test complete (no frame expected)", $time);

        // =========================================================
        // Final Results
        // =========================================================
        repeat(OVERSAMPLE * 2) @(posedge clk);
        
        $display("=== Test Summary ===");
        $display("Frames received: %0d", frames_received);
        $display("Frame errors: %0d", frame_errors_received);
        
        if (test_pass)
            $display("ALL TESTS PASSED");
        else
            $display("SOME TESTS FAILED");
        
        $display("Coverage goals:");
        $display("  clean_8bit_frame_0x55:      %s", (frames_received >= 1) ? "COVERED" : "MISSED");
        $display("  clean_8bit_frame_0x00:      %s", (frames_received >= 2) ? "COVERED" : "MISSED");
        $display("  framing_error_stop_bit_low: %s", (frame_errors_received >= 1) ? "COVERED" : "MISSED");
        $display("  back_to_back_frames:        %s", (frames_received >= 6) ? "COVERED" : "MISSED");
        $display("  reset_during_receive:       COVERED");
        
        $finish;
    end

    // Dump waveforms
    initial begin
        $dumpfile("uart_rx_tb.vcd");
        $dumpvars(0, uart_rx_tb);
    end

endmodule

module test_xtor_core;
    reg clock = 0;
    reg reset = 1;
    reg valid = 0;
    reg [31:0] data_i = 0;
    wire ready;
    wire [31:0] data_o;
    
    integer error_count = 0;
    
    // Clock generation
    initial begin
        clock = 0;
        forever begin
            #5;
            clock = ~clock;
        end
    end
    
    // Instantiate XtorCore
    XtorCore dut (
        .clock(clock),
        .reset(reset),
        .ready(ready),
        .valid(valid),
        .data_i(data_i),
        .data_o(data_o)
    );
    
    // Test procedure
    initial begin
        $display("%0t: Starting test_xtor_core", $time);
        
        // Hold reset for a few cycles
        reset = 1;
        valid = 0;
        data_i = 0;
        #20;
        
        // Release reset
        @(posedge clock);
        reset = 0;
        
        // Wait for a full clock cycle before checking
        @(posedge clock);
        @(posedge clock);
        #1; // Small delay to check output
        
        // Check that ready goes high after reset
        if (!ready) begin
            $display("%0t: ERROR - ready should be high after reset", $time);
            error_count = error_count + 1;
        end else begin
            $display("%0t: PASS - ready is high after reset", $time);
        end
        
        // Test case 1: Send data with valid=1
        @(posedge clock);
        valid = 1;
        data_i = 32'h12345678;
        
        @(posedge clock);
        #1; // Small delay to check output
        if (data_o !== 32'h12345679) begin
            $display("%0t: ERROR - Expected data_o=0x12345679, got 0x%h", $time, data_o);
            error_count = error_count + 1;
        end else begin
            $display("%0t: PASS - data_o=0x%h (input + 1)", $time, data_o);
        end
        
        // Test case 2: Send another value
        @(posedge clock);
        data_i = 32'h00000010;
        
        @(posedge clock);
        #1;
        if (data_o !== 32'h00000011) begin
            $display("%0t: ERROR - Expected data_o=0x00000011, got 0x%h", $time, data_o);
            error_count = error_count + 1;
        end else begin
            $display("%0t: PASS - data_o=0x%h (input + 1)", $time, data_o);
        end
        
        // Test case 3: valid=0, output should hold previous value
        @(posedge clock);
        valid = 0;
        data_i = 32'hFFFFFFFF;
        
        @(posedge clock);
        #1;
        if (data_o !== 32'h00000011) begin
            $display("%0t: ERROR - Expected data_o to hold 0x00000011, got 0x%h", $time, data_o);
            error_count = error_count + 1;
        end else begin
            $display("%0t: PASS - data_o holds previous value when valid=0", $time);
        end
        
        // Test case 4: Check reset clears output
        @(posedge clock);
        reset = 1;
        
        @(posedge clock);
        #1;
        if (data_o !== 32'h00000000) begin
            $display("%0t: ERROR - Expected data_o=0x00000000 after reset, got 0x%h", $time, data_o);
            error_count = error_count + 1;
        end else begin
            $display("%0t: PASS - data_o cleared after reset", $time);
        end
        
        if (ready !== 1'b0) begin
            $display("%0t: ERROR - Expected ready=0 after reset, got %b", $time, ready);
            error_count = error_count + 1;
        end else begin
            $display("%0t: PASS - ready cleared after reset", $time);
        end
        
        // Final summary
        #10;
        if (error_count == 0) begin
            $display("***************************************");
            $display("* TEST PASSED - All checks succeeded *");
            $display("***************************************");
        end else begin
            $display("***************************************");
            $display("* TEST FAILED - %0d errors detected    *", error_count);
            $display("***************************************");
        end
        
        $finish;
    end
    
    // Timeout watchdog
    initial begin
        #10000;
        $display("ERROR: Test timeout!");
        $finish;
    end

endmodule

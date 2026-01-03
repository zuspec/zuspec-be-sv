module tb;
    logic clock = 0;
    logic reset = 1;
    logic [31:0] result;
    integer error_count = 0;
    
    // Clock generation
    initial begin
        forever #5 clock = ~clock;
    end
    
    // Instantiate Xtor
    {module_name} dut (
        .clock(clock),
        .reset(reset)
    );
    
    // Test procedure
    initial begin
        $display("%0t: Starting test_xtor_smoke_sim", $time);
        
        // Hold reset
        reset = 1;
        #20;
        
        // Release reset
        @(posedge clock);
        reset = 0;
        @(posedge clock);
        
        // Test case 1: Send 0x12345678
        $display("%0t: Sending 0x12345678", $time);
        dut.xtor_if.send(result, 32'h12345678);
        $display("%0t: Returned 0x%h", $time, result);
        
        if (result !== 32'h12345679) begin
            $display("%0t: ERROR - Expected 0x12345679, got 0x%h", $time, result);
            error_count = error_count + 1;
        end else begin
            $display("%0t: PASS - send(0x12345678) returned 0x12345679", $time);
        end
        
        // Test case 2: Send 0x00000010
        $display("%0t: Sending 0x00000010", $time);
        dut.xtor_if.send(result, 32'h00000010);
        $display("%0t: Returned 0x%h", $time, result);
        
        if (result !== 32'h00000011) begin
            $display("%0t: ERROR - Expected 0x00000011, got 0x%h", $time, result);
            error_count = error_count + 1;
        end else begin
            $display("%0t: PASS - send(0x00000010) returned 0x00000011", $time);
        end
        
        // Test case 3: Send 0x00000000
        $display("%0t: Sending 0x00000000", $time);
        dut.xtor_if.send(result, 32'h00000000);
        $display("%0t: Returned 0x%h", $time, result);
        
        if (result !== 32'h00000001) begin
            $display("%0t: ERROR - Expected 0x00000001, got 0x%h", $time, result);
            error_count = error_count + 1;
        end else begin
            $display("%0t: PASS - send(0x00000000) returned 0x00000001", $time);
        end
        
        // Final summary
        #10;
        if (error_count == 0) begin
            $display("***************************************");
            $display("* TEST PASSED - All tests successful! *");
            $display("***************************************");
        end else begin
            $display("*************************************");
            $display("* TEST FAILED - %0d errors detected    *", error_count);
            $display("*************************************");
        end
        
        #20;
        $finish;
    end
    
    // Timeout
    initial begin
        #10000;
        $display("ERROR: Test timeout!");
        $finish;
    end

endmodule

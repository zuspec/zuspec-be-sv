module tb;
    logic clock = 0;
    logic reset = 1;
    logic [31:0] result;
    
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
        $display("%0t: Starting test_xtor_interface_bundle_sim", $time);
        
        // Hold reset
        reset = 1;
        #20;
        $display("%0t: Holding reset...", $time);
        
        // Release reset
        @(posedge clock);
        $display("%0t: Releasing reset", $time);
        reset = 0;
        @(posedge clock);
        $display("%0t: Reset released", $time);
        
        // Call the transactor interface task
        $display("%0t: Calling dut.xtor_if.send(32'h42)", $time);
        
        dut.xtor_if.send(result, 32'h42);
        
        $display("%0t: Returned from send, result = 0x%h", $time, result);
        
        // Check result (should be 0x43 = 0x42 + 1)
        if (result === 32'h43) begin
            $display("*************************************");
            $display("* TEST PASSED - Result is correct! *");
            $display("*************************************");
        end else begin
            $display("*************************************");
            $display("* TEST FAILED - Expected 0x43, got 0x%h", result);
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

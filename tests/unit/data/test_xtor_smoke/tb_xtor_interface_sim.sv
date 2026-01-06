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
        $display("%0t: Starting test_xtor_interface_sim", $time);
        
        // Hold reset
        reset = 1;
        #20;
        $display("%0t: Holding reset...", $time);
        
        // Release reset
        @(posedge clock);
        $display("%0t: Releasing reset", $time);
        reset = 0;
        @(posedge clock);
        $display("%0t: Reset released, ready=%b", $time, dut.ready);
        
        // Call the transactor interface task
        $display("%0t: Calling dut.xtor_if.send(32'h42)", $time);
        $display("%0t:   Before call: reset=%b, ready=%b, valid=%b", $time, reset, dut.ready, dut.valid);
        
        dut.xtor_if.send(result, 32'h42);
        
        $display("%0t: Returned from send, result = 0x%h", $time, result);
        $display("%0t:   After call: ready=%b, valid=%b, data_i=0x%h, data_o=0x%h", 
                 $time, dut.ready, dut.valid, dut.data_i, dut.data_o);
        
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
    
    // Monitor key signals
    initial begin
        $monitor("%0t: reset=%b ready=%b valid=%b data_i=0x%h data_o=0x%h", 
                 $time, reset, dut.ready, dut.valid, dut.data_i, dut.data_o);
    end
    
    // Timeout
    initial begin
        #10000;
        $display("ERROR: Test timeout!");
        $display("  Final state: reset=%b ready=%b valid=%b", reset, dut.ready, dut.valid);
        $finish;
    end

endmodule

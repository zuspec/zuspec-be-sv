########
Examples
########

This page provides complete examples of Zuspec components and their generated SystemVerilog.

Simple Counter
==============

A basic up-counter with synchronous reset.

Zuspec Source
-------------

.. code-block:: python

   import zuspec.dataclasses as zdc
   
   @zdc.dataclass
   class Counter(zdc.Component):
       """Simple up-counter with reset."""
       clock : zdc.bit = zdc.input()
       reset : zdc.bit = zdc.input()
       count : zdc.bit32 = zdc.output()

       @zdc.sync(clock=lambda s:s.clock, reset=lambda s:s.reset)
       def _count(self):
           if self.reset:
               self.count = 0
           else:
               self.count += 1

Generated SystemVerilog
-----------------------

.. code-block:: systemverilog

   module Counter(
     input logic clock,
     input logic reset,
     output logic [31:0] count
   );

     always @(posedge clock or posedge reset) begin
       if (reset) begin
         count <= 0;
       end else begin
         count <= count + 1;
       end
     end

   endmodule

Parameterized FIFO
==================

A FIFO with configurable width and depth.

Zuspec Source
-------------

.. code-block:: python

   @zdc.dataclass
   class FIFO(zdc.Component):
       """Parameterized FIFO."""
       DATA_WIDTH : int = zdc.const(default=32)
       DEPTH : int = zdc.const(default=16)
       
       clock : zdc.bit = zdc.input()
       reset : zdc.bit = zdc.input()
       
       # Write interface
       wr_en : zdc.bit = zdc.input()
       wr_data : zdc.int = zdc.input(width=lambda s: s.DATA_WIDTH)
       full : zdc.bit = zdc.output()
       
       # Read interface
       rd_en : zdc.bit = zdc.input()
       rd_data : zdc.int = zdc.output(width=lambda s: s.DATA_WIDTH)
       empty : zdc.bit = zdc.output()
       
       # Internal state
       wr_ptr : zdc.int = zdc.field(bits=8)
       rd_ptr : zdc.int = zdc.field(bits=8)
       count : zdc.int = zdc.field(bits=8)
       
       @zdc.sync(clock=lambda s:s.clock, reset=lambda s:s.reset)
       def _fifo_ctrl(self):
           if self.reset:
               self.wr_ptr = 0
               self.rd_ptr = 0
               self.count = 0
               self.full = 0
               self.empty = 1
           else:
               # Update count
               if self.wr_en and not self.full:
                   if not (self.rd_en and not self.empty):
                       self.count += 1
               elif self.rd_en and not self.empty:
                   self.count -= 1
               
               # Update full/empty flags
               self.full = (self.count == self.DEPTH)
               self.empty = (self.count == 0)
               
               # Update pointers
               if self.wr_en and not self.full:
                   self.wr_ptr += 1
               if self.rd_en and not self.empty:
                   self.rd_ptr += 1

Generated SystemVerilog
-----------------------

.. code-block:: systemverilog

   module FIFO #(
     parameter int DATA_WIDTH = 32,
     parameter int DEPTH = 16
   )(
     input logic clock,
     input logic reset,
     input logic wr_en,
     input logic [(DATA_WIDTH-1):0] wr_data,
     output logic full,
     input logic rd_en,
     output logic [(DATA_WIDTH-1):0] rd_data,
     output logic empty
   );

     logic [7:0] wr_ptr;
     logic [7:0] rd_ptr;
     logic [7:0] count;

     always @(posedge clock or posedge reset) begin
       if (reset) begin
         wr_ptr <= 0;
         rd_ptr <= 0;
         count <= 0;
         full <= 0;
         empty <= 1;
       end else begin
         // Counter update logic
         if (wr_en && !full) begin
           if (!(rd_en && !empty)) begin
             count <= count + 1;
           end
         end else if (rd_en && !empty) begin
           count <= count - 1;
         end
         
         // Flag updates
         full <= (count == DEPTH);
         empty <= (count == 0);
         
         // Pointer updates
         if (wr_en && !full) begin
           wr_ptr <= wr_ptr + 1;
         end
         if (rd_en && !empty) begin
           rd_ptr <= rd_ptr + 1;
         end
       end
     end

   endmodule

State Machine
=============

A traffic light controller FSM.

Zuspec Source
-------------

.. code-block:: python

   @zdc.dataclass
   class TrafficLight(zdc.Component):
       """Traffic light controller FSM."""
       clock : zdc.bit = zdc.input()
       reset : zdc.bit = zdc.input()
       
       red : zdc.bit = zdc.output()
       yellow : zdc.bit = zdc.output()
       green : zdc.bit = zdc.output()
       
       state : zdc.bit8 = zdc.field()
       timer : zdc.bit32 = zdc.field()
       
       # States
       STATE_RED = 0
       STATE_GREEN = 1
       STATE_YELLOW = 2
       
       @zdc.sync(clock=lambda s:s.clock, reset=lambda s:s.reset)
       def _fsm(self):
           if self.reset:
               self.state = self.STATE_RED
               self.timer = 0
               self.red = 1
               self.yellow = 0
               self.green = 0
           else:
               self.timer += 1
               
               match self.state:
                   case self.STATE_RED:
                       self.red = 1
                       self.yellow = 0
                       self.green = 0
                       if self.timer >= 100:
                           self.state = self.STATE_GREEN
                           self.timer = 0
                   
                   case self.STATE_GREEN:
                       self.red = 0
                       self.yellow = 0
                       self.green = 1
                       if self.timer >= 80:
                           self.state = self.STATE_YELLOW
                           self.timer = 0
                   
                   case self.STATE_YELLOW:
                       self.red = 0
                       self.yellow = 1
                       self.green = 0
                       if self.timer >= 20:
                           self.state = self.STATE_RED
                           self.timer = 0

Generated SystemVerilog
-----------------------

.. code-block:: systemverilog

   module TrafficLight(
     input logic clock,
     input logic reset,
     output logic red,
     output logic yellow,
     output logic green
   );

     logic [7:0] state;
     logic [31:0] timer;

     always @(posedge clock or posedge reset) begin
       if (reset) begin
         state <= 0;
         timer <= 0;
         red <= 1;
         yellow <= 0;
         green <= 0;
       end else begin
         timer <= timer + 1;
         
         case (state)
           0: begin  // STATE_RED
             red <= 1;
             yellow <= 0;
             green <= 0;
             if (timer >= 100) begin
               state <= 1;
               timer <= 0;
             end
           end
           
           1: begin  // STATE_GREEN
             red <= 0;
             yellow <= 0;
             green <= 1;
             if (timer >= 80) begin
               state <= 2;
               timer <= 0;
             end
           end
           
           2: begin  // STATE_YELLOW
             red <= 0;
             yellow <= 1;
             green <= 0;
             if (timer >= 20) begin
               state <= 0;
               timer <= 0;
             end
           end
         endcase
       end
     end

   endmodule

Hierarchical System
===================

A system with multiple component instances.

Zuspec Source
-------------

.. code-block:: python

   @zdc.dataclass
   class Adder(zdc.Component):
       """32-bit adder."""
       a : zdc.bit32 = zdc.input()
       b : zdc.bit32 = zdc.input()
       sum : zdc.bit32 = zdc.output()
       
       @zdc.sync(clock=lambda s:s.clock)
       def _add(self):
           self.sum = self.a + self.b

   @zdc.dataclass
   class Accumulator(zdc.Component):
       """Accumulator using adder."""
       clock : zdc.bit = zdc.input()
       reset : zdc.bit = zdc.input()
       data_in : zdc.bit32 = zdc.input()
       sum_out : zdc.bit32 = zdc.output()
       
       adder : Adder = zdc.inst()
       accum : zdc.bit32 = zdc.field()
       
       def __bind__(self):
           return {
               self.adder.a : self.accum,
               self.adder.b : self.data_in,
               self.adder.sum : self.sum_out
           }
       
       @zdc.sync(clock=lambda s:s.clock, reset=lambda s:s.reset)
       def _accum(self):
           if self.reset:
               self.accum = 0
           else:
               self.accum = self.sum_out

Generated SystemVerilog
-----------------------

.. code-block:: systemverilog

   module Adder(
     input logic [31:0] a,
     input logic [31:0] b,
     output logic [31:0] sum
   );

     always @(posedge clock) begin
       sum <= a + b;
     end

   endmodule

   module Accumulator(
     input logic clock,
     input logic reset,
     input logic [31:0] data_in,
     output logic [31:0] sum_out
   );

     logic [31:0] accum;
     logic [31:0] adder_sum;

     Adder adder (
       .a(accum),
       .b(data_in),
       .sum(adder_sum)
     );
     
     assign sum_out = adder_sum;

     always @(posedge clock or posedge reset) begin
       if (reset) begin
         accum <= 0;
       end else begin
         accum <= sum_out;
       end
     end

   endmodule

Export Interface Example
=========================

A transactor with export interface.

Zuspec Source
-------------

.. code-block:: python

   from typing import Protocol

   class SendIF(Protocol):
       """Send interface protocol."""
       async def send(self, data: int) -> int: ...

   @zdc.dataclass
   class Transactor(zdc.Component):
       """Component with export interface."""
       clock : zdc.bit = zdc.input()
       reset : zdc.bit = zdc.input()
       
       send_if : SendIF = zdc.export()
       
       data_out : zdc.bit32 = zdc.output()
       valid : zdc.bit = zdc.output()
       
       def __bind__(self):
           return {
               self.send_if.send : self._send_impl
           }
       
       async def _send_impl(self, data: int) -> int:
           """Implementation of send method."""
           self.data_out = data
           self.valid = 1
           await self.posedge(self.clock)
           self.valid = 0
           return data * 2

Generated SystemVerilog
-----------------------

Interface:

.. code-block:: systemverilog

   interface Transactor_send_if;
     
     logic [31:0] data_out = 0;
     logic valid = 0;
     
     task send(
       input logic [31:0] data,
       output logic [31:0] __ret);
       
       $display("%0t: [send] Task started", $time);
       
       data_out = data;
       valid = 1;
       @(posedge clock);
       valid = 0;
       __ret = data * 2;
       
       $display("%0t: [send] Task completed", $time);
       
     endtask

   endinterface

Module:

.. code-block:: systemverilog

   module Transactor(
     input logic clock,
     input logic reset,
     output logic [31:0] data_out,
     output logic valid
   );

     // Instantiate interface
     Transactor_send_if send_if();
     
     // Connect module signals to interface
     assign data_out = send_if.data_out;
     assign valid = send_if.valid;

   endmodule

Usage
-----

The interface can be called from a testbench:

.. code-block:: systemverilog

   module tb;
     logic clock, reset;
     logic [31:0] data_out;
     logic valid;
     
     Transactor dut(
       .clock(clock),
       .reset(reset),
       .data_out(data_out),
       .valid(valid)
     );
     
     initial begin
       logic [31:0] result;
       
       reset = 1;
       #10ns reset = 0;
       
       // Call send through interface
       dut.send_if.send(42, result);
       $display("Result: %d", result);  // Should be 84
       
       $finish;
     end
   endmodule

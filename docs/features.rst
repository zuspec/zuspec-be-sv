########
Features
########

This page describes the key features of the Zuspec SystemVerilog backend and how they work.

Component Translation
=====================

Basic Components
----------------

Zuspec Components are translated to SystemVerilog modules with appropriate ports:

.. code-block:: python

   @zdc.dataclass
   class Adder(zdc.Component):
       a : zdc.bit32 = zdc.input()
       b : zdc.bit32 = zdc.input()
       sum : zdc.bit32 = zdc.output()

Generates:

.. code-block:: systemverilog

   module Adder(
     input logic [31:0] a,
     input logic [31:0] b,
     output logic [31:0] sum
   );
     // ...
   endmodule

Parameterization
================

Const Fields as Parameters
---------------------------

Const fields in components become module parameters:

.. code-block:: python

   @zdc.dataclass
   class ConfigurableAdder(zdc.Component):
       DATA_WIDTH : int = zdc.const(default=32)
       
       a : zdc.int = zdc.input(width=lambda s: s.DATA_WIDTH)
       b : zdc.int = zdc.input(width=lambda s: s.DATA_WIDTH)
       sum : zdc.int = zdc.output(width=lambda s: s.DATA_WIDTH)

Generates:

.. code-block:: systemverilog

   module ConfigurableAdder #(
     parameter int DATA_WIDTH = 32
   )(
     input logic [(DATA_WIDTH-1):0] a,
     input logic [(DATA_WIDTH-1):0] b,
     output logic [(DATA_WIDTH-1):0] sum
   );
     // ...
   endmodule

Width Expressions
-----------------

Lambda width expressions are converted to SystemVerilog parameter expressions:

.. code-block:: python

   # Zuspec
   data : zdc.int = zdc.input(width=lambda s: s.DATA_WIDTH // 8)

.. code-block:: systemverilog

   // SystemVerilog
   input logic [(DATA_WIDTH/8-1):0] data

Parameter Overrides
-------------------

Instance parameters can be overridden using kwargs:

.. code-block:: python

   @zdc.dataclass
   class Top(zdc.Component):
       DATA_WIDTH : int = zdc.const(default=32)
       
       adder : ConfigurableAdder = zdc.inst(
           kwargs=lambda s: dict(DATA_WIDTH=s.DATA_WIDTH + 4)
       )

Generates:

.. code-block:: systemverilog

   module Top #(
     parameter int DATA_WIDTH = 32
   )(
     // ...
   );
     
     ConfigurableAdder #(.DATA_WIDTH(DATA_WIDTH+4)) adder (
       // port connections
     );

   endmodule

Clocked Processes
=================

@sync Methods
-------------

Methods decorated with ``@zdc.sync`` are converted to always blocks:

.. code-block:: python

   @zdc.dataclass
   class Register(zdc.Component):
       clock : zdc.bit = zdc.input()
       reset : zdc.bit = zdc.input()
       d : zdc.bit32 = zdc.input()
       q : zdc.bit32 = zdc.output()
       
       @zdc.sync(clock=lambda s:s.clock, reset=lambda s:s.reset)
       def _register(self):
           if self.reset:
               self.q = 0
           else:
               self.q = self.d

Generates:

.. code-block:: systemverilog

   always @(posedge clock or posedge reset) begin
     if (reset) begin
       q <= 0;
     end else begin
       q <= self.d;
     end
   end

Match Statements
----------------

Python match/case statements are converted to SystemVerilog case statements:

.. code-block:: python

   @zdc.sync(clock=lambda s:s.clock)
   def _fsm(self):
       match self.state:
           case 0:
               self.output = 1
               self.state = 1
           case 1:
               self.output = 0
               self.state = 0

Generates:

.. code-block:: systemverilog

   always @(posedge clock) begin
     case (state)
       0: begin
         output <= 1;
         state <= 1;
       end
       1: begin
         output <= 0;
         state <= 0;
       end
     endcase
   end

Async Processes
===============

@process Methods
----------------

Methods decorated with ``@zdc.process`` become initial blocks for testbench stimulus:

.. code-block:: python

   @zdc.dataclass
   class Testbench(zdc.Component):
       clock : zdc.bit = zdc.output()
       data : zdc.bit32 = zdc.output()
       
       @zdc.process
       async def _stimulus(self):
           for i in range(10):
               self.data = i
               await self.posedge(self.clock)

Generates:

.. code-block:: systemverilog

   initial begin
     for (int i = 0; i < 10; i++) begin
       data = i;
       @(posedge clock);
     end
   end

Timing Controls
---------------

Await expressions are converted to SystemVerilog timing controls:

* ``await self.posedge(signal)`` → ``@(posedge signal);``
* ``await self.wait(Time.ns(10))`` → ``#10ns;``

Bundle Handling
===============

Automatic Flattening
--------------------

Interface bundles are automatically flattened into individual ports:

.. code-block:: python

   @zdc.dataclass
   class ValidReadyBundle(zdc.Struct):
       valid : zdc.bit = zdc.field(is_out=False)
       ready : zdc.bit = zdc.field(is_out=True)
       data : zdc.bit32 = zdc.field(is_out=False)

   @zdc.dataclass
   class Consumer(zdc.Component):
       io : ValidReadyBundle = zdc.field()

Generates:

.. code-block:: systemverilog

   module Consumer(
     input logic io_valid,
     output logic io_ready,
     input logic [31:0] io_data
   );
     // ...
   endmodule

Bundle Connections
------------------

Bundle-to-bundle connections are expanded into individual signal connections:

.. code-block:: python

   @zdc.dataclass
   class System(zdc.Component):
       producer : Producer = zdc.inst()
       consumer : Consumer = zdc.inst()
       
       def __bind__(self):
           return {
               self.producer.io : self.consumer.io
           }

Generates:

.. code-block:: systemverilog

   module System(
     // ...
   );
     
     Producer producer(
       .io_valid(consumer_io_valid),
       .io_ready(consumer_io_ready),
       .io_data(consumer_io_data)
     );
     
     Consumer consumer(
       .io_valid(consumer_io_valid),
       .io_ready(consumer_io_ready),
       .io_data(consumer_io_data)
     );

   endmodule

Export Interfaces
=================

Interface Generation
--------------------

Export fields are converted to SystemVerilog interfaces with tasks:

.. code-block:: python

   from typing import Protocol

   class DataIF(Protocol):
       async def send(self, value: int) -> int: ...

   @zdc.dataclass
   class Transactor(zdc.Component):
       data_if : DataIF = zdc.export()
       data_out : zdc.bit32 = zdc.output()
       
       def __bind__(self):
           return {
               self.data_if.send : self._send
           }
       
       async def _send(self, value: int) -> int:
           self.data_out = value
           await self.posedge(self.clock)
           return value + 1

Generates interface:

.. code-block:: systemverilog

   interface Transactor_data_if;
     
     logic [31:0] data_out = 0;
     
     task send(
       input logic [31:0] value,
       output logic [31:0] __ret);
       
       data_out = value;
       @(posedge clock);
       __ret = value + 1;
       
     endtask

   endinterface

And module with interface instance:

.. code-block:: systemverilog

   module Transactor(
     // ports
   );
     
     // Instantiate interface
     Transactor_data_if data_if();
     
     // Connect module signals to interface
     assign data_out = data_if.data_out;

   endmodule

Instance Hierarchy
==================

Component Instantiation
-----------------------

Component fields with ``zdc.inst()`` are instantiated as submodules:

.. code-block:: python

   @zdc.dataclass
   class System(zdc.Component):
       clock : zdc.bit = zdc.input()
       
       counter1 : Counter = zdc.inst()
       counter2 : Counter = zdc.inst()
       
       def __bind__(self):
           return {
               self.counter1.clock : self.clock,
               self.counter2.clock : self.clock
           }

Generates:

.. code-block:: systemverilog

   module System(
     input logic clock
   );
     
     Counter counter1 (
       .clock(clock)
     );
     
     Counter counter2 (
       .clock(clock)
     );

   endmodule

External Components
-------------------

External components (``zdc.extern()``) are also instantiated:

.. code-block:: python

   @zdc.dataclass
   class RAM(zdc.Extern):
       addr : zdc.bit32 = zdc.input()
       data : zdc.bit32 = zdc.output()

   @zdc.dataclass
   class System(zdc.Component):
       ram : RAM = zdc.inst()

Generates instantiation with the external module name.

Debug Features
==============

Source Location Annotations
---------------------------

Enable ``debug_annotations`` to include source file references:

.. code-block:: python

   generator = SVGenerator(
       output_dir=Path("output"),
       debug_annotations=True
   )

Generates comments like:

.. code-block:: systemverilog

   // Generated from: test_smoke.py:15
   module Counter(
     // ...
   );
     
     // Source: test_smoke.py:20
     always @(posedge clock or posedge reset) begin
       // ...
     end

   endmodule

Name Sanitization
-----------------

Python names are sanitized to valid SystemVerilog identifiers:

* Dots (.) → Double underscores (__)
* Angle brackets (<, >) → Double underscores
* Invalid characters → Underscores
* Leading digits → Prefixed with underscore

Example: ``test_smoke.<locals>.Counter`` → ``test_smoke__locals__Counter``

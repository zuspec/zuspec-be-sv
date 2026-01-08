##########
Quickstart
##########

This guide will get you up and running with the Zuspec SystemVerilog backend in 5 minutes.

Installation
============

Install from PyPI:

.. code-block:: bash

   pip install zuspec-be-sv zuspec-dataclasses

Or for development:

.. code-block:: bash

   git clone https://github.com/zuspec/zuspec-be-sv
   cd zuspec-be-sv
   pip install -e ".[dev]"

Basic Example
=============

Let's create a simple counter and generate SystemVerilog:

.. code-block:: python

   import zuspec.dataclasses as zdc
   from zuspec.be.sv import SVGenerator
   from pathlib import Path

   @zdc.dataclass
   class Counter(zdc.Component):
       """Simple up-counter with reset."""
       clock : zdc.bit = zdc.input()
       reset : zdc.bit = zdc.input()
       count : zdc.bit32 = zdc.output()

       @zdc.sync(clock=lambda s:s.clock, reset=lambda s:s.reset)
       def _count(self):
           """Increment counter each clock cycle."""
           if self.reset:
               self.count = 0
           else:
               self.count += 1

   # Build the intermediate representation
   factory = zdc.DataModelFactory()
   ctxt = factory.build(Counter)
   
   # Generate SystemVerilog
   output_dir = Path("sv_output")
   generator = SVGenerator(output_dir)
   sv_files = generator.generate(ctxt)
   
   print(f"Generated: {sv_files[0]}")

Generated SystemVerilog
-----------------------

The above code generates a SystemVerilog module like:

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

Key Concepts
============

SVGenerator
-----------

The :class:`~zuspec.be.sv.SVGenerator` class is the main entry point for code generation.

Constructor parameters:

* ``output_dir``: Path where SystemVerilog files will be written
* ``debug_annotations``: Enable source location comments (default: False)

.. code-block:: python

   generator = SVGenerator(
       output_dir=Path("output"),
       debug_annotations=True  # Add source file comments
   )

Clocked Processes
-----------------

Methods decorated with ``@zdc.sync`` become ``always`` blocks:

.. code-block:: python

   @zdc.sync(clock=lambda s:s.clock, reset=lambda s:s.reset)
   def _behavior(self):
       if self.reset:
           self.state = 0
       else:
           self.state += 1

Generates:

.. code-block:: systemverilog

   always @(posedge clock or posedge reset) begin
     if (reset) begin
       state <= 0;
     end else begin
       state <= state + 1;
     end
   end

Async Processes
---------------

Methods decorated with ``@zdc.process`` become ``initial`` blocks with timing control:

.. code-block:: python

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

Next Steps
==========

* Learn about :doc:`features` - parameterization, bundles, interfaces
* Browse :doc:`examples` - complete component examples
* Read the :doc:`api` - full API reference

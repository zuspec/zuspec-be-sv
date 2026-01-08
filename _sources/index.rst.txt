.. Zuspec SystemVerilog Backend documentation master file

Zuspec SystemVerilog Backend
=============================

The Zuspec SystemVerilog (SV) Backend is a code generator that transforms 
Zuspec hardware component models into synthesizable SystemVerilog RTL.

It provides a complete path from high-level Zuspec component descriptions 
(with clocked processes, interfaces, and bundles) to production-ready 
SystemVerilog modules.

Version: 0.0.1

**Quick Links:**

* :doc:`quickstart` - Get started in 5 minutes
* :doc:`api` - API Reference
* :doc:`examples` - Example transformations
* `GitHub Repository <https://github.com/zuspec/zuspec-be-sv>`_

.. toctree::
   :maxdepth: 2
   :caption: User Guide:

   quickstart
   generator
   features
   examples

.. toctree::
   :maxdepth: 2
   :caption: Reference:

   api
   
.. toctree::
   :maxdepth: 1
   :caption: Development:
   
   testing
   contributing

Key Features
------------

* **Component Translation**: Converts Zuspec Components to SystemVerilog modules
* **Clocked Processes**: Transforms ``@sync`` decorated methods to ``always`` blocks
* **Async Processes**: Converts ``@process`` methods to ``initial`` blocks with timing control
* **Parameterization**: Supports parameterized component widths using const fields
* **Bundle Flattening**: Automatically flattens interface bundles to port lists
* **Instance Hierarchy**: Generates module instantiations with port connections
* **Export Interfaces**: Creates SystemVerilog interfaces for export fields with bound tasks
* **Debug Annotations**: Optional source location comments in generated code

Getting Started
---------------

Install the package:

.. code-block:: bash

   pip install zuspec-be-sv

Basic usage:

.. code-block:: python

   import zuspec.dataclasses as zdc
   from zuspec.be.sv import SVGenerator
   from pathlib import Path

   @zdc.dataclass
   class Counter(zdc.Component):
       clock : zdc.bit = zdc.input()
       reset : zdc.bit = zdc.input()
       count : zdc.bit32 = zdc.output()

       @zdc.sync(clock=lambda s:s.clock, reset=lambda s:s.reset)
       def _count(self):
           if self.reset:
               self.count = 0
           else:
               self.count += 1

   # Generate SystemVerilog
   factory = zdc.DataModelFactory()
   ctxt = factory.build(Counter)
   
   generator = SVGenerator(Path("output"))
   sv_files = generator.generate(ctxt)

This generates a complete SystemVerilog module with clock, reset, and counter logic.

Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`

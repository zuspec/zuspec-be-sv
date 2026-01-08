#######
Testing
#######

The Zuspec SystemVerilog backend includes comprehensive tests to ensure correct code generation.

Test Structure
==============

Tests are organized into categories:

Unit Tests
----------

Located in ``tests/unit/``:

* ``test_smoke.py`` - Basic component generation
* ``test_xtor_smoke.py`` - Transactor components with interfaces
* ``test_parameterization.py`` - Parameterized components
* ``test_translation.py`` - Statement and expression translation
* ``test_sim.py`` - Generated code simulation
* ``test_param_simulation.py`` - Parameterized component simulation

Performance Tests
-----------------

Located in ``tests/performance/``:

* ``test_backend_comparison.py`` - Compare different backends
* ``test_scaling_analysis.py`` - Analyze generation performance
* ``test_sim_perf.py`` - Simulation performance tests
* ``test_verify_work.py`` - Verify correctness at scale

Running Tests
=============

All Tests
---------

Run the complete test suite:

.. code-block:: bash

   pytest

Unit Tests Only
---------------

Run only unit tests:

.. code-block:: bash

   pytest tests/unit/

Specific Test
-------------

Run a specific test file:

.. code-block:: bash

   pytest tests/unit/test_smoke.py

Run a specific test function:

.. code-block:: bash

   pytest tests/unit/test_smoke.py::test_smoke

With Coverage
-------------

Generate coverage report:

.. code-block:: bash

   pytest --cov=zuspec.be.sv --cov-report=html

Skip Slow Tests
---------------

Skip performance tests:

.. code-block:: bash

   pytest -m "not slow"

Test Categories
===============

Smoke Tests
-----------

Basic sanity checks that generation works:

.. code-block:: python

   def test_smoke(tmpdir):
       """Test basic SystemVerilog generation."""
       
       @zdc.dataclass
       class Counter(zdc.Component):
           clock : zdc.bit = zdc.input()
           count : zdc.bit32 = zdc.output()
       
       factory = zdc.DataModelFactory()
       ctxt = factory.build(Counter)
       
       generator = SVGenerator(Path(tmpdir))
       sv_files = generator.generate(ctxt)
       
       assert len(sv_files) > 0
       assert sv_files[0].exists()

Parameterization Tests
----------------------

Test parameterized component generation:

.. code-block:: python

   def test_parameterized_width(tmpdir):
       """Test width parameterization."""
       
       @zdc.dataclass
       class ParamComp(zdc.Component):
           WIDTH : int = zdc.const(default=32)
           data : zdc.int = zdc.input(width=lambda s: s.WIDTH)
       
       factory = zdc.DataModelFactory()
       ctxt = factory.build(ParamComp)
       
       generator = SVGenerator(Path(tmpdir))
       sv_files = generator.generate(ctxt)
       
       sv_content = sv_files[0].read_text()
       assert "parameter int WIDTH = 32" in sv_content
       assert "input logic [(WIDTH-1):0] data" in sv_content

Simulation Tests
----------------

Tests that simulate generated code with a HDL simulator:

.. code-block:: python

   @pytest.mark.parametrize("sim", ["vlt", "mti"])
   def test_counter_sim(tmpdir, sim):
       """Test counter in simulation."""
       
       # Generate SystemVerilog
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
       
       factory = zdc.DataModelFactory()
       ctxt = factory.build(Counter)
       
       generator = SVGenerator(Path(tmpdir))
       sv_files = generator.generate(ctxt)
       
       # Build and run simulation
       # ... simulation setup code ...
       
       # Verify results
       assert simulation_passed

Translation Tests
-----------------

Test specific statement and expression translations:

.. code-block:: python

   def test_match_statement(tmpdir):
       """Test match/case translation."""
       
       @zdc.dataclass
       class FSM(zdc.Component):
           state : zdc.bit8 = zdc.field()
           output : zdc.bit = zdc.output()
           
           @zdc.sync(clock=lambda s:s.clock)
           def _fsm(self):
               match self.state:
                   case 0:
                       self.output = 1
                   case 1:
                       self.output = 0
       
       factory = zdc.DataModelFactory()
       ctxt = factory.build(FSM)
       
       generator = SVGenerator(Path(tmpdir))
       sv_files = generator.generate(ctxt)
       
       sv_content = sv_files[0].read_text()
       assert "case (state)" in sv_content
       assert "0: begin" in sv_content
       assert "1: begin" in sv_content

Writing Tests
=============

Test Template
-------------

Use this template for new tests:

.. code-block:: python

   import pytest
   import zuspec.dataclasses as zdc
   from zuspec.be.sv import SVGenerator
   from pathlib import Path

   def test_my_feature(tmpdir):
       """Test description."""
       
       # Define component
       @zdc.dataclass
       class MyComponent(zdc.Component):
           # ... component definition
           pass
       
       # Build IR
       factory = zdc.DataModelFactory()
       ctxt = factory.build(MyComponent)
       
       # Generate SV
       output_dir = Path(tmpdir)
       generator = SVGenerator(output_dir)
       sv_files = generator.generate(ctxt)
       
       # Verify generation
       assert len(sv_files) > 0
       assert sv_files[0].exists()
       
       # Check generated content
       sv_content = sv_files[0].read_text()
       assert "expected_pattern" in sv_content

Simulation Test Template
-------------------------

For tests that simulate generated code:

.. code-block:: python

   import pytest
   import shutil
   from dv_flow.mgr import TaskListenerLog, TaskSetRunner

   @pytest.mark.parametrize("sim", get_available_sims())
   def test_my_sim(tmpdir, sim):
       """Test with simulation."""
       
       # Generate SystemVerilog
       # ... generation code ...
       
       # Create testbench
       tb_file = Path(tmpdir) / "tb.sv"
       tb_file.write_text("""
       module tb;
           // testbench code
       endmodule
       """)
       
       # Setup simulation
       from dv_flow.mgr.task_graph_builder import TaskGraphBuilder
       builder = TaskGraphBuilder()
       # ... build simulation tasks ...
       
       # Run simulation
       listener = TaskListenerLog()
       runner = TaskSetRunner([listener])
       result = runner.run(task_set)
       
       # Verify results
       assert result == 0

Test Markers
============

The test suite uses pytest markers to categorize tests:

* ``@pytest.mark.unit`` - Unit tests (fast, isolated)
* ``@pytest.mark.performance`` - Performance tests
* ``@pytest.mark.slow`` - Tests that take >5 seconds
* ``@pytest.mark.integration`` - Integration tests

Example:

.. code-block:: python

   @pytest.mark.slow
   @pytest.mark.performance
   def test_large_hierarchy(tmpdir):
       """Test large component hierarchy."""
       # ... test code ...

Run specific markers:

.. code-block:: bash

   pytest -m unit           # Run only unit tests
   pytest -m "not slow"     # Skip slow tests
   pytest -m performance    # Run only performance tests

Continuous Integration
======================

The test suite runs automatically on:

* Pull requests
* Push to main branch
* Manual workflow dispatch

CI Configuration
----------------

See ``.github/workflows/ci.yml`` for CI configuration.

Test Requirements
=================

Dependencies
------------

Test dependencies are listed in ``pyproject.toml``:

.. code-block:: toml

   [project.optional-dependencies]
   dev = [
       "pytest>=6.0",
       "pytest-dfm",
       "zuspec-dataclasses",
       "zuspec-be-sw",
   ]

Install test dependencies:

.. code-block:: bash

   pip install -e ".[dev]"

Simulators
----------

Some tests require HDL simulators:

* **Verilator** - Open source Verilog simulator
* **ModelSim/QuestaSim** - Commercial simulator
* **Vivado Simulator (xsim)** - Xilinx simulator

Tests automatically detect available simulators and skip if not found.

Debugging Failed Tests
======================

Verbose Output
--------------

Run with verbose output:

.. code-block:: bash

   pytest -v

Show print statements:

.. code-block:: bash

   pytest -s

Keep test files:

.. code-block:: bash

   pytest --keep-tmpdir

Debug Mode
----------

Run with Python debugger:

.. code-block:: bash

   pytest --pdb

Run single test with full output:

.. code-block:: bash

   pytest tests/unit/test_smoke.py::test_smoke -vvs

Inspect Generated Files
------------------------

Tests use ``tmpdir`` fixture for temporary files. To inspect:

.. code-block:: python

   def test_example(tmpdir):
       output_dir = Path(tmpdir)
       generator = SVGenerator(output_dir)
       sv_files = generator.generate(ctxt)
       
       # Print for debugging
       print(f"\\n=== Generated File: {sv_files[0]} ===")
       print(sv_files[0].read_text())
       print("=== End ===\\n")

Then run with ``-s`` flag to see output.

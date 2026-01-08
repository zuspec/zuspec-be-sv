##################
Generator Details
##################

The :class:`~zuspec.be.sv.SVGenerator` class is the core component that transforms 
Zuspec IR (Intermediate Representation) into SystemVerilog RTL.

Overview
========

The generator performs these key transformations:

1. **Component → Module**: Zuspec Components become SystemVerilog modules
2. **Fields → Ports/Signals**: Input/output fields become ports, internal fields become signals
3. **@sync → always**: Clocked processes become always blocks
4. **@process → initial**: Async processes become initial blocks
5. **Bindings → Connections**: Component bindings become wire connections or port maps
6. **Exports → Interfaces**: Export fields become SystemVerilog interfaces with tasks

Constructor
===========

.. code-block:: python

   SVGenerator(output_dir: Path, debug_annotations: bool = False)

Parameters:

* ``output_dir``: Directory where generated ``.sv`` files will be written
* ``debug_annotations``: If True, includes source location comments in generated code

Example:

.. code-block:: python

   from pathlib import Path
   from zuspec.be.sv import SVGenerator

   generator = SVGenerator(
       output_dir=Path("rtl_output"),
       debug_annotations=True
   )

Main API
========

generate()
----------

.. code-block:: python

   def generate(self, ctxt: ir.Context) -> List[Path]:
       """Generate SystemVerilog code for all components in context.
       
       Args:
           ctxt: IR Context containing components to generate
           
       Returns:
           List of paths to generated .sv files
       """

This is the main entry point. It:

1. Iterates through all components in the context
2. Generates a ``.sv`` file for each component
3. Returns list of generated file paths

Example:

.. code-block:: python

   import zuspec.dataclasses as zdc
   from zuspec.be.sv import SVGenerator
   from pathlib import Path

   @zdc.dataclass
   class MyComponent(zdc.Component):
       clock : zdc.bit = zdc.input()
       # ... component definition

   factory = zdc.DataModelFactory()
   ctxt = factory.build(MyComponent)
   
   generator = SVGenerator(Path("output"))
   files = generator.generate(ctxt)
   
   for f in files:
       print(f"Generated: {f}")

Internal Methods
================

The generator uses several internal methods for different aspects of code generation:

Component Generation
--------------------

* ``_generate_component()``: Main component-to-module transformation
* ``_generate_component_instances()``: Creates module instantiations
* ``_generate_extern_instances()``: Instantiates external modules

Process Generation
------------------

* ``_generate_sync_process()``: Converts @sync methods to always blocks
* ``_generate_async_process()``: Converts @process methods to initial blocks
* ``_generate_stmt()``: Generates individual statements (if, assign, match)
* ``_generate_expr()``: Generates expressions (field refs, operators, constants)

Interface Generation
--------------------

* ``_generate_export_interfaces()``: Creates SystemVerilog interfaces for exports
* ``_generate_interface_task()``: Converts bound methods to interface tasks
* ``_generate_task_body()``: Generates task body with timing controls

Type Conversion
---------------

* ``_get_sv_type()``: Converts IR types to SystemVerilog types
* ``_get_sv_parameterized_type()``: Generates parameterized types
* ``_eval_width_lambda_to_sv()``: Evaluates width expressions for parameters

Name Handling
-------------

* ``_sanitize_sv_name()``: Converts Python names to valid SystemVerilog identifiers
  
  - Replaces dots with double underscores
  - Handles angle brackets and special characters
  - Example: ``test_smoke.<locals>.Counter`` → ``test_smoke__locals__Counter``

Bundle Handling
---------------

* ``_resolve_bundle_type()``: Resolves bundle field references to struct types
* ``_get_flattened_bundle_fields()``: Flattens bundle into individual signals
* ``_infer_bundle_signal_type()``: Determines signal types for bundle fields

Operator Conversion
-------------------

* ``_get_sv_binop()``: Binary operators (+, -, \*, /, <<, >>, \|, ^, &)
* ``_get_sv_cmpop()``: Comparison operators (==, !=, <, <=, >, >=)
* ``_get_sv_boolop()``: Boolean operators (&&, \|\|)
* ``_get_sv_unaryop()``: Unary operators (!, ~, +, -)

Generation Patterns
===================

Module Structure
----------------

Generated modules follow this pattern:

.. code-block:: systemverilog

   module ModuleName #(
     parameter int PARAM1 = default_val
   )(
     input logic port1,
     output logic [WIDTH-1:0] port2
   );

     // Internal signal declarations
     logic internal_sig;
     
     // Component instantiations
     SubModule inst(...);
     
     // Always blocks (from @sync)
     always @(posedge clk) begin
       // ...
     end
     
     // Initial blocks (from @process)
     initial begin
       // ...
     end
     
     // Interface instantiations (from exports)
     ModuleName_if my_if();
     assign my_if.signal = internal_signal;

   endmodule

Interface Structure
-------------------

Generated interfaces for export fields:

.. code-block:: systemverilog

   interface ModuleName_export_name;
     
     // Local signals
     logic signal1;
     logic [31:0] signal2 = 0;
     
     // Tasks for bound methods
     task method_name(
       input logic [31:0] arg1,
       output logic [31:0] __ret);
       
       // Task body with timing controls
       @(posedge clock);
       __ret = signal1 + arg1;
       
     endtask

   endinterface

Port Connection Patterns
-------------------------

The generator handles several connection patterns:

**Direct Port Connection**:

.. code-block:: python

   # Zuspec
   self.bind(self.sub.port, self.my_port)

.. code-block:: systemverilog

   // SystemVerilog
   SubModule sub(
     .port(my_port)
   );

**Bundle Flattening**:

.. code-block:: python

   # Zuspec
   self.bind(self.sub.io, self.my_io)  # io is a bundle

.. code-block:: systemverilog

   // SystemVerilog - flattened
   SubModule sub(
     .io_ready(my_io_ready),
     .io_valid(my_io_valid),
     .io_data(my_io_data)
   );

**Internal Signal Connection**:

.. code-block:: python

   # Zuspec
   self.bind(self.sub1.out, self.sub2.in)

.. code-block:: systemverilog

   // SystemVerilog
   logic sub1_out;  // Internal wire
   
   SubModule1 sub1(.out(sub1_out));
   SubModule2 sub2(.in(sub1_out));

Limitations and Notes
=====================

Current Limitations
-------------------

1. **State Machines**: Match/case statements are converted, but FSM optimization is not performed
2. **Type Inference**: Some complex expressions may need explicit type hints
3. **Bundle Recursion**: Nested bundles are not fully supported
4. **Extern Types**: Limited introspection of external component ports

Best Practices
--------------

1. **Use Type Annotations**: Explicit types help with correct width inference
2. **Keep Bundles Flat**: Avoid deeply nested bundle structures
3. **Name Signals Clearly**: Signal names influence type inference
4. **Test Generated Code**: Always verify generated SystemVerilog with simulation
5. **Enable Debug Mode**: Use ``debug_annotations=True`` during development

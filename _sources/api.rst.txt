##############
API Reference
##############

This page provides the complete API reference for the Zuspec SystemVerilog backend.

SVGenerator
===========

.. class:: SVGenerator(output_dir: Path, debug_annotations: bool = False)

   Main SystemVerilog code generator from datamodel.
   
   :param output_dir: Directory where generated .sv files will be written
   :type output_dir: pathlib.Path
   :param debug_annotations: If True, includes source location comments
   :type debug_annotations: bool
   :param default: False
   
   **Attributes:**
   
   .. attribute:: output_dir
      
      Output directory for generated files (Path).
   
   .. attribute:: debug_annotations
      
      Whether to include debug source annotations (bool).
   
   **Methods:**
   
   .. method:: generate(ctxt: ir.Context) -> List[Path]
      
      Generate SystemVerilog code for all components in context.
      
      :param ctxt: IR Context containing components to generate
      :type ctxt: zuspec.dataclasses.ir.Context
      :return: List of paths to generated .sv files
      :rtype: List[pathlib.Path]
      
      Iterates through all DataTypeComponent types in the context and generates
      a .sv file for each one. Files are named after the sanitized component name.

Internal IR Types
=================

These types from ``zuspec.dataclasses.ir`` are used by the generator:

Context
-------

.. class:: ir.Context

   Container for all types and components in a model.
   
   **Attributes:**
   
   * ``type_m: Dict[str, DataType]`` - Map of type names to type definitions
   * ``data: Dict[str, Any]`` - Additional context data

DataTypeComponent
-----------------

.. class:: ir.DataTypeComponent

   Represents a Zuspec Component.
   
   **Attributes:**
   
   * ``name: str`` - Component name
   * ``fields: List[Field]`` - Component fields (ports, instances, internal signals)
   * ``functions: List[Function]`` - Component methods
   * ``sync_processes: List[Function]`` - Methods decorated with @sync
   * ``bind_map: List[Binding]`` - Port/signal bindings
   * ``py_type: type`` - Original Python class
   * ``loc: Location`` - Source location

Field Types
-----------

.. class:: ir.Field

   Base class for component fields.
   
   **Attributes:**
   
   * ``name: str`` - Field name
   * ``datatype: DataType`` - Field data type
   * ``is_const: bool`` - Whether field is a const (parameter)
   * ``kind: FieldKind`` - Field kind (normal, export, etc.)

.. class:: ir.FieldInOut

   Field with direction (input/output port).
   
   Inherits from Field.
   
   **Attributes:**
   
   * ``is_out: bool`` - True for output, False for input
   * ``width_expr: Expr`` - Optional width expression (for parameterization)

DataType Types
--------------

.. class:: ir.DataType

   Base data type.

.. class:: ir.DataTypeInt

   Integer type with bit width.
   
   **Attributes:**
   
   * ``bits: int`` - Bit width (-1 means inferred)

.. class:: ir.DataTypeRef

   Reference to another type (component, struct, extern).
   
   **Attributes:**
   
   * ``ref_name: str`` - Name of referenced type

.. class:: ir.DataTypeStruct

   Struct/bundle type.
   
   **Attributes:**
   
   * ``fields: List[Field]`` - Struct fields

.. class:: ir.DataTypeExtern

   External component type.
   
   **Attributes:**
   
   * ``extern_name: str`` - External module name
   * ``py_type: type`` - Python class

Statement Types
---------------

.. class:: ir.Stmt

   Base statement class.

.. class:: ir.StmtAssign

   Assignment statement.
   
   **Attributes:**
   
   * ``targets: List[Expr]`` - Assignment targets (LHS)
   * ``value: Expr`` - Assignment value (RHS)

.. class:: ir.StmtIf

   If/else statement.
   
   **Attributes:**
   
   * ``test: Expr`` - Condition expression
   * ``body: List[Stmt]`` - If body statements
   * ``orelse: List[Stmt]`` - Else body statements

.. class:: ir.StmtMatch

   Match/case statement.
   
   **Attributes:**
   
   * ``subject: Expr`` - Match subject expression
   * ``cases: List[Case]`` - Case clauses

.. class:: ir.StmtWhile

   While loop statement.
   
   **Attributes:**
   
   * ``test: Expr`` - Loop condition
   * ``body: List[Stmt]`` - Loop body

.. class:: ir.StmtFor

   For loop statement.
   
   **Attributes:**
   
   * ``target: Expr`` - Loop variable
   * ``iter: Expr`` - Iteration expression
   * ``body: List[Stmt]`` - Loop body

.. class:: ir.StmtReturn

   Return statement.
   
   **Attributes:**
   
   * ``value: Expr`` - Return value expression

Expression Types
----------------

.. class:: ir.Expr

   Base expression class.

.. class:: ir.ExprConstant

   Constant value.
   
   **Attributes:**
   
   * ``value: Any`` - Constant value

.. class:: ir.ExprRefField

   Field reference (self.field or self.instance.port).
   
   **Attributes:**
   
   * ``base: Expr`` - Base expression
   * ``index: int`` - Field index in component

.. class:: ir.ExprRefPy

   Python attribute reference.
   
   **Attributes:**
   
   * ``base: Expr`` - Base expression
   * ``ref: str`` - Attribute name

.. class:: ir.ExprAttribute

   Attribute access (bundle.field).
   
   **Attributes:**
   
   * ``value: Expr`` - Object expression
   * ``attr: str`` - Attribute name

.. class:: ir.ExprBin

   Binary operation.
   
   **Attributes:**
   
   * ``lhs: Expr`` - Left operand
   * ``rhs: Expr`` - Right operand
   * ``op: BinOp`` - Binary operator

.. class:: ir.ExprCompare

   Comparison expression.
   
   **Attributes:**
   
   * ``left: Expr`` - Left operand
   * ``ops: List[CmpOp]`` - Comparison operators
   * ``comparators: List[Expr]`` - Right operands

.. class:: ir.ExprBool

   Boolean operation (and, or).
   
   **Attributes:**
   
   * ``op: BoolOp`` - Boolean operator
   * ``values: List[Expr]`` - Operands

.. class:: ir.ExprUnary

   Unary operation (not, ~, -, +).
   
   **Attributes:**
   
   * ``op: UnaryOp`` - Unary operator
   * ``operand: Expr`` - Operand expression

.. class:: ir.ExprAwait

   Await expression (for async processes).
   
   **Attributes:**
   
   * ``value: Expr`` - Awaited expression

Operator Enums
--------------

.. class:: ir.BinOp

   Binary operators:
   
   * ``Add`` - Addition (+)
   * ``Sub`` - Subtraction (-)
   * ``Mult`` - Multiplication (*)
   * ``Div`` - Division (/)
   * ``Mod`` - Modulo (%)
   * ``LShift`` - Left shift (<<)
   * ``RShift`` - Right shift (>>)
   * ``BitOr`` - Bitwise OR (|)
   * ``BitXor`` - Bitwise XOR (^)
   * ``BitAnd`` - Bitwise AND (&)

.. class:: ir.CmpOp

   Comparison operators:
   
   * ``Eq`` - Equal (==)
   * ``NotEq`` - Not equal (!=)
   * ``Lt`` - Less than (<)
   * ``LtE`` - Less than or equal (<=)
   * ``Gt`` - Greater than (>)
   * ``GtE`` - Greater than or equal (>=)

.. class:: ir.BoolOp

   Boolean operators:
   
   * ``And`` - Logical AND (&&)
   * ``Or`` - Logical OR (||)

.. class:: ir.UnaryOp

   Unary operators:
   
   * ``Not`` - Logical NOT (!)
   * ``Invert`` - Bitwise NOT (~)
   * ``UAdd`` - Unary plus (+)
   * ``USub`` - Unary minus (-)

Example Usage
=============

Basic Generation
----------------

.. code-block:: python

   from pathlib import Path
   import zuspec.dataclasses as zdc
   from zuspec.be.sv import SVGenerator

   # Define component
   @zdc.dataclass
   class MyComp(zdc.Component):
       clock : zdc.bit = zdc.input()
       data : zdc.bit32 = zdc.output()

   # Build IR
   factory = zdc.DataModelFactory()
   ctxt = factory.build(MyComp)

   # Generate SV
   gen = SVGenerator(Path("output"))
   files = gen.generate(ctxt)

With Debug Annotations
----------------------

.. code-block:: python

   gen = SVGenerator(
       output_dir=Path("output"),
       debug_annotations=True
   )
   files = gen.generate(ctxt)

Multiple Components
-------------------

.. code-block:: python

   # Build context with multiple components
   @zdc.dataclass
   class CompA(zdc.Component):
       # ...

   @zdc.dataclass
   class CompB(zdc.Component):
       # ...

   factory = zdc.DataModelFactory()
   ctxt = factory.build(CompA)  # Build from top component
   
   # Generate all components
   gen = SVGenerator(Path("output"))
   files = gen.generate(ctxt)  # Generates .sv for CompA and CompB

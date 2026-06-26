"""SV IR — SystemVerilog-specific IR nodes beyond RTL.

These nodes represent the full breadth of SystemVerilog constructs:
packages, typedefs, classes, interfaces, constraints, tasks, functions,
etc.  They are distinct from the RTL IR (``RTLModule``, ``RTLWire``, …) which covers
only synthesisable RTL modules.

Lowering passes in ``zuspec-be-sv`` translate semantic IR (from
``zuspec-dataclasses``) and synthesis IR (from ``zuspec-synth``) into
these nodes; ``SVEmitter`` serialises them to SV text.
"""
from __future__ import annotations

import dataclasses as dc
from typing import Any, List, Optional, Tuple


@dc.dataclass
class SVField:
    """One field inside a struct, union, or class.

    Args:
        name: Field name.
        width: Bit-width (used when ``dtype`` is ``None``).
        dtype: Optional explicit type string, e.g. ``"logic [31:0]"`` or
            ``"my_typedef_t"``.  When set, ``width`` is ignored for
            emission purposes.
        is_rand: When ``True`` the field is declared ``rand``.
        is_randc: When ``True`` the field is declared ``randc``
            (takes precedence over ``is_rand``).

    Note:
        ``is_rand`` and ``is_randc`` are only meaningful inside classes
        (not packed structs).  The emitter ignores them in struct
        contexts.
    """

    name: str = dc.field()
    width: int = dc.field(default=1)
    dtype: Optional[str] = dc.field(default=None)
    is_rand: bool = dc.field(default=False)
    is_randc: bool = dc.field(default=False)


@dc.dataclass
class SVClassField:
    """A field inside a SystemVerilog class.

    Unlike ``SVField`` (used in packed structs/unions), ``SVClassField``
    supports ``rand``/``randc`` qualifiers and an optional initial value.

    Args:
        name: Field name.
        dtype: Type string, e.g. ``"int unsigned"``, ``"bit [31:0]"``,
            ``"MyClass"``.
        is_rand: Emit ``rand`` qualifier.
        is_randc: Emit ``randc`` qualifier (takes precedence over ``is_rand``).
        initial_value: Optional initial value expression string.
    """

    name: str = dc.field()
    dtype: str = dc.field(default="logic")
    is_rand: bool = dc.field(default=False)
    is_randc: bool = dc.field(default=False)
    is_static: bool = dc.field(default=False)
    initial_value: Optional[str] = dc.field(default=None)


@dc.dataclass
class SVArg:
    """A task/function argument.

    Args:
        name: Argument name.
        dtype: Type string.
        direction: One of ``"input"``, ``"output"``, ``"inout"``, ``"ref"``.
    """

    name: str = dc.field()
    dtype: str = dc.field(default="logic")
    direction: str = dc.field(default="input")


@dc.dataclass
class SVConstraintBlock:
    """A ``constraint name { ... }`` block inside a class.

    Constraint expressions are stored as pre-rendered strings so that
    be-sv remains PSS-agnostic.

    Args:
        name: Constraint block name.
        exprs: List of SV constraint expression strings (one per line).
    """

    name: str = dc.field()
    exprs: List[str] = dc.field(default_factory=list)


@dc.dataclass
class SVTaskDecl:
    """A ``task`` declaration inside a class or at package scope.

    Args:
        name: Task name.
        args: Argument list.
        body_lines: SV statement lines forming the task body.
        is_virtual: Emit ``virtual`` qualifier.
        is_pure: Emit ``pure virtual`` (implies ``is_virtual``; body is omitted).
    """

    name: str = dc.field()
    args: List[SVArg] = dc.field(default_factory=list)
    body_lines: List[str] = dc.field(default_factory=list)
    is_virtual: bool = dc.field(default=False)
    is_pure: bool = dc.field(default=False)
    # Structured body (SVStmt list). When set, takes precedence over body_lines
    # and is rendered via SVStmtEmitter (plan task C3).
    body: Optional[List[Any]] = dc.field(default=None)


@dc.dataclass
class SVFunctionDecl:
    """A ``function`` declaration inside a class or at package scope.

    Args:
        name: Function name.
        args: Argument list.
        return_type: Return type string (``"void"`` if none).
        body_lines: SV statement lines forming the function body.
        is_virtual: Emit ``virtual`` qualifier.
        is_pure: Emit ``pure virtual`` (implies ``is_virtual``; body is omitted).
    """

    name: str = dc.field()
    args: List[SVArg] = dc.field(default_factory=list)
    return_type: str = dc.field(default="void")
    body_lines: List[str] = dc.field(default_factory=list)
    is_virtual: bool = dc.field(default=False)
    is_pure: bool = dc.field(default=False)
    is_static: bool = dc.field(default=False)
    # Structured body (SVStmt list). When set, takes precedence over body_lines
    # and is rendered via SVStmtEmitter (plan task C3).
    body: Optional[List[Any]] = dc.field(default=None)


@dc.dataclass
class SVImportDPI:
    """An ``import "DPI-C"`` declaration.

    Args:
        language: DPI language string (typically ``"DPI-C"``).
        func_or_task: ``"function"`` or ``"task"``.
        return_type: Return type string (ignored for tasks).
        name: Imported function/task name.
        args: Argument list.
    """

    language: str = dc.field(default="DPI-C")
    func_or_task: str = dc.field(default="function")
    return_type: str = dc.field(default="void")
    name: str = dc.field(default="")
    args: List[SVArg] = dc.field(default_factory=list)


@dc.dataclass
class SVTypedefStruct:
    """A ``typedef struct [packed] { … } name;`` declaration.

    Args:
        name: Typedef name (the alias created by the typedef).
        fields: Ordered list of struct fields (MSB-first by convention).
        packed: When ``True`` (default) emits ``struct packed``.
    """

    name: str = dc.field()
    fields: List[SVField] = dc.field(default_factory=list)
    packed: bool = dc.field(default=True)


@dc.dataclass
class SVTypedefEnum:
    """A ``typedef enum logic[…] { … } name;`` declaration.

    Args:
        name: Typedef name.
        members: List of ``(member_name, value)`` pairs in declaration order.
        width: Bit-width of the underlying ``logic`` type.  ``0`` means the
            emitter infers the minimum width from the member values.
    """

    name: str = dc.field()
    members: List[Tuple[str, int]] = dc.field(default_factory=list)
    width: int = dc.field(default=0)


@dc.dataclass
class SVTypedefUnion:
    """A ``typedef union [packed] { … } name;`` declaration.

    Args:
        name: Typedef name.
        fields: Ordered list of union fields.
        packed: When ``True`` (default) emits ``union packed``.
    """

    name: str = dc.field()
    fields: List[SVField] = dc.field(default_factory=list)
    packed: bool = dc.field(default=True)


@dc.dataclass
class SVModuleDecl:
    """A non-RTL ``module ... endmodule`` block (for testbench use).

    Args:
        name: Module name.
        body_lines: SV statement lines forming the module body.
    """

    name: str = dc.field()
    body_lines: List[str] = dc.field(default_factory=list)


@dc.dataclass
class SVLineDirective:
    """A ```line`` directive for source location tracking.

    Args:
        filename: Source file path.
        lineno: Line number in the source file.
    """

    filename: str = dc.field()
    lineno: int = dc.field(default=1)


@dc.dataclass
class SVForwardDecl:
    """A ``typedef class name;`` forward declaration.

    Args:
        class_name: Name of the class being forward-declared.
    """

    class_name: str = dc.field()


@dc.dataclass
class SVPackage:
    """A SystemVerilog ``package … endpackage`` block.

    Args:
        name: Package name.
        items: Ordered list of package-scope items (typedefs, parameters,
            functions, ``SVRawItem`` escapes, etc.).
    """

    name: str = dc.field()
    items: List[Any] = dc.field(default_factory=list)


@dc.dataclass
class SVRawItem:
    """Escape hatch: raw SV text lines for constructs not yet structured.

    Unlike ``RTLRawModule`` (which represents a complete named module),
    ``SVRawItem`` is for arbitrary non-module SV content: file-scope
    declarations, preprocessor directives, comments, etc.

    Args:
        lines: Raw SV text lines (no trailing newline per element).
    """

    lines: List[str] = dc.field(default_factory=list)


@dc.dataclass
class SVClass:
    """A SystemVerilog ``class ... endclass`` block.

    Args:
        name: Class name.
        extends_name: Optional base class name.
        is_virtual: Emit ``virtual class``.
        fields: Ordered list of class fields.
        constraints: Constraint blocks.
        tasks: Task declarations.
        functions: Function declarations.
        forward_decls: Forward declaration class names emitted before
            the class definition.
        items: Additional miscellaneous items (raw items, etc.).
    """

    name: str = dc.field()
    extends_name: Optional[str] = dc.field(default=None)
    is_virtual: bool = dc.field(default=False)
    implements: List[str] = dc.field(default_factory=list)
    fields: List[SVClassField] = dc.field(default_factory=list)
    constraints: List[SVConstraintBlock] = dc.field(default_factory=list)
    tasks: List[SVTaskDecl] = dc.field(default_factory=list)
    functions: List[SVFunctionDecl] = dc.field(default_factory=list)
    forward_decls: List[str] = dc.field(default_factory=list)
    items: List[Any] = dc.field(default_factory=list)


@dc.dataclass
class SVInterfaceClass:
    """A SystemVerilog ``interface class ... endclass`` block.

    Interface classes contain only pure-virtual method *prototypes* (no
    fields, no bodies) and may extend other interface classes.  Concrete
    classes realise them via ``SVClass.implements``.

    Args:
        name: Interface-class name.
        extends: Names of interface classes this one extends (``extends A, B``).
        tasks: Pure-virtual task prototypes (emitted as ``pure virtual task``).
        functions: Pure-virtual function prototypes.
        forward_decls: Forward declaration names emitted before the definition.
    """

    name: str = dc.field()
    extends: List[str] = dc.field(default_factory=list)
    tasks: List[SVTaskDecl] = dc.field(default_factory=list)
    functions: List[SVFunctionDecl] = dc.field(default_factory=list)
    forward_decls: List[str] = dc.field(default_factory=list)


@dc.dataclass
class SVInterface:
    """Stub for a SystemVerilog ``interface … endinterface`` block.

    Full implementation is deferred to a future phase.

    Args:
        name: Interface name.
        items: Placeholder for interface body items.
    """

    name: str = dc.field()
    items: List[Any] = dc.field(default_factory=list)

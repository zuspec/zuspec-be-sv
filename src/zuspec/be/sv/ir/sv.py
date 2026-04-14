"""SV IR — SystemVerilog-specific IR nodes beyond RTL.

These nodes represent the full breadth of SystemVerilog constructs:
packages, typedefs, classes, interfaces, constraints, etc.  They are
distinct from the RTL IR (``RTLModule``, ``RTLWire``, …) which covers
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
    """

    name: str = dc.field()
    width: int = dc.field(default=1)
    dtype: Optional[str] = dc.field(default=None)


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
    """Stub for a SystemVerilog ``class … endclass`` block.

    Full implementation is deferred to a future phase.  The stub allows
    the emitter and IR walkers to recognise the type without panicking.

    Args:
        name: Class name.
        items: Placeholder for class body items.
    """

    name: str = dc.field()
    items: List[Any] = dc.field(default_factory=list)


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

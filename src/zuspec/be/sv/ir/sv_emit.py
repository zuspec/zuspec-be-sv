"""SVEmitter — serialise any SystemVerilog IR node to SV text.

``SVEmitter`` is the single serialisation point for all SV output.  It
handles the full SV IR hierarchy:

* **New SV IR** (``SVPackage``, ``SVTypedefStruct``, ``SVTypedefEnum``,
  ``SVTypedefUnion``, ``SVRawItem``, ``SVClass``, ``SVInterface``) —
  serialised directly by this class.
* **RTL IR** (``RTLModule``, ``RTLRawModule``) — delegated to the embedded
  ``RTLEmitter`` instance so that all existing pipeline / plugin code
  continues to work unchanged.

Usage::

    emitter = SVEmitter()
    sv_text = emitter.emit_all(constructs)

where *constructs* is any ordered mix of SV IR and RTL IR nodes.
"""
from __future__ import annotations

import math
from typing import Any, List

from zuspec.be.sv.ir.rtl import RTLModule, RTLRawModule
from zuspec.be.sv.ir.rtl_emit import RTLEmitter
from zuspec.be.sv.ir.sv import (
    SVClass,
    SVField,
    SVInterface,
    SVPackage,
    SVRawItem,
    SVTypedefEnum,
    SVTypedefStruct,
    SVTypedefUnion,
)


class SVEmitter:
    """Serialise any SV IR or RTL IR node to SystemVerilog text.

    The emitter owns an ``RTLEmitter`` instance and delegates all
    ``RTLModule`` / ``RTLRawModule`` handling to it, so that RTL pipeline
    output is produced identically to the M7 behaviour.
    """

    def __init__(self) -> None:
        self._rtl = RTLEmitter()

    # ------------------------------------------------------------------
    # Field helpers (shared by struct and union)
    # ------------------------------------------------------------------

    def _emit_field(self, field: SVField, indent: str = "  ") -> str:
        """Emit one struct/union field declaration line."""
        if field.dtype:
            return f"{indent}{field.dtype} {field.name};"
        if field.width > 1:
            return f"{indent}logic [{field.width - 1}:0] {field.name};"
        return f"{indent}logic {field.name};"

    # ------------------------------------------------------------------
    # Typedef serialisation
    # ------------------------------------------------------------------

    def emit_typedef_struct(self, td: SVTypedefStruct) -> str:
        """Emit a ``typedef struct [packed] { … } name;``."""
        kw = "struct packed" if td.packed else "struct"
        lines: List[str] = [f"typedef {kw} {{"]
        for field in td.fields:
            lines.append(self._emit_field(field))
        lines.append(f"}} {td.name};")
        return "\n".join(lines)

    def emit_typedef_enum(self, td: SVTypedefEnum) -> str:
        """Emit a ``typedef enum logic[…] { … } name;``."""
        width = td.width
        if width == 0 and td.members:
            max_val = max(v for _, v in td.members)
            width = max(1, math.ceil(math.log2(max_val + 2)) if max_val >= 0 else 1)
        wspec = f"[{width - 1}:0]" if width > 1 else ""
        lines: List[str] = [f"typedef enum logic{wspec} {{"]
        for i, (name, val) in enumerate(td.members):
            comma = "" if i == len(td.members) - 1 else ","
            lines.append(f"  {name} = {width}'d{val}{comma}")
        lines.append(f"}} {td.name};")
        return "\n".join(lines)

    def emit_typedef_union(self, td: SVTypedefUnion) -> str:
        """Emit a ``typedef union [packed] { … } name;``."""
        kw = "union packed" if td.packed else "union"
        lines: List[str] = [f"typedef {kw} {{"]
        for field in td.fields:
            lines.append(self._emit_field(field))
        lines.append(f"}} {td.name};")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Package serialisation
    # ------------------------------------------------------------------

    def emit_package(self, pkg: SVPackage) -> str:
        """Emit a ``package … endpackage`` block."""
        lines: List[str] = [f"package {pkg.name};"]
        for item in pkg.items:
            lines.append("")
            for subline in self.emit_one(item).splitlines():
                lines.append(f"  {subline}")
        lines.append("")
        lines.append(f"endpackage : {pkg.name}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Escape-hatch serialisation
    # ------------------------------------------------------------------

    def emit_raw_item(self, item: SVRawItem) -> str:
        """Emit raw SV lines verbatim."""
        return "\n".join(item.lines)

    # ------------------------------------------------------------------
    # Stub serialisation (future phases)
    # ------------------------------------------------------------------

    def emit_class(self, cls: SVClass) -> str:
        """Emit a class stub (placeholder — full impl in a future phase)."""
        return f"// SVClass stub: {cls.name}"

    def emit_interface(self, iface: SVInterface) -> str:
        """Emit an interface stub (placeholder — full impl in a future phase)."""
        return f"// SVInterface stub: {iface.name}"

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def emit_one(self, construct: Any) -> str:
        """Serialise one SV IR or RTL IR node."""
        if isinstance(construct, SVTypedefStruct):
            return self.emit_typedef_struct(construct)
        if isinstance(construct, SVTypedefEnum):
            return self.emit_typedef_enum(construct)
        if isinstance(construct, SVTypedefUnion):
            return self.emit_typedef_union(construct)
        if isinstance(construct, SVPackage):
            return self.emit_package(construct)
        if isinstance(construct, SVRawItem):
            return self.emit_raw_item(construct)
        if isinstance(construct, SVClass):
            return self.emit_class(construct)
        if isinstance(construct, SVInterface):
            return self.emit_interface(construct)
        # Delegate RTL types to the embedded RTLEmitter
        if isinstance(construct, (RTLModule, RTLRawModule)):
            return self._rtl.emit_one(construct)
        raise TypeError(f"SVEmitter.emit_one: unknown construct type {type(construct).__name__}")

    def emit_all(self, constructs: List[Any]) -> str:
        """Serialise an ordered list of SV and RTL IR nodes to a single SV source string.

        Each node is emitted via :meth:`emit_one`.  Results are joined so
        that the final string matches the line-by-line concatenation used
        throughout the pipeline (identical to ``RTLEmitter.emit_all``
        behaviour for RTL-only lists).
        """
        flat: List[str] = []
        for construct in constructs:
            if isinstance(construct, RTLRawModule):
                flat.extend(construct.lines)
            else:
                flat.extend(self.emit_one(construct).splitlines())
        return "\n".join(flat)

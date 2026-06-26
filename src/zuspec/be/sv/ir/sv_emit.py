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

import dataclasses as dc
import math
from typing import Any, List

from zuspec.be.sv.ir.rtl import RTLModule, RTLRawModule
from zuspec.be.sv.ir.rtl_emit import RTLEmitter
from zuspec.be.sv.ir.sv import (
    SVArg,
    SVClass,
    SVClassField,
    SVConstraintBlock,
    SVField,
    SVForwardDecl,
    SVFunctionDecl,
    SVImportDPI,
    SVInterface,
    SVInterfaceClass,
    SVLineDirective,
    SVModuleDecl,
    SVPackage,
    SVRawItem,
    SVTaskDecl,
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
        self._constraint = None  # lazily constructed SVConstraintEmitter
        self._stmt = None        # lazily constructed SVStmtEmitter

    def _body_lines(self, decl, indent: str) -> List[str]:
        """Render a task/function body: structured ``body`` if present
        (rendered via SVStmtEmitter), else the legacy ``body_lines`` strings."""
        body = getattr(decl, "body", None)
        if body is not None:
            if self._stmt is None:
                from .stmt_emit import SVStmtEmitter
                self._stmt = SVStmtEmitter()
            return self._stmt.emit_stmts(body, indent + "  ")
        return [f"{indent}  {line}" for line in decl.body_lines]

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
    # Argument / field helpers
    # ------------------------------------------------------------------

    def _emit_arg_list(self, args: List[SVArg]) -> str:
        """Format a task/function argument list."""
        parts: List[str] = []
        for a in args:
            parts.append(f"{a.direction} {a.dtype} {a.name}")
        return ", ".join(parts)

    def _emit_class_field(self, cf: SVClassField, indent: str = "  ") -> str:
        """Emit one class field declaration line."""
        qual = ""
        if cf.is_static:
            qual = "static "
        if cf.is_randc:
            qual += "randc "
        elif cf.is_rand:
            qual += "rand "
        init = f" = {cf.initial_value}" if cf.initial_value is not None else ""
        return f"{indent}{qual}{cf.dtype} {cf.name}{init};"

    # ------------------------------------------------------------------
    # Constraint block
    # ------------------------------------------------------------------

    def emit_constraint_block(self, cb, indent: str = "  ") -> str:
        """Emit a ``constraint name { ... }`` block.

        Accepts either the legacy :class:`SVConstraintBlock` (pre-rendered
        ``exprs: List[str]``) or a structured ``zuspec.ir.core.ConstraintBlock``
        (rendered via :class:`SVConstraintEmitter`, decision D3/D4).
        """
        from zuspec.ir.core.constraint import ConstraintBlock as CoreConstraintBlock
        if isinstance(cb, CoreConstraintBlock):
            if self._constraint is None:
                from .constraint_emit import SVConstraintEmitter
                self._constraint = SVConstraintEmitter()
            return self._constraint.emit_block(cb, indent=indent)
        lines: List[str] = [f"{indent}constraint {cb.name} {{"]
        for expr in cb.exprs:
            lines.append(f"{indent}  {expr};")
        lines.append(f"{indent}}}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Task / function declarations
    # ------------------------------------------------------------------

    def emit_task_decl(self, td: SVTaskDecl, indent: str = "  ") -> str:
        """Emit a task declaration."""
        args_str = f"({self._emit_arg_list(td.args)})" if td.args else "()"
        if td.is_pure:
            return f"{indent}pure virtual task {td.name}{args_str};"
        prefix = "virtual " if td.is_virtual else ""
        lines: List[str] = [f"{indent}{prefix}task {td.name}{args_str};"]
        lines.extend(self._body_lines(td, indent))
        lines.append(f"{indent}endtask")
        return "\n".join(lines)

    def emit_function_decl(self, fd: SVFunctionDecl, indent: str = "  ") -> str:
        """Emit a function declaration."""
        args_str = f"({self._emit_arg_list(fd.args)})" if fd.args else "()"
        if fd.is_pure:
            return f"{indent}pure virtual function {fd.return_type} {fd.name}{args_str};"
        prefix = "static " if fd.is_static else ("virtual " if fd.is_virtual else "")
        ret_str = f"{fd.return_type} " if fd.return_type else ""
        lines: List[str] = [f"{indent}{prefix}function {ret_str}{fd.name}{args_str};"]
        lines.extend(self._body_lines(fd, indent))
        lines.append(f"{indent}endfunction")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Class serialisation
    # ------------------------------------------------------------------

    def emit_class(self, cls: SVClass) -> str:
        """Emit a full ``[virtual] class name [extends base]; ... endclass``."""
        parts: List[str] = []

        # Forward declarations
        for fwd in cls.forward_decls:
            parts.append(f"typedef class {fwd};")

        # Class header
        kw = "virtual class" if cls.is_virtual else "class"
        ext = f" extends {cls.extends_name}" if cls.extends_name else ""
        impl = f" implements {', '.join(cls.implements)}" if cls.implements else ""
        parts.append(f"{kw} {cls.name}{ext}{impl};")

        # Fields
        for cf in cls.fields:
            parts.append(self._emit_class_field(cf))

        # Constraints
        for cb in cls.constraints:
            parts.append("")
            parts.append(self.emit_constraint_block(cb))

        # Functions
        for fd in cls.functions:
            parts.append("")
            parts.append(self.emit_function_decl(fd))

        # Tasks
        for td in cls.tasks:
            parts.append("")
            parts.append(self.emit_task_decl(td))

        # Miscellaneous items
        for item in cls.items:
            parts.append("")
            parts.append(f"  {self.emit_one(item)}")

        parts.append(f"endclass")
        return "\n".join(parts)

    def emit_interface_class(self, ic: SVInterfaceClass) -> str:
        """Emit an ``interface class name [extends I, ...]; ... endclass`` block.

        Bodies contain only pure-virtual prototypes; method declarations are
        forced to ``pure virtual`` regardless of their flags, since that is the
        only legal form inside an interface class.
        """
        parts: List[str] = []

        for fwd in ic.forward_decls:
            parts.append(f"typedef class {fwd};")

        ext = f" extends {', '.join(ic.extends)}" if ic.extends else ""
        parts.append(f"interface class {ic.name}{ext};")

        for fd in ic.functions:
            parts.append(self.emit_function_decl(dc.replace(fd, is_pure=True)))
        for td in ic.tasks:
            parts.append(self.emit_task_decl(dc.replace(td, is_pure=True)))

        parts.append("endclass")
        return "\n".join(parts)

    def emit_interface(self, iface: SVInterface) -> str:
        """Emit an interface stub (placeholder — full impl in a future phase)."""
        return f"// SVInterface stub: {iface.name}"

    # ------------------------------------------------------------------
    # DPI import
    # ------------------------------------------------------------------

    def emit_import_dpi(self, dpi: SVImportDPI) -> str:
        """Emit an ``import "DPI-C" function/task ...;`` declaration."""
        args_str = f"({self._emit_arg_list(dpi.args)})" if dpi.args else "()"
        if dpi.func_or_task == "task":
            return f'import "{dpi.language}" task {dpi.name}{args_str};'
        return f'import "{dpi.language}" function {dpi.return_type} {dpi.name}{args_str};'

    # ------------------------------------------------------------------
    # Module (non-RTL, testbench)
    # ------------------------------------------------------------------

    def emit_module_decl(self, mod: SVModuleDecl) -> str:
        """Emit a ``module name; ... endmodule`` block."""
        lines: List[str] = [f"module {mod.name};"]
        for line in mod.body_lines:
            lines.append(f"  {line}")
        lines.append(f"endmodule")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Line directive
    # ------------------------------------------------------------------

    def emit_line_directive(self, ld: SVLineDirective) -> str:
        """Emit a ```line`` directive."""
        return f'`line {ld.lineno} "{ld.filename}" 0'

    # ------------------------------------------------------------------
    # Forward declaration
    # ------------------------------------------------------------------

    def emit_forward_decl(self, fd: SVForwardDecl) -> str:
        """Emit a ``typedef class name;`` forward declaration."""
        return f"typedef class {fd.class_name};"

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
        if isinstance(construct, SVInterfaceClass):
            return self.emit_interface_class(construct)
        if isinstance(construct, SVInterface):
            return self.emit_interface(construct)
        if isinstance(construct, SVClassField):
            return self._emit_class_field(construct, indent="")
        if isinstance(construct, SVConstraintBlock):
            return self.emit_constraint_block(construct, indent="")
        from zuspec.ir.core.constraint import ConstraintBlock as _CoreCB
        if isinstance(construct, _CoreCB):
            return self.emit_constraint_block(construct, indent="")
        if isinstance(construct, SVTaskDecl):
            return self.emit_task_decl(construct, indent="")
        if isinstance(construct, SVFunctionDecl):
            return self.emit_function_decl(construct, indent="")
        if isinstance(construct, SVImportDPI):
            return self.emit_import_dpi(construct)
        if isinstance(construct, SVModuleDecl):
            return self.emit_module_decl(construct)
        if isinstance(construct, SVLineDirective):
            return self.emit_line_directive(construct)
        if isinstance(construct, SVForwardDecl):
            return self.emit_forward_decl(construct)
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

"""RTLEmitter — serialise ``RTLModule`` / ``RTLRawModule`` objects to SystemVerilog text."""
from __future__ import annotations

from typing import Any, List, Union

from zuspec.be.sv.ir.rtl import (
    PortDirection,
    RTLAlways,
    RTLAssign,
    RTLInstance,
    RTLModule,
    RTLPort,
    RTLRawModule,
    RTLWire,
)
from zuspec.be.sv.ir.rtl_expr import (
    RTLBinop,
    RTLCase,
    RTLConcat,
    RTLExpr,
    RTLIdent,
    RTLLiteral,
    RTLRawExpr,
    RTLSlice,
    RTLTernary,
    RTLUnop,
)


class RTLEmitter:
    """Serialises ``RTLModule`` and ``RTLRawModule`` objects to SystemVerilog text.

    Usage::

        emitter = RTLEmitter()
        sv_text = emitter.emit_all(ir.rtl_modules)

    The emitter handles both structured ``RTLModule`` objects (where it
    generates SV from the IR fields) and ``RTLRawModule`` objects (where it
    emits the raw text lines verbatim).
    """

    # ------------------------------------------------------------------
    # Expression serialisation
    # ------------------------------------------------------------------

    def emit_expr(self, expr: RTLExpr) -> str:
        """Convert an ``RTLExpr`` to a SystemVerilog expression string."""
        if isinstance(expr, RTLRawExpr):
            return expr.sv
        if isinstance(expr, RTLLiteral):
            if expr.width > 0:
                return f"{expr.width}'d{expr.value}"
            return str(expr.value)
        if isinstance(expr, RTLIdent):
            return expr.name
        if isinstance(expr, RTLSlice):
            base = self.emit_expr(expr.base)
            return f"{base}[{expr.hi}:{expr.lo}]"
        if isinstance(expr, RTLConcat):
            parts = ", ".join(self.emit_expr(p) for p in expr.parts)
            return "{" + parts + "}"
        if isinstance(expr, RTLBinop):
            lhs = self.emit_expr(expr.lhs)
            rhs = self.emit_expr(expr.rhs)
            return f"({lhs} {expr.op} {rhs})"
        if isinstance(expr, RTLUnop):
            operand = self.emit_expr(expr.operand)
            return f"{expr.op}{operand}"
        if isinstance(expr, RTLTernary):
            cond = self.emit_expr(expr.cond)
            then_ = self.emit_expr(expr.then_)
            else_ = self.emit_expr(expr.else_)
            return f"({cond} ? {then_} : {else_})"
        if isinstance(expr, RTLCase):
            return self._emit_case_expr(expr)
        raise TypeError(f"Unknown RTLExpr subtype: {type(expr).__name__}")

    def _emit_case_expr(self, expr: RTLCase) -> str:
        """Emit a case *expression* (not a statement) as a ternary chain."""
        sel = self.emit_expr(expr.sel)
        result = "'x"  # fallback
        for item in reversed(expr.items):
            body = self.emit_expr(item.body) if item.body is not None else "'x"
            if any(m is None for m in item.matches):
                result = body
            else:
                conds = " || ".join(
                    f"(({sel}) == ({self.emit_expr(m)}))" for m in item.matches
                )
                result = f"({conds} ? {body} : {result})"
        return result

    # ------------------------------------------------------------------
    # Statement / block serialisation
    # ------------------------------------------------------------------

    def _emit_port(self, port: RTLPort, is_last: bool) -> str:
        dir_str = port.direction.value
        wspec = f"[{port.width - 1}:0] " if port.width > 1 else ""
        comma = "" if is_last else ","
        dir_pad = "input " if dir_str == "input" else "output"
        return f"  {dir_pad} wire {wspec}{port.name}{comma}"

    def _emit_wire(self, wire: RTLWire) -> str:
        if wire.dtype:
            return f"  wire {wire.dtype} {wire.name};"
        if wire.width > 1:
            return f"  wire [{wire.width - 1}:0] {wire.name};"
        return f"  wire {wire.name};"

    def _emit_assign(self, assign: RTLAssign) -> str:
        lhs = self.emit_expr(assign.lhs)
        rhs = self.emit_expr(assign.rhs)
        return f"  assign {lhs} = {rhs};"

    def _emit_always(self, always: RTLAlways, indent: str = "  ") -> List[str]:
        lines: List[str] = []
        if always.sensitivity:
            sens = " or ".join(always.sensitivity)
            lines.append(f"{indent}always @({sens}) begin")
        else:
            lines.append(f"{indent}always @(*) begin")
        for item in always.body:
            if isinstance(item, str):
                lines.append(f"{indent}  {item}")
            else:
                lines.append(f"{indent}  {self.emit_expr(item)}")
        lines.append(f"{indent}end")
        return lines

    def _emit_instance(self, inst: RTLInstance) -> List[str]:
        lines: List[str] = []
        lines.append(f"  {inst.module_name} {inst.inst_name} (")
        conns = list(inst.port_map.items())
        for i, (port, conn) in enumerate(conns):
            conn_str = self.emit_expr(conn)
            comma = "" if i == len(conns) - 1 else ","
            lines.append(f"    .{port:<28} ({conn_str}){comma}")
        lines.append("  );")
        return lines

    # ------------------------------------------------------------------
    # Module serialisation
    # ------------------------------------------------------------------

    def emit_module(self, mod: RTLModule) -> str:
        """Serialise one ``RTLModule`` to a SystemVerilog module string."""
        lines: List[str] = []
        lines.append(f"module {mod.name} (")
        for i, port in enumerate(mod.ports):
            lines.append(self._emit_port(port, i == len(mod.ports) - 1))
        lines.append(");")

        if mod.wires:
            lines.append("")
            for wire in mod.wires:
                lines.append(self._emit_wire(wire))

        if mod.assigns:
            lines.append("")
            for assign in mod.assigns:
                lines.append(self._emit_assign(assign))

        if mod.always_blocks:
            lines.append("")
            for always in mod.always_blocks:
                lines.extend(self._emit_always(always))

        if mod.instances:
            lines.append("")
            for inst in mod.instances:
                lines.extend(self._emit_instance(inst))
                lines.append("")

        lines.append("endmodule")
        return "\n".join(lines)

    def emit_raw_module(self, mod: RTLRawModule) -> str:
        """Serialise one ``RTLRawModule`` by joining its lines verbatim."""
        return "\n".join(mod.lines)

    def emit_one(self, mod: Any) -> str:
        """Serialise one module (``RTLModule`` or ``RTLRawModule``)."""
        if isinstance(mod, RTLModule):
            return self.emit_module(mod)
        if isinstance(mod, RTLRawModule):
            return self.emit_raw_module(mod)
        raise TypeError(f"emit_one: unknown module type {type(mod).__name__}")

    def emit_all(self, modules: List[Any]) -> str:
        """Serialise an ordered list of modules to a single SV source string.

        Each module is emitted via :meth:`emit_one`, and the results are
        concatenated by joining all lines from all modules in order — exactly
        mirroring how ``_generate_pipeline_sv`` builds its ``out`` list.
        The final string ends with a trailing newline.
        """
        flat: List[str] = []
        for mod in modules:
            if isinstance(mod, RTLRawModule):
                flat.extend(mod.lines)
            elif isinstance(mod, RTLModule):
                flat.extend(self.emit_module(mod).splitlines())
            else:
                raise TypeError(f"emit_all: unknown module type {type(mod).__name__}")
        return "\n".join(flat)

"""RTLEmitter — serialise ``RTLModule`` / ``RTLRawModule`` objects to SystemVerilog text."""
from __future__ import annotations

from typing import Any, List, Union

from zuspec.be.sv.ir.rtl import (
    PortDirection,
    RTLAlways,
    RTLAssign,
    RTLInstance,
    RTLMemory,
    RTLModule,
    RTLParameter,
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
        comma = "" if is_last else ","
        dir_pad = "input " if dir_str == "input" else "output"
        dtype = getattr(port, "dtype", None)
        if dtype:
            # Typedef-typed port (e.g. a struct interface bundle): no net/width.
            return f"  {dir_pad} {dtype} {port.name}{comma}"
        wspec = f"[{port.width - 1}:0] " if port.width > 1 else ""
        net = getattr(port, "net", "wire")
        return f"  {dir_pad} {net} {wspec}{port.name}{comma}"

    def emit_port_decl_fragment(self, ports: List[RTLPort], indent: str = "  ") -> str:
        """Render ``RTLPort``\\s as a module-header port-list *fragment*.

        Unlike :meth:`emit_module` (which owns the final-port comma), every line
        of a fragment ends with a comma — the fragment is meant to be spliced
        into an enclosing ``module`` header whose assembler manages commas.  Set
        ``net="logic"`` on the ports for procedurally-driven signals (the
        IfProtocol port bundles do).

        Args:
            ports:  Ports to render.
            indent: Whitespace prefix for each line.

        Returns:
            Multi-line fragment (each line ``<indent><dir> <net> [<w>:0] <name>,``),
            terminated with a newline, or ``""`` for an empty port list.
        """
        out: List[str] = []
        for p in ports:
            wspec = f"[{p.width - 1}:0] " if p.width > 1 else ""
            out.append(f"{indent}{p.direction.value} {p.net} {wspec}{p.name},")
        return "\n".join(out) + ("\n" if out else "")

    def emit_port_connections(self, port_names: List[str], indent: str = "  ") -> str:
        """Render ``.name(name),`` connection lines for a submodule instantiation.

        A by-name connection *fragment* (no module/instance wrapper) — mirrors the
        legacy ``generate_port_instantiation`` snippet.

        Args:
            port_names: Signal names to connect (port == local signal name).
            indent:     Whitespace prefix for each line.

        Returns:
            Multi-line fragment (each line ``<indent>.<name>(<name>),``),
            terminated with a newline, or ``""`` for an empty list.
        """
        out = [f"{indent}.{n}({n})," for n in port_names]
        return "\n".join(out) + ("\n" if out else "")

    def _emit_wire(self, wire: RTLWire) -> str:
        if wire.dtype:
            # A typedef'd variable (e.g. a state enum) takes no net keyword.
            decl = f"  {wire.dtype} {wire.name};"
        else:
            net = getattr(wire, "net", "wire")
            if wire.width > 1:
                decl = f"  {net} [{wire.width - 1}:0] {wire.name};"
            else:
                decl = f"  {net} {wire.name};"
        lint_off = getattr(wire, "lint_off", None)
        if lint_off:
            offs = "\n".join(f"  /* verilator lint_off {w} */" for w in lint_off)
            ons = "\n".join(f"  /* verilator lint_on {w} */" for w in reversed(lint_off))
            return f"{offs}\n{decl}\n{ons}"
        return decl

    def _emit_memory(self, mem: RTLMemory) -> str:
        elem = mem.dtype if mem.dtype else (
            f"logic [{mem.width - 1}:0]" if mem.width > 1 else "logic")
        return f"  {elem} {mem.name} [0:{mem.depth - 1}];"

    def _emit_localparam(self, p: RTLParameter) -> str:
        return f"  localparam {p.name} = {p.value};"

    def _emit_assign(self, assign: RTLAssign) -> str:
        lhs = self.emit_expr(assign.lhs)
        rhs = self.emit_expr(assign.rhs)
        return f"  assign {lhs} = {rhs};"

    def _stmt_emitter(self):
        """Lazily build (and cache) an ``SVStmtEmitter`` for structured bodies.

        Imported lazily to keep ``rtl_emit`` loadable without pulling the full
        SV statement/expression stack unless an ``always`` body actually carries
        structured ``SVStmt`` nodes (e.g. the ``case``/``if`` of an FSM).
        """
        e = getattr(self, "_stmt_e", None)
        if e is None:
            from zuspec.be.sv.ir.stmt_emit import SVStmtEmitter
            e = SVStmtEmitter()
            self._stmt_e = e
        return e

    def _emit_always(self, always: RTLAlways, indent: str = "  ") -> List[str]:
        # Structured procedural statements (SVStmt: case/if/assign/...) may appear
        # in an always body — e.g. a lowered FSM's next-state case.  They are
        # delegated to SVStmtEmitter; plain strings and RTLExpr items are kept for
        # backward compatibility.
        from zuspec.be.sv.ir.stmt import SVStmt
        lines: List[str] = []
        if always.sensitivity:
            sens = " or ".join(always.sensitivity)
            lines.append(f"{indent}always @({sens}) begin")
        else:
            lines.append(f"{indent}always @(*) begin")
        for item in always.body:
            if isinstance(item, str):
                lines.append(f"{indent}  {item}")
            elif isinstance(item, SVStmt):
                lines.extend(self._stmt_emitter().emit_stmts([item], indent + "  "))
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
        header_params = [p for p in mod.params if not p.local]
        if header_params:
            lines.append(f"module {mod.name} #(")
            for i, p in enumerate(header_params):
                comma = "" if i == len(header_params) - 1 else ","
                lines.append(f"  parameter {p.name} = {p.value}{comma}")
            lines.append(") (")
        else:
            lines.append(f"module {mod.name} (")
        for i, port in enumerate(mod.ports):
            lines.append(self._emit_port(port, i == len(mod.ports) - 1))
        lines.append(");")

        local_params = [p for p in mod.params if p.local]
        if local_params:
            lines.append("")
            for p in local_params:
                lines.append(self._emit_localparam(p))

        if mod.wires:
            lines.append("")
            for wire in mod.wires:
                lines.append(self._emit_wire(wire))

        if mod.memories:
            lines.append("")
            for mem in mod.memories:
                lines.append(self._emit_memory(mem))

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

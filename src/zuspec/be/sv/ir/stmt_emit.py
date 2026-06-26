"""SVStmtEmitter — render structured SV procedural statements (plan task C3).

Consumes the ``ir/stmt.py`` statement nodes and produces SystemVerilog text
lines, delegating expression rendering to :class:`SVExprEmitter` (D4). Block
bodies always use ``begin``/``end`` for safety.
"""
from __future__ import annotations

from typing import List

from . import stmt as s
from .expr_emit import SVExprEmitter


class SVStmtEmitter:
    """Render structured SV statements to indented SV text lines."""

    def __init__(self, expr_emitter: SVExprEmitter | None = None) -> None:
        self._e = expr_emitter or SVExprEmitter()
        self._constraint = None  # lazily constructed SVConstraintEmitter

    def emit_stmts(self, stmts: List[s.SVStmt], indent: str = "  ") -> List[str]:
        out: List[str] = []
        for st in stmts:
            out.extend(self._emit(st, indent))
        return out

    # ------------------------------------------------------------------ #

    def _emit(self, st: s.SVStmt, indent: str) -> List[str]:
        m = getattr(self, "_emit_" + type(st).__name__, None)
        if m is None:
            raise NotImplementedError(
                f"SVStmtEmitter: no rendering for {type(st).__name__}")
        return m(st, indent)

    def _block(self, body, indent) -> List[str]:
        """Render a begin/end block body at the next indent level."""
        return self.emit_stmts(body, indent + "  ")

    # ------------------------------------------------------------------ #
    # Leaves                                                               #
    # ------------------------------------------------------------------ #

    def _emit_SVStmtRaw(self, st, indent) -> List[str]:
        return [f"{indent}{st.text}"]

    def _emit_SVStmtComment(self, st, indent) -> List[str]:
        return [f"{indent}// {st.text}"]

    def _emit_SVVarDecl(self, st, indent) -> List[str]:
        init = f" = {self._e.emit(st.init)}" if st.init is not None else ""
        return [f"{indent}{st.dtype} {st.name}{init};"]

    def _emit_SVStmtExpr(self, st, indent) -> List[str]:
        return [f"{indent}{self._e.emit(st.expr)};"]

    def _emit_SVStmtAssign(self, st, indent) -> List[str]:
        return [f"{indent}{self._e.emit(st.lhs)} {st.op} {self._e.emit(st.rhs)};"]

    def _emit_SVStmtReturn(self, st, indent) -> List[str]:
        if st.value is None:
            return [f"{indent}return;"]
        return [f"{indent}return {self._e.emit(st.value)};"]

    # ------------------------------------------------------------------ #
    # Control flow                                                         #
    # ------------------------------------------------------------------ #

    def _emit_SVStmtIf(self, st, indent) -> List[str]:
        lines = [f"{indent}if ({self._e.emit(st.cond)}) begin"]
        lines.extend(self._block(st.then_body, indent))
        if st.else_body:
            lines.append(f"{indent}end else begin")
            lines.extend(self._block(st.else_body, indent))
        lines.append(f"{indent}end")
        return lines

    def _emit_SVStmtFor(self, st, indent) -> List[str]:
        start = self._e.emit(st.start) if st.start is not None else "0"
        limit = self._e.emit(st.limit)
        head = (f"{indent}for ({st.dtype} {st.var} = {start}; "
                f"{st.var} < {limit}; {st.var}++) begin")
        lines = [head]
        lines.extend(self._block(st.body, indent))
        lines.append(f"{indent}end")
        return lines

    def _emit_SVStmtForeach(self, st, indent) -> List[str]:
        arr = self._e.emit(st.array)
        lines = [f"{indent}foreach ({arr}[{st.index_var}]) begin"]
        lines.extend(self._block(st.body, indent))
        lines.append(f"{indent}end")
        return lines

    def _emit_SVStmtWhile(self, st, indent) -> List[str]:
        lines = [f"{indent}while ({self._e.emit(st.cond)}) begin"]
        lines.extend(self._block(st.body, indent))
        lines.append(f"{indent}end")
        return lines

    def _emit_SVStmtRepeat(self, st, indent) -> List[str]:
        lines = [f"{indent}repeat ({self._e.emit(st.count)}) begin"]
        lines.extend(self._block(st.body, indent))
        lines.append(f"{indent}end")
        return lines

    def _emit_SVStmtFork(self, st, indent) -> List[str]:
        lines = [f"{indent}fork"]
        for branch in st.branches:
            lines.append(f"{indent}  begin")
            lines.extend(self.emit_stmts(branch, indent + "    "))
            lines.append(f"{indent}  end")
        lines.append(f"{indent}{st.join}")
        return lines

    def _emit_SVStmtRandomize(self, st, indent) -> List[str]:
        call = f"{self._e.emit(st.target)}.randomize()"
        msg = st.fail_msg or "randomize failed"
        if not st.constraints:
            if st.check:
                return [f'{indent}if (!{call}) $fatal(1, "{msg}");']
            return [f"{indent}void'({call});"]
        if self._constraint is None:
            from .constraint_emit import SVConstraintEmitter
            self._constraint = SVConstraintEmitter(self._e)
        body = self._constraint.emit_items(st.constraints, indent + "  ")
        if st.check:
            head = f"{indent}if (!{call} with {{"
            tail = f'{indent}}}) $fatal(1, "{msg}");'
        else:
            head = f"{indent}void'({call} with {{"
            tail = f"{indent}}});"
        return [head] + body + [tail]

    def _emit_SVStmtCase(self, st, indent) -> List[str]:
        lines = [f"{indent}case ({self._e.emit(st.subject)})"]
        for item in st.items:
            label = ("default" if not item.labels
                     else ", ".join(self._e.emit(l) for l in item.labels))
            lines.append(f"{indent}  {label}: begin")
            lines.extend(self.emit_stmts(item.body, indent + "    "))
            lines.append(f"{indent}  end")
        lines.append(f"{indent}endcase")
        return lines

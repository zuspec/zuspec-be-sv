"""SVConstraintEmitter — render structured constraint IR to SystemVerilog.

Consumes the ``zuspec.ir.core`` constraint model (``ConstraintBlock`` and the
``Constraint`` item hierarchy, plan task A1/D3) and emits SV ``constraint``
blocks, delegating all expression rendering to :class:`SVExprEmitter` (D4).

This replaces the pre-rendered-string approach (``SVConstraintBlock.exprs:
List[str]``): the backend now receives structure and owns the text.
"""
from __future__ import annotations

from typing import List

from zuspec.ir.core import constraint as ic
from .expr_emit import SVExprEmitter


class SVConstraintEmitter:
    """Render a core ``ConstraintBlock`` (and its items) to SV text."""

    def __init__(self, expr_emitter: SVExprEmitter | None = None) -> None:
        self._e = expr_emitter or SVExprEmitter()

    # ------------------------------------------------------------------ #
    # Public entry points                                                  #
    # ------------------------------------------------------------------ #

    def emit_block(self, blk: ic.ConstraintBlock, indent: str = "  ") -> str:
        """Emit a full ``constraint name { ... }`` block."""
        lines: List[str] = [f"{indent}constraint {blk.name} {{"]
        for item in blk.items:
            lines.extend(self._emit_item(item, indent + "  "))
        lines.append(f"{indent}}}")
        return "\n".join(lines)

    def emit_items(self, items: List[ic.Constraint], indent: str = "") -> List[str]:
        """Emit a list of constraint items (no enclosing block)."""
        out: List[str] = []
        for item in items:
            out.extend(self._emit_item(item, indent))
        return out

    # ------------------------------------------------------------------ #
    # Item dispatch                                                        #
    # ------------------------------------------------------------------ #

    def _emit_item(self, item: ic.Constraint, indent: str) -> List[str]:
        m = getattr(self, "_emit_" + type(item).__name__, None)
        if m is None:
            raise NotImplementedError(
                f"SVConstraintEmitter: no rendering for {type(item).__name__}")
        return m(item, indent)

    def _emit_ConstraintExpr(self, item, indent) -> List[str]:
        return [f"{indent}{self._e.emit(item.expr)};"]

    def _emit_ConstraintSoft(self, item, indent) -> List[str]:
        return [f"{indent}soft {self._e.emit(item.expr)};"]

    def _emit_ConstraintImplies(self, item, indent) -> List[str]:
        # SV allows `ant -> { constraint_set }`, but some simulators (notably
        # Verilator) only accept `ant -> constraint_expression`. We therefore
        # render an all-expression body as `ant -> (e1 && e2 && ...)`, and a body
        # with structured items via the equivalent `if (ant) { body }` form
        # (both LRM-valid and broadly supported).
        ant = self._e.emit(item.antecedent)
        if all(isinstance(c, ic.ConstraintExpr) for c in item.body):
            conj = " && ".join(self._e.emit(c.expr) for c in item.body)
            return [f"{indent}{ant} -> ({conj});"]
        lines = [f"{indent}if ({ant}) {{"]
        for c in item.body:
            lines.extend(self._emit_item(c, indent + "  "))
        lines.append(f"{indent}}}")
        return lines

    def _emit_ConstraintIfElse(self, item, indent) -> List[str]:
        lines = [f"{indent}if ({self._e.emit(item.cond)}) {{"]
        for c in item.then_body:
            lines.extend(self._emit_item(c, indent + "  "))
        if item.else_body:
            lines.append(f"{indent}}} else {{")
            for c in item.else_body:
                lines.extend(self._emit_item(c, indent + "  "))
        lines.append(f"{indent}}}")
        return lines

    def _emit_ConstraintForeach(self, item, indent) -> List[str]:
        arr = self._e.emit(item.array)
        lines = [f"{indent}foreach ({arr}[{item.index_var}]) {{"]
        for c in item.body:
            lines.extend(self._emit_item(c, indent + "  "))
        lines.append(f"{indent}}}")
        return lines

    def _emit_ConstraintUnique(self, item, indent) -> List[str]:
        items = ", ".join(self._e.emit(x) for x in item.items)
        return [f"{indent}unique {{{items}}};"]

    def _emit_ConstraintDist(self, item, indent) -> List[str]:
        weights = ", ".join(self._emit_weight(w) for w in item.weights)
        return [f"{indent}{self._e.emit(item.target)} dist {{{weights}}};"]

    def _emit_weight(self, w: ic.DistWeight) -> str:
        rng = self._e.emit(w.rng)
        if w.weight is None:
            return rng
        op = ":/" if w.per_value else ":="
        return f"{rng} {op} {self._e.emit(w.weight)}"

    def _emit_ConstraintSolveBefore(self, item, indent) -> List[str]:
        before = ", ".join(self._e.emit(x) for x in item.before)
        after = ", ".join(self._e.emit(x) for x in item.after)
        return [f"{indent}solve {before} before {after};"]

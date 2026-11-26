"""SystemVerilog for a component's process blocks (fw-hdl layer 2,
core-layer2 §7; ``zuspec.ir.core.proc_block``).

:func:`proc_block_lines` writes ``DataTypeComponent.proc_blocks``, after
a pass has resolved their domains to the module's nets (``clock_net``,
``reset_net``, ``ExprInReset.net``):

- a step block as ``always_ff @(posedge <clock_net>)``, with
  ``or posedge <reset_net>`` (``negedge`` when active low) for an
  asynchronous reset. Its ``reset_body`` is the branch taken while the
  reset is asserted, its ``body`` the other. Assignments are nonblocking:
  the block writes registers, whose new values are seen at the next edge.
  A plain state variable (``ProcBlock.state``) is the exception: the block
  works on a local copy (``<name>_v``) with blocking assignments, so later
  statements see a write, and the register takes the copy at the end.
- a comb block as ``always_comb`` with blocking assignments.

A block's locals (``StmtAnnAssign``, wherever they are in the body) are
declared at the top of its ``always`` block, renamed when a field has the
name, and assigned with blocking assignments where the body declares them:
the initializer, else the type's default (``'0``), so each evaluation
starts from it as the class code's automatic locals do. ``ExprCast`` to a
``DataType`` is ``T'(v)`` (``N'(v)`` for a vector).

A field reference (``ExprRefField`` of ``self``) is the net of the field
with that index in the component's ``fields``; ``ExprInReset`` is its
reset net, inverted when active low. The writers are the ones the front
end checked (one block per signal), so the nets the blocks write are the
component's ``logic`` nets and ports as :mod:`.structural` declares them.
"""
from __future__ import annotations

from typing import List

import zuspec.ir.core as ir

from .sync_param import _BIN, _CMP, _IFEXP, _UN, _base, _decl


class ProcEmitter:
    """Writes the ``proc_blocks`` of one component."""

    def __init__(self, comp: ir.DataTypeComponent, decl=None, type_name=None):
        self.comp = comp
        self.fields = comp.fields
        self.local = {}     # state field -> its block-local copy
        self.vars = {}      # block local -> its name in the always block
        # ``decl(dt, name)``: a declaration; ``type_name(dt)``: a named
        # type's SV name (the module's types package)
        self.decl = decl or _decl
        self.type_name = type_name or _base

    def _locals(self, b: ir.ProcBlock, ind: str, names: set) -> List[str]:
        """Declares the block's locals (``self.vars``)."""
        out: List[str] = []
        self.vars = {}

        def walk(body):
            for s in body:
                if isinstance(s, ir.StmtAnnAssign) and isinstance(s.target, ir.ExprRefLocal):
                    n = s.target.name
                    if n in self.vars:
                        continue
                    if s.ir_type is None:
                        raise ValueError(f"block {b.name!r}: local {n!r} has no type")
                    v = n
                    while v in names:
                        v += "_"
                    names.add(v)
                    self.vars[n] = v
                    out.append(f"{ind}{self.decl(s.ir_type, v)};")
                for k in ("body", "orelse"):
                    walk(getattr(s, k, None) or [])
        walk(b.reset_body)
        walk(b.body)
        return out

    def lines(self, ind: str = "  ") -> List[str]:
        out: List[str] = []
        for b in self.comp.proc_blocks:
            out.extend(self.block(b, ind))
            out.append("")
        return out

    def block(self, b: ir.ProcBlock, ind: str) -> List[str]:
        if b.kind == ir.ProcKind.TASK:
            raise ValueError(f"task block {b.name!r}: task code is simulation only, "
                             "not lowered to RTL")
        out = [f"{ind}// {b.name}"]
        if b.kind == ir.ProcKind.COMB:
            out.append(f"{ind}always_comb begin")
            out.extend(self._locals(b, ind + "  ", {f.name for f in self.fields}))
            out.extend(self.stmts(b.body, "=", ind + "  "))
            out.append(f"{ind}end")
            self.vars = {}
            return out
        if not b.clock_net:
            raise ValueError(f"step block {b.name!r}: clock not resolved to a net")
        if b.reset_body and not b.reset_net:
            raise ValueError(f"step block {b.name!r}: reset not resolved to a net")
        ev = f"posedge {b.clock_net}"
        if b.reset_body and b.reset_async:
            ev += f" or {'negedge' if b.reset_active_low else 'posedge'} {b.reset_net}"
        out.append(f"{ind}always_ff @({ev}) begin")
        names = {f.name for f in self.fields}
        self.local = {}
        for n in b.state:
            dt = next(f.datatype for f in self.fields if f.name == n)
            v = n + "_v"
            while v in names:
                v += "_"
            names.add(v)
            self.local[n] = v
            out.append(f"{ind}  {self.decl(dt, v)};")
        out.extend(self._locals(b, ind + "  ", names))
        for n, v in self.local.items():
            out.append(f"{ind}  {v} = {n};")
        if b.reset_body:
            test = f"!{b.reset_net}" if b.reset_active_low else b.reset_net
            out.append(f"{ind}  if ({test}) begin")
            out.extend(self.stmts(b.reset_body, "<=", ind + "    "))
            out.append(f"{ind}  end else begin")
            out.extend(self.stmts(b.body, "<=", ind + "    "))
            out.append(f"{ind}  end")
        else:
            out.extend(self.stmts(b.body, "<=", ind + "  "))
        for n, v in self.local.items():
            out.append(f"{ind}  {n} <= {v};")
        self.local = {}
        self.vars = {}
        out.append(f"{ind}end")
        return out

    # -- statements ---------------------------------------------------------

    def stmts(self, body: list, op: str, p: str) -> List[str]:
        out: List[str] = []
        for s in body:
            if isinstance(s, ir.StmtAssign):
                t = self.target(s.targets[0])
                base = t.split("[")[0].split(".")[0]
                o = "=" if base in self.local.values() or base in self.vars.values() else op
                out.append(f"{p}{t} {o} {self.expr(s.value)};")
            elif isinstance(s, ir.StmtAnnAssign):
                v = self.vars[s.target.name]
                out.append(f"{p}{v} = {self.expr(s.value) if s.value is not None else chr(39) + '0'};")
            elif isinstance(s, ir.StmtIf):
                out.append(f"{p}if ({self.expr(s.test)}) begin")
                out.extend(self.stmts(s.body, op, p + "  "))
                if len(s.orelse) == 1 and isinstance(s.orelse[0], ir.StmtIf):
                    nested = self.stmts(s.orelse, op, p)
                    out.append(f"{p}end else {nested[0].lstrip()}")
                    out.extend(nested[1:])
                    continue
                if s.orelse:
                    out.append(f"{p}end else begin")
                    out.extend(self.stmts(s.orelse, op, p + "  "))
                out.append(f"{p}end")
            elif isinstance(s, ir.StmtPass):
                pass
            else:
                raise ValueError(f"unsupported statement {type(s).__name__} in a process block")
        return out

    def target(self, t) -> str:
        if isinstance(t, ir.ExprSubscript):
            return f"{self.target(t.value)}[{self.expr(t.slice)}]"
        if isinstance(t, ir.ExprAttribute):
            return f"{self.target(t.value)}.{t.attr}"
        return self.expr(t)

    # -- expressions --------------------------------------------------------

    def expr(self, e, top: bool = True) -> str:
        s = self._expr(e)
        if not top and isinstance(e, (ir.ExprBin, ir.ExprBool, ir.ExprCompare) + _IFEXP):
            return f"({s})"
        return s

    def _expr(self, e) -> str:
        if isinstance(e, ir.ExprConstant):
            v = int(e.value) if isinstance(e.value, bool) else e.value
            return f"{e.width}'d{v}" if e.width and isinstance(v, int) and v >= 0 else str(v)
        if isinstance(e, ir.ExprRefField):
            if not isinstance(e.base, ir.TypeExprRefSelf) \
                    or not 0 <= e.index < len(self.fields):
                raise ValueError(f"unsupported field reference {e}")
            n = self.fields[e.index].name
            return self.local.get(n, n)
        if isinstance(e, ir.ExprInReset):
            if not e.net:
                raise ValueError("in_reset(): reset not resolved to a net")
            return f"!{e.net}" if e.active_low else e.net
        if isinstance(e, ir.ExprRefLocal):
            return self.vars.get(e.name, e.name)
        if isinstance(e, ir.ExprRefParam):
            return e.name
        if isinstance(e, ir.ExprBin):
            # zuspec-fe-sv maps a comparison to an ExprBin with a CmpOp
            op = _CMP[e.op] if isinstance(e.op, ir.CmpOp) else _BIN[e.op]
            return f"{self.expr(e.lhs, False)} {op} {self.expr(e.rhs, False)}"
        if isinstance(e, ir.ExprBool):
            op = " && " if e.op == ir.BoolOp.And else " || "
            return op.join(self.expr(v, False) for v in e.values)
        if isinstance(e, ir.ExprCompare):
            return f"{self.expr(e.left, False)} {_CMP[e.ops[0]]} " \
                   f"{self.expr(e.comparators[0], False)}"
        if isinstance(e, ir.ExprUnary):
            if e.op == ir.UnaryOp.Not and isinstance(e.operand, ir.ExprInReset) \
                    and e.operand.active_low and e.operand.net:
                return e.operand.net
            return f"{_UN[e.op]}{self.expr(e.operand, False)}"
        if isinstance(e, _IFEXP):
            return f"{self.expr(e.test, False)} ? {self.expr(e.body, False)} : " \
                   f"{self.expr(e.orelse, False)}"
        if isinstance(e, ir.ExprSubscript):
            return f"{self.expr(e.value, False)}[{self.expr(e.slice)}]"
        if isinstance(e, ir.ExprAttribute):
            return f"{self.expr(e.value, False)}.{e.attr}"
        if isinstance(e, ir.ExprCast):
            tt = e.target_type
            if isinstance(tt, ir.DataTypeInt) and tt.width_expr is None:
                return f"{tt.bits}'({self.expr(e.value)})"
            if isinstance(tt, ir.DataTypeRef):
                return f"{self.type_name(tt)}'({self.expr(e.value)})"
            return f"{_base(tt)}'({self.expr(e.value)})"
        raise ValueError(f"unsupported expression {type(e).__name__} in a process block")


def proc_block_lines(comp: ir.DataTypeComponent, ind: str = "  ", decl=None,
                     type_name=None) -> List[str]:
    """The ``always_ff``/``always_comb`` blocks of ``comp.proc_blocks``
    (``decl``, ``type_name``: see :class:`ProcEmitter`)."""
    return ProcEmitter(comp, decl, type_name).lines(ind)

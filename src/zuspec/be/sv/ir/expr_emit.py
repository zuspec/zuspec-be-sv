"""SVExprEmitter — render a ``zuspec.ir.core`` expression to SystemVerilog text.

This is the single expression-rendering path for the SV backend (plan decision
D4): constraints, ``randomize() with`` bodies, and procedural statements all
emit expressions through here, replacing the per-frontend string rendering that
previously lived in ``pssc.targets.sv.lower_exprs`` / ``lower_constraints``.

The emitter is deliberately frontend-agnostic. A few PSS-specific concerns are
exposed as optional hooks so the lowering can inject behavior without coupling
the backend to PSS:

* ``field_resolver(base_sv, index)`` — map an :class:`ExprRefField` index to a
  member name. Defaults to ``field_<index>``.
* ``call_hook(expr) -> Optional[str]`` — intercept :class:`ExprCall` (e.g. PSS
  import-function rewriting). Return ``None`` to fall through to default.
* ``type_namer(datatype) -> str`` — render a :class:`DataType` as an SV type
  name (for casts). Defaults to ``str(datatype.name)``.

Expressions are fully parenthesized at binary/compare/bool/unary boundaries,
matching the prior pssc output and guaranteeing operator-precedence safety.
"""
from __future__ import annotations

from typing import Callable, Optional

from zuspec.ir.core import expr as e


_BINOP = {
    e.BinOp.Add: "+", e.BinOp.Sub: "-", e.BinOp.Mult: "*", e.BinOp.Div: "/",
    e.BinOp.Mod: "%", e.BinOp.FloorDiv: "/", e.BinOp.Exp: "**",
    e.BinOp.BitAnd: "&", e.BinOp.BitOr: "|", e.BinOp.BitXor: "^",
    e.BinOp.LShift: "<<", e.BinOp.RShift: ">>",
    e.BinOp.Eq: "==", e.BinOp.NotEq: "!=", e.BinOp.Lt: "<", e.BinOp.LtE: "<=",
    e.BinOp.Gt: ">", e.BinOp.GtE: ">=", e.BinOp.And: "&&", e.BinOp.Or: "||",
}

_UNARYOP = {
    e.UnaryOp.Not: "!", e.UnaryOp.Invert: "~", e.UnaryOp.USub: "-",
    e.UnaryOp.UAdd: "+",
}

_CMPOP = {
    e.CmpOp.Eq: "==", e.CmpOp.NotEq: "!=", e.CmpOp.Lt: "<", e.CmpOp.LtE: "<=",
    e.CmpOp.Gt: ">", e.CmpOp.GtE: ">=", e.CmpOp.Is: "==", e.CmpOp.IsNot: "!=",
}

# Self-reference collapses: ``self.addr`` -> ``addr`` in SV class scope.
_SELF_TOKENS = {"", "self", "this"}

# SV keywords that can legally appear as PSS identifiers (e.g. the implicit
# ``initial`` field on state objects). Renamed deterministically so field
# declarations and references stay consistent.
_SV_KEYWORDS = frozenset({
    "initial", "final", "begin", "end", "fork", "join", "wait", "event",
    "time", "ref", "bind", "do", "for", "if", "else", "while", "repeat",
    "return", "this", "super", "new", "null", "default", "type", "task",
    "function", "module", "package", "class", "constraint", "rand", "randc",
    "force", "release", "assign", "static", "local", "virtual", "extends",
})


def sv_ident(name: str) -> str:
    """Return an SV-safe identifier (keyword collisions get a ``_`` suffix)."""
    return name + "_" if name in _SV_KEYWORDS else name


class SVExprEmitter:
    """Render ``zuspec.ir.core`` expressions to SystemVerilog strings."""

    def __init__(
        self,
        *,
        self_ref: str = "this",
        field_resolver: Optional[Callable[[str, int], str]] = None,
        call_hook: Optional[Callable[[object], Optional[str]]] = None,
        type_namer: Optional[Callable[[object], str]] = None,
    ) -> None:
        self._self_ref = self_ref
        self._field_resolver = field_resolver
        self._call_hook = call_hook
        self._type_namer = type_namer

    # ------------------------------------------------------------------ #
    # Public entry point                                                   #
    # ------------------------------------------------------------------ #

    def emit(self, expr) -> str:
        """Render *expr* to an SV expression string."""
        m = getattr(self, "_emit_" + type(expr).__name__, None)
        if m is None:
            raise NotImplementedError(
                f"SVExprEmitter: no rendering for {type(expr).__name__}")
        return m(expr)

    # ------------------------------------------------------------------ #
    # Leaves                                                               #
    # ------------------------------------------------------------------ #

    def _emit_ExprConstant(self, x) -> str:
        v = x.value
        if isinstance(v, bool):
            return "1'b1" if v else "1'b0"
        if isinstance(v, int):
            return str(v)
        if isinstance(v, str):
            return f'"{v}"'
        if v is None:
            return "null"
        return str(v)

    def _emit_ExprNull(self, x) -> str:
        return "null"

    def _emit_TypeExprRefSelf(self, x) -> str:
        return self._self_ref

    def _emit_ExprRefLocal(self, x) -> str:
        return x.name

    def _emit_ExprRefParam(self, x) -> str:
        return x.name

    def _emit_ExprRefUnresolved(self, x) -> str:
        return x.name

    def _emit_ExprStaticRef(self, x) -> str:
        return "::".join(x.path)

    # ------------------------------------------------------------------ #
    # Operators                                                           #
    # ------------------------------------------------------------------ #

    def _emit_ExprBin(self, x) -> str:
        op = _BINOP.get(x.op)
        if op is None:
            raise NotImplementedError(f"SVExprEmitter: BinOp {x.op}")
        return f"({self.emit(x.lhs)} {op} {self.emit(x.rhs)})"

    def _emit_ExprUnary(self, x) -> str:
        op = _UNARYOP.get(x.op, "!")
        return f"{op}({self.emit(x.operand)})"

    def _emit_ExprBool(self, x) -> str:
        op = "&&" if x.op == e.BoolOp.And else "||"
        return f" {op} ".join(self.emit(v) for v in x.values)

    def _emit_ExprCompare(self, x) -> str:
        parts = [self.emit(x.left)]
        for op, comp in zip(x.ops, x.comparators):
            if op == e.CmpOp.In:
                # chained membership is unusual; render as `inside`
                parts.append("inside")
                parts.append("{" + self.emit(comp) + "}")
            elif op == e.CmpOp.NotIn:
                parts.append("!inside")
                parts.append("{" + self.emit(comp) + "}")
            else:
                parts.append(_CMPOP.get(op, "=="))
                parts.append(self.emit(comp))
        return f"({' '.join(parts)})"

    def _emit_ExprIfExp(self, x) -> str:
        return f"({self.emit(x.test)} ? {self.emit(x.body)} : {self.emit(x.orelse)})"

    # ------------------------------------------------------------------ #
    # Membership / ranges                                                  #
    # ------------------------------------------------------------------ #

    def _emit_ExprIn(self, x) -> str:
        return f"{self.emit(x.value)} inside {{{self.emit(x.container)}}}"

    def _emit_ExprRange(self, x) -> str:
        if x.upper is not None:
            return f"[{self.emit(x.lower)}:{self.emit(x.upper)}]"
        return self.emit(x.lower)

    def _emit_ExprRangeList(self, x) -> str:
        return ", ".join(self._emit_ExprRange(r) for r in x.ranges)

    # ------------------------------------------------------------------ #
    # References / access                                                  #
    # ------------------------------------------------------------------ #

    def _emit_ExprAttribute(self, x) -> str:
        base = self.emit(x.value)
        attr = sv_ident(x.attr)
        if base in _SELF_TOKENS:
            return attr
        return f"{base}.{attr}"

    def _emit_ExprRefField(self, x) -> str:
        base = self.emit(x.base)
        name = (self._field_resolver(base, x.index)
                if self._field_resolver else f"field_{x.index}")
        if base in _SELF_TOKENS:
            return name
        return f"{base}.{name}"

    def _emit_ExprSubscript(self, x) -> str:
        val = self.emit(x.value)
        if isinstance(x.slice, e.ExprSlice):
            lo = self.emit(x.slice.lower) if x.slice.lower is not None else "0"
            hi = self.emit(x.slice.upper) if x.slice.upper is not None else ""
            return f"{val}[{hi}:{lo}]"
        return f"{val}[{self.emit(x.slice)}]"

    def _emit_ExprSlice(self, x) -> str:
        lo = self.emit(x.lower) if x.lower is not None else "0"
        hi = self.emit(x.upper) if x.upper is not None else ""
        return f"[{hi}:{lo}]"

    def _emit_ExprHierarchical(self, x) -> str:
        parts = []
        for elem in x.elements:
            part = elem.name
            if elem.subscript is not None:
                part += f"[{self.emit(elem.subscript)}]"
            parts.append(part)
        return ".".join(parts)

    # ------------------------------------------------------------------ #
    # Calls / casts / zdc builtins                                         #
    # ------------------------------------------------------------------ #

    def _emit_ExprCall(self, x) -> str:
        if self._call_hook is not None:
            hooked = self._call_hook(x)
            if hooked is not None:
                return hooked
        # Implication: ExprCall(func=ExprRefUnresolved('implies'), args=[c, b])
        if (isinstance(x.func, e.ExprRefUnresolved)
                and x.func.name == "implies" and len(x.args) == 2):
            return f"({self.emit(x.args[0])} -> {self.emit(x.args[1])})"
        func = self.emit(x.func)
        args = ", ".join(self.emit(a) for a in x.args)
        return f"{func}({args})"

    def _emit_ExprCast(self, x) -> str:
        tname = (self._type_namer(x.target_type)
                 if self._type_namer else str(getattr(x.target_type, "name", x.target_type)))
        return f"{tname}'({self.emit(x.value)})"

    def _emit_ExprAwait(self, x) -> str:
        return self.emit(x.value)

    def _emit_ExprSigned(self, x) -> str:
        return f"$signed({self.emit(x.value)})"

    def _emit_ExprCbit(self, x) -> str:
        return f"({self.emit(x.value)} ? 1'b1 : 1'b0)"

    def _emit_ExprZext(self, x) -> str:
        return f"{self.emit(x.value)}[{x.bits - 1}:0]"

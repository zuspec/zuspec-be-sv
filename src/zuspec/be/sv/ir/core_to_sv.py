"""Core IR -> SV IR translation (plan task C5, decision D1).

This is the mechanical projection of ``zuspec.ir.core`` data-model nodes onto
be-sv SV IR: a core ``DataType`` (struct/action class) with rand/non-rand fields
and structured ``ConstraintBlock`` constraints becomes an :class:`SVClass`.

PSS *semantics* (flow analysis, inference, scheduling, solve-groups) stay in the
frontend (pssc); this pass is intentionally a dumb translator. Frontend-specific
naming (mangling) is injected via the ``type_namer`` hook.

Procedural body translation (Function/Scenario -> SV tasks) is handled
separately by the ScenarioToSV pass (C5b); ``translate_class`` accepts already
-built ``tasks``/``functions`` so the two compose.
"""
from __future__ import annotations

from typing import Callable, List, Optional

from zuspec.ir.core import (
    DataType, DataTypeInt, DataTypeEnum, DataTypeString, DataTypeArray,
    DataTypeList, DataTypeRef, Field, FieldKind, RandKind,
)
from zuspec.ir.core import expr as _e
from .sv import SVClass, SVClassField, SVTaskDecl, SVFunctionDecl
from . import stmt as svs


_AUGOP = {
    _e.AugOp.Add: "+=", _e.AugOp.Sub: "-=", _e.AugOp.Mult: "*=",
    _e.AugOp.Div: "/=", _e.AugOp.Mod: "%=", _e.AugOp.LShift: "<<=",
    _e.AugOp.RShift: ">>=", _e.AugOp.BitAnd: "&=", _e.AugOp.BitOr: "|=",
    _e.AugOp.BitXor: "^=", _e.AugOp.FloorDiv: "/=",
}


def _ident(expr) -> str:
    """Best-effort identifier name for a loop/target expression."""
    if isinstance(expr, _e.ExprRefLocal):
        return expr.name
    if isinstance(expr, _e.ExprRefUnresolved):
        return expr.name
    if isinstance(expr, _e.ExprAttribute):
        return expr.attr
    return "i"


def translate_stmts(stmts) -> List[svs.SVStmt]:
    """Translate a list of core procedural ``Stmt`` nodes to ``SVStmt`` nodes.

    Covers the common procedural forms used in PSS exec/activity bodies. Unknown
    statement kinds raise ``NotImplementedError`` so gaps surface in tests.
    """
    from zuspec.ir.core import stmt as st
    out: List[svs.SVStmt] = []
    for s in stmts:
        if isinstance(s, st.StmtExpr):
            out.append(svs.SVStmtExpr(expr=s.expr))
        elif isinstance(s, st.StmtAssign):
            tgt = s.targets[0] if getattr(s, "targets", None) else None
            out.append(svs.SVStmtAssign(lhs=tgt, rhs=s.value))
        elif isinstance(s, st.StmtAugAssign):
            out.append(svs.SVStmtAssign(lhs=s.target, rhs=s.value,
                                        op=_AUGOP.get(s.op, "=")))
        elif isinstance(s, st.StmtReturn):
            out.append(svs.SVStmtReturn(value=s.value))
        elif isinstance(s, st.StmtIf):
            out.append(svs.SVStmtIf(
                cond=s.test,
                then_body=translate_stmts(s.body or []),
                else_body=translate_stmts(s.orelse or [])))
        elif isinstance(s, st.StmtWhile):
            out.append(svs.SVStmtWhile(cond=s.test, body=translate_stmts(s.body or [])))
        elif isinstance(s, st.StmtForeach):
            out.append(svs.SVStmtForeach(
                array=s.iter, index_var=_ident(s.target),
                body=translate_stmts(s.body or [])))
        elif isinstance(s, st.StmtRepeat):
            out.append(svs.SVStmtRepeat(count=s.count, body=translate_stmts(s.body or [])))
        elif isinstance(s, st.StmtBreak):
            out.append(svs.SVStmtRaw(text="break;"))
        elif isinstance(s, st.StmtContinue):
            out.append(svs.SVStmtRaw(text="continue;"))
        elif isinstance(s, st.StmtPass):
            continue
        else:
            raise NotImplementedError(
                f"translate_stmts: unsupported statement {type(s).__name__}")
    return out


def sv_type_str(dtype: DataType,
                type_namer: Optional[Callable[[DataType], str]] = None) -> str:
    """Map a core ``DataType`` to an SV type string for a class field.

    ``type_namer`` resolves named/ref/enum types to their SV class name
    (default: the datatype's ``name``).
    """
    def _name(dt):
        if type_namer is not None:
            return type_namer(dt)
        return getattr(dt, "name", None) or "int"

    if isinstance(dtype, DataTypeInt):
        if dtype.bits == 1:
            return "bit"
        if getattr(dtype, "signed", False):
            return "int" if dtype.bits == 32 else f"int signed [{dtype.bits - 1}:0]"
        # unspecified width defaults to a 32-bit vector
        bits = dtype.bits if dtype.bits and dtype.bits > 0 else 32
        return f"bit [{bits - 1}:0]"
    if isinstance(dtype, DataTypeEnum):
        return _name(dtype)
    if isinstance(dtype, DataTypeString):
        return "string"
    if isinstance(dtype, (DataTypeArray, DataTypeList)):
        elem = sv_type_str(dtype.element_type, type_namer) if dtype.element_type else "int"
        size = getattr(dtype, "size", -1)
        if isinstance(dtype, DataTypeArray) and size and size > 0:
            return f"{elem} [{size}]"
        return f"{elem} [$]"
    if isinstance(dtype, DataTypeRef):
        # Resolve through the name hook (maps to the mangled SV class name);
        # fall back to the raw ref_name when no hook is provided.
        if type_namer is not None:
            return type_namer(dtype)
        return getattr(dtype, "ref_name", None) or "int"
    # Fallback: a named class/handle
    return _name(dtype)


def _rand_flags(rand_kind) -> tuple:
    """Return ``(is_rand, is_randc)`` accepting either the ``RandKind`` enum or
    a string (the ast2ir frontend stores ``"rand"``/``"randc"`` strings)."""
    if rand_kind is None:
        return (False, False)
    if isinstance(rand_kind, str):
        k = rand_kind.lower()
        return (k == "rand", k == "randc")
    return (rand_kind == RandKind.RAND, rand_kind == RandKind.RANDC)


def translate_field(field: Field,
                    type_namer: Optional[Callable[[DataType], str]] = None
                    ) -> SVClassField:
    """Translate a core data ``Field`` to an :class:`SVClassField`."""
    from .expr_emit import sv_ident
    is_rand, is_randc = _rand_flags(field.rand_kind)
    return SVClassField(
        name=sv_ident(field.name),
        dtype=sv_type_str(field.datatype, type_namer),
        is_rand=is_rand,
        is_randc=is_randc,
    )


def translate_class(
    dtype: DataType,
    *,
    sv_name: Optional[str] = None,
    extends: Optional[str] = None,
    constraints: Optional[List] = None,
    tasks: Optional[List[SVTaskDecl]] = None,
    functions: Optional[List[SVFunctionDecl]] = None,
    type_namer: Optional[Callable[[DataType], str]] = None,
    include_kinds=(FieldKind.Field,),
) -> SVClass:
    """Translate a core struct/action ``DataType`` to an :class:`SVClass`.

    Args:
        dtype: Core ``DataTypeStruct``/``DataTypeAction``/``DataTypeClass``.
        sv_name: SV class name (default: ``dtype.name``).
        extends: Base class name.
        constraints: Structured ``ConstraintBlock`` list (placed verbatim into
            ``SVClass.constraints`` — emitted via ``SVConstraintEmitter``).
        tasks/functions: Pre-built SV-IR task/function decls (bodies).
        type_namer: Resolves named field types to SV class names.
        include_kinds: Field kinds to materialize as class fields. Defaults to
            plain data fields; flow/resource fields are handled by later passes.
    """
    fields: List[SVClassField] = []
    for f in getattr(dtype, "fields", []):
        if f.kind in include_kinds:
            fields.append(translate_field(f, type_namer))

    return SVClass(
        name=sv_name or dtype.name,
        extends_name=extends,
        fields=fields,
        constraints=list(constraints or []),
        tasks=list(tasks or []),
        functions=list(functions or []),
    )

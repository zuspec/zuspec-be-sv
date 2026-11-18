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

from typing import Callable, List, Optional, Tuple

from zuspec.ir.core import (
    DataType, DataTypeInt, DataTypeEnum, DataTypeString, DataTypeArray,
    DataTypeList, DataTypeRef, Field, FieldKind, RandKind,
    DataTypeParameterized, DataTypeSpecialized, DataTypeProtocol,
    DataTypeStruct, Function, SignalDirection,
    TemplateParamType, TemplateParamValue, TemplateParamEnum,
    TemplateArgType, TemplateArgValue, TemplateArgEnum,
)
from zuspec.ir.core import expr as _e
from .sv import (SVArg, SVClass, SVClassField, SVInterfaceClass, SVTaskDecl,
                 SVFunctionDecl, SVField, SVForwardDecl, SVPackage, SVRawItem,
                 SVTypedefEnum, SVTypedefStruct)
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


def _pattern_labels(pattern) -> List:
    """Reduce a core ``Pattern`` to a list of SV case-label exprs.

    An empty list denotes the ``default`` arm. ``PatternValue`` yields its value
    expr; ``PatternOr`` flattens its sub-patterns; ``PatternAs`` (wildcard /
    binding) and unknown patterns fall through to ``default``.
    """
    from zuspec.ir.core import stmt as st
    if isinstance(pattern, st.PatternValue):
        return [pattern.value]
    if isinstance(pattern, st.PatternOr):
        labels: List = []
        for p in pattern.patterns:
            labels.extend(_pattern_labels(p))
        return labels
    if isinstance(pattern, st.PatternAs) and pattern.pattern is not None:
        return _pattern_labels(pattern.pattern)
    return []  # default


_JOIN = {"all": "join", "any": "join_any", "none": "join_none"}


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
            nb = bool((getattr(s, "pragmas", None) or {}).get("nonblocking"))
            out.append(svs.SVStmtAssign(lhs=tgt, rhs=s.value, op="<=" if nb else "="))
        elif isinstance(s, st.StmtAugAssign):
            out.append(svs.SVStmtAssign(lhs=s.target, rhs=s.value,
                                        op=_AUGOP.get(s.op, "=")))
        elif isinstance(s, st.StmtAnnAssign) and s.ir_type is not None:
            dt, unp = sv_decl_type(s.ir_type)
            out.append(svs.SVVarDecl(name=_ident(s.target) + (f" {unp}" if unp else ""),
                                     dtype=dt, init=s.value))
        elif isinstance(s, st.StmtReturn):
            out.append(svs.SVStmtReturn(value=s.value))
        elif isinstance(s, st.StmtIf):
            out.append(svs.SVStmtIf(
                cond=s.test,
                then_body=translate_stmts(s.body or []),
                else_body=translate_stmts(s.orelse or [])))
        elif isinstance(s, st.StmtWhile):
            t = s.test
            if (isinstance(t, _e.ExprConstant) and not isinstance(t.value, str)
                    and t.value not in (None, 0, False)):
                out.append(svs.SVStmtForever(body=translate_stmts(s.body or [])))
            else:
                out.append(svs.SVStmtWhile(cond=s.test, body=translate_stmts(s.body or [])))
        elif isinstance(s, st.StmtForeach):
            out.append(svs.SVStmtForeach(
                array=s.iter, index_var=_ident(s.target),
                body=translate_stmts(s.body or [])))
        elif isinstance(s, st.StmtFor):
            # PSS/IR StmtFor projects onto a foreach over the iterable, matching
            # the legacy lower_stmts mapping.
            out.append(svs.SVStmtForeach(
                array=s.iter, index_var=_ident(s.target),
                body=translate_stmts(s.body or [])))
        elif isinstance(s, st.StmtRepeat):
            out.append(svs.SVStmtRepeat(count=s.count, body=translate_stmts(s.body or [])))
        elif isinstance(s, st.StmtRepeatWhile):
            out.append(svs.SVStmtDoWhile(
                cond=s.condition, body=translate_stmts(s.body or [])))
        elif isinstance(s, st.StmtMatch):
            items = []
            for case in s.cases:
                items.append(svs.SVCaseItem(
                    labels=_pattern_labels(case.pattern),
                    body=translate_stmts(case.body or [])))
            out.append(svs.SVStmtCase(subject=s.subject, items=items))
        elif isinstance(s, st.StmtAssert):
            out.append(svs.SVStmtAssert(cond=s.test, else_msg=s.msg))
        elif isinstance(s, st.StmtCover):
            out.append(svs.SVStmtCover(cond=s.test, msg=s.msg))
        elif isinstance(s, st.StmtRaise):
            # PSS raise -> $fatal(1, <exc>); default message when no exc.
            msg = s.exc if s.exc is not None else _e.ExprConstant(value="error")
            out.append(svs.SVStmtExpr(expr=_e.ExprCall(
                func=_e.ExprRefUnresolved(name="$fatal"),
                args=[_e.ExprConstant(value=1), msg])))
        elif isinstance(s, st.StmtYield):
            out.append(svs.SVStmtComment(text="yield (no-op in SV class execution)"))
        elif isinstance(s, st.StmtBreak):
            out.append(svs.SVStmtRaw(text="break;"))
        elif isinstance(s, st.StmtContinue):
            out.append(svs.SVStmtRaw(text="continue;"))
        elif isinstance(s, st.StmtPass):
            continue
        elif isinstance(s, st.StmtFork):
            out.append(svs.SVStmtFork(
                branches=[translate_stmts(b) for b in s.branches],
                join=_JOIN.get(s.join, s.join)))
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
        # A four-state type (SV ``logic``) keeps its states.
        kw = "logic" if getattr(dtype, "four_state", None) else "bit"
        if dtype.bits == 1:
            return kw
        if getattr(dtype, "signed", False):
            if kw == "logic":
                return f"logic signed [{dtype.bits - 1}:0]"
            return "int" if dtype.bits == 32 else f"int signed [{dtype.bits - 1}:0]"
        # A width over class parameters (``bit [W-1:0]``) wins over ``bits``
        # (which holds the width for the parameters' defaults).
        if getattr(dtype, "width_expr", None) is not None:
            return f"{kw} [{_width_msb(dtype.width_expr)}:0]"
        # unspecified width defaults to a 32-bit vector
        bits = dtype.bits if dtype.bits and dtype.bits > 0 else 32
        return f"{kw} [{bits - 1}:0]"
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
    if isinstance(dtype, DataTypeSpecialized):
        # ``gen_c #(s_t, 4)``: the template's name and its arguments, in order.
        base = dtype.base_template
        name = (type_namer(base) if type_namer is not None
                else getattr(base, "name", None) or dtype.name)
        args = [_template_arg_str(a, type_namer) for a in dtype.template_args]
        return f"{name} #({', '.join(args)})" if args else name
    if isinstance(dtype, DataTypeRef):
        # Resolve through the name hook (maps to the mangled SV class name);
        # fall back to the raw ref_name when no hook is provided.
        if type_namer is not None:
            return type_namer(dtype)
        return getattr(dtype, "ref_name", None) or "int"
    # Fallback: a named class/handle
    return _name(dtype)


def _expr_str(expr) -> str:
    from .expr_emit import SVExprEmitter
    return SVExprEmitter().emit(expr)


def _width_msb(width_expr) -> str:
    """The MSB for a width expression: ``W-1``, or ``(W*2)-1``."""
    w = _expr_str(width_expr)
    return f"{w}-1" if w.isidentifier() else f"({w})-1"


def _template_arg_str(arg, type_namer=None) -> str:
    if isinstance(arg, TemplateArgType):
        return sv_type_str(arg.type_value, type_namer)
    if isinstance(arg, TemplateArgValue):
        return _expr_str(arg.value_expr)
    if isinstance(arg, TemplateArgEnum):
        return arg.enum_value
    raise NotImplementedError(f"template arg {type(arg).__name__}")


def sv_decl_type(dtype: DataType,
                 type_namer: Optional[Callable[[DataType], str]] = None
                 ) -> Tuple[str, str]:
    """Split a declaration's type into ``(type, unpacked)``: the unpacked
    dimensions (``[$]``, ``[4]``) go after the declared name in SV."""
    if isinstance(dtype, (DataTypeArray, DataTypeList)):
        elem = (sv_decl_type(dtype.element_type, type_namer)
                if dtype.element_type else ("int", ""))
        size = getattr(dtype, "size", -1)
        dim = (f"[{size}]" if isinstance(dtype, DataTypeArray) and size
               and size > 0 else "[$]")
        return elem[0], f"{dim}{elem[1]}" if elem[1] else dim
    return sv_type_str(dtype, type_namer), ""


def sv_attributes(node) -> List[str]:
    """Render a node's ``annotations`` as SV attribute specs: ``fw_opt`` for a
    bare (value 1) one, ``fw_opt = 2`` / ``fw_opt = "x"`` otherwise."""
    out: List[str] = []
    for a in getattr(node, "annotations", None) or []:
        v = a.values.get("value", 1) if isinstance(a.values, dict) else 1
        if v is None or v is True or v == 1:
            out.append(a.type_qname)
        elif isinstance(v, str):
            out.append(f'{a.type_qname} = "{v}"')
        else:
            out.append(f"{a.type_qname} = {v}")
    return out


_DIR = {
    SignalDirection.INPUT: "input",
    SignalDirection.OUTPUT: "output",
    SignalDirection.INOUT: "inout",
}


def translate_param(tp, type_namer=None) -> str:
    """Render a ``TemplateParam`` as an SV class-parameter declaration."""
    if isinstance(tp, TemplateParamType):
        dflt = (f" = {sv_type_str(tp.default_value, type_namer)}"
                if tp.default_value is not None else "")
        return f"type {tp.name}{dflt}"
    if isinstance(tp, TemplateParamValue):
        dflt = (f" = {_expr_str(tp.default_value)}"
                if tp.default_value is not None else "")
        return f"{sv_type_str(tp.value_type, type_namer)} {tp.name}{dflt}"
    if isinstance(tp, TemplateParamEnum):
        dflt = f" = {tp.default_value}" if tp.default_value is not None else ""
        return f"{sv_type_str(tp.enum_type, type_namer)} {tp.name}{dflt}"
    raise NotImplementedError(f"template param {type(tp).__name__}")


def translate_method(fn: Function, *, pure: bool = False,
                     type_namer: Optional[Callable[[DataType], str]] = None):
    """Translate a core ``Function`` to an :class:`SVTaskDecl` (``is_async``
    or ``is_task``) or :class:`SVFunctionDecl`, typed and directed from its
    ``args``. ``pure`` gives a ``pure virtual`` prototype; otherwise the
    method is ``virtual`` with its body translated. A method with
    ``metadata["pure"]`` (in a virtual class) is ``pure virtual`` too."""
    pure = pure or bool((fn.metadata or {}).get("pure"))
    args: List[SVArg] = []
    fargs = fn.args.args if fn.args is not None else []
    # Defaults align with the trailing arguments, as in Python's ``ast``.
    dflts = list(fn.args.defaults or []) if fn.args is not None else []
    dflts = [None] * (len(fargs) - len(dflts)) + dflts
    for a, d in zip(fargs, dflts):
        dt, unp = (sv_decl_type(a.datatype, type_namer)
                   if a.datatype is not None else ("int", ""))
        args.append(SVArg(name=a.arg, dtype=dt, unpacked=unp,
                          direction=_DIR.get(a.direction, "input"), default=d))
    body = None if pure else translate_stmts(fn.body)
    attrs = sv_attributes(fn)
    if fn.is_async or fn.is_task:
        return SVTaskDecl(name=fn.name, args=args, is_virtual=True,
                          is_pure=pure, body=body, attributes=attrs)
    if fn.name == "new":
        # A constructor: neither virtual nor typed.
        return SVFunctionDecl(name="new", args=args, return_type="",
                              is_virtual=False, body=body, attributes=attrs)
    ret = (sv_type_str(fn.returns, type_namer)
           if fn.returns is not None else "void")
    # A static function (``metadata["static"]``) is not virtual.
    static = bool((fn.metadata or {}).get("static")) and not pure
    return SVFunctionDecl(name=fn.name, args=args, return_type=ret,
                          is_virtual=not static, is_pure=pure, is_static=static,
                          body=body, attributes=attrs)


def _split_methods(methods, **kw):
    tasks, funcs = [], []
    for fn in methods:
        d = translate_method(fn, **kw)
        (tasks if isinstance(d, SVTaskDecl) else funcs).append(d)
    return tasks, funcs


def translate_protocol(proto: DataTypeProtocol, *,
                       sv_name: Optional[str] = None,
                       type_namer: Optional[Callable[[DataType], str]] = None
                       ) -> SVInterfaceClass:
    """Translate a ``DataTypeProtocol`` to an ``interface class`` of
    ``pure virtual`` prototypes."""
    tasks, funcs = _split_methods(proto.methods, pure=True,
                                  type_namer=type_namer)
    return SVInterfaceClass(name=sv_name or proto.name, tasks=tasks,
                            functions=funcs)


def translate_decl(dtype: DataType, *,
                   type_namer: Optional[Callable[[DataType], str]] = None):
    """Translate a declared type, with everything taken from the IR: a
    ``DataTypeProtocol`` becomes an :class:`SVInterfaceClass` (with its
    parameters when it is a template's body); a ``DataTypeParameterized``
    (with a ``body``) or a class becomes an :class:`SVClass` with its
    parameters, ``extends`` (from ``super``), ``implements`` (names, or
    types such as a specialized API) and methods; an ``is_abstract`` class
    is a ``virtual class``."""
    if isinstance(dtype, DataTypeProtocol):
        return translate_protocol(dtype, type_namer=type_namer)
    params: List[str] = []
    name = dtype.name
    if isinstance(dtype, DataTypeParameterized):
        if dtype.body is None:
            raise ValueError(f"template {dtype.name} has no body")
        params = [translate_param(tp, type_namer)
                  for tp in dtype.template_params]
        dtype = dtype.body
        if isinstance(dtype, DataTypeProtocol):
            ic = translate_protocol(dtype, sv_name=name,
                                    type_namer=type_namer)
            ic.params = params
            return ic
    if not isinstance(dtype, DataTypeStruct):
        raise NotImplementedError(f"translate_decl: {type(dtype).__name__}")
    sup = dtype.super
    extends = sv_type_str(sup, type_namer) if sup is not None else None
    tasks, funcs = _split_methods(dtype.functions, type_namer=type_namer)
    return translate_class(
        dtype, sv_name=name, extends=extends, params=params,
        implements=[i if isinstance(i, str) else sv_type_str(i, type_namer)
                    for i in getattr(dtype, "implements", None) or []],
        tasks=tasks, functions=funcs, type_namer=type_namer)


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
    dt, unp = sv_decl_type(field.datatype, type_namer)
    return SVClassField(
        name=sv_ident(field.name),
        dtype=dt,
        unpacked=unp,
        is_rand=is_rand,
        is_randc=is_randc,
        attributes=sv_attributes(field),
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
    implements: Optional[List[str]] = None,
    params: Optional[List[str]] = None,
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
        implements: Interface-class names for the header.
        params: Rendered class parameters (see :func:`translate_param`).
    """
    fields: List[SVClassField] = []
    for f in getattr(dtype, "fields", []):
        if f.kind in include_kinds:
            fields.append(translate_field(f, type_namer))

    return SVClass(
        name=sv_name or dtype.name,
        extends_name=extends,
        implements=list(implements or []),
        params=list(params or []),
        fields=fields,
        constraints=list(constraints or []),
        tasks=list(tasks or []),
        functions=list(functions or []),
        is_virtual=bool(getattr(dtype, "is_abstract", False)),
    )


def translate_typedef(dtype: DataType, *, sv_name: Optional[str] = None,
                      type_namer: Optional[Callable[[DataType], str]] = None):
    """Translate a named type (a typedef) to its SV declaration: a packed
    ``DataTypeStruct`` to an :class:`SVTypedefStruct`, a ``DataTypeEnum`` to
    an :class:`SVTypedefEnum` (``bit`` based unless 4-state), a named
    ``DataTypeInt`` or ``DataTypeRef`` to a ``typedef`` line."""
    name = sv_name or dtype.name
    if isinstance(dtype, DataTypeStruct):
        return SVTypedefStruct(
            name=name, packed=bool(getattr(dtype, "packed", True)),
            fields=[SVField(name=f.name,
                            dtype=sv_type_str(f.datatype, type_namer))
                    for f in dtype.fields])
    if isinstance(dtype, DataTypeEnum):
        base = dtype.base_type
        return SVTypedefEnum(
            name=name, members=list(dtype.items.items()), width=dtype.width,
            base="logic" if getattr(base, "four_state", False) else "bit")
    if isinstance(dtype, DataTypeInt):
        return SVRawItem(lines=[f"typedef {sv_type_str(dtype)} {name};"])
    if isinstance(dtype, DataTypeRef):
        target = (type_namer(DataTypeRef(ref_name=dtype.ref_name))
                  if type_namer is not None else dtype.ref_name)
        return SVRawItem(lines=[f"typedef {target} {name};"])
    raise NotImplementedError(f"translate_typedef: {type(dtype).__name__}")


def _type_deps(dt, out: set) -> None:
    """Names of the declared types ``dt`` refers to."""
    if dt is None or isinstance(dt, str):
        if dt:
            out.add(dt)
        return
    if isinstance(dt, DataTypeRef):
        out.add(dt.ref_name)
    elif isinstance(dt, DataTypeSpecialized):
        base = dt.base_template
        out.add(getattr(base, "name", None) or dt.name)
        for a in dt.template_args:
            if isinstance(a, TemplateArgType):
                _type_deps(a.type_value, out)
    elif isinstance(dt, (DataTypeArray, DataTypeList)):
        _type_deps(dt.element_type, out)
    elif getattr(dt, "name", None):
        out.add(dt.name)


def _decl_deps(dtype) -> Tuple[set, set]:
    """``(hard, soft)`` dependencies of a class declaration: a base class or
    an implemented interface must be defined first (hard); a type used by a
    field, parameter or method needs at least a forward declaration (soft)."""
    hard, soft = set(), set()
    if isinstance(dtype, DataTypeParameterized):
        for tp in dtype.template_params:
            _type_deps(getattr(tp, "default_value", None)
                       if isinstance(tp, TemplateParamType)
                       else getattr(tp, "value_type", None), soft)
        dtype = dtype.body
    if dtype is None:
        return hard, soft
    _type_deps(getattr(dtype, "super", None), hard)
    for i in getattr(dtype, "implements", None) or []:
        _type_deps(i, hard)
    for f in getattr(dtype, "fields", None) or []:
        _type_deps(f.datatype, soft)
    for fn in (getattr(dtype, "functions", None)
               or getattr(dtype, "methods", None) or []):
        _type_deps(fn.returns, soft)
        for a in (fn.args.args if fn.args is not None else []):
            _type_deps(a.datatype, soft)
    return hard, soft


def translate_package(name: str, types, decls, *,
                      type_namer: Optional[Callable[[DataType], str]] = None,
                      imports=()) -> SVPackage:
    """Build a ``package`` of named types and class declarations.

    ``types`` maps each type's (qualified) name to its definition, in an
    order where a type follows the types it uses (``TypeMapper.types``
    order); ``decls`` are protocols, templates and classes in any order.
    Classes are ordered so a base class or implemented interface precedes
    its user; a class used before its definition gets a ``typedef class``.
    A ``name::`` prefix is dropped from type names (the default namer).
    ``imports`` names packages imported (``import x::*;``) by the package."""
    prefix = f"{name}::"

    def _local(n: str) -> str:
        return n[len(prefix):] if n.startswith(prefix) else n

    if type_namer is None:
        def type_namer(dt):
            return _local(getattr(dt, "ref_name", None)
                          or getattr(dt, "name", None) or "int")

    items: List = []
    for qn, t in (types.items() if isinstance(types, dict) else types):
        items.append(translate_typedef(t, sv_name=_local(qn),
                                       type_namer=type_namer))

    by_name = {_local(d.name): d for d in decls}
    deps = {n: tuple({_local(x) for x in s if _local(x) in by_name} - {n}
                     for s in _decl_deps(d))
            for n, d in by_name.items()}
    order: List[str] = []
    state: dict = {}
    forward: List[str] = []

    def _visit(n: str) -> None:
        if state.get(n) == "done":
            return
        if state.get(n) == "active":
            if n not in forward:
                forward.append(n)
            return
        state[n] = "active"
        hard, soft = deps[n]
        for m in sorted(hard) + sorted(soft - hard):
            _visit(m)
        state[n] = "done"
        order.append(n)

    for n in by_name:
        _visit(n)
    items.extend(SVForwardDecl(class_name=n) for n in forward)
    items.extend(translate_decl(by_name[n], type_namer=type_namer)
                 for n in order)
    return SVPackage(name=name, items=items, imports=list(imports))


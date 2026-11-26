"""SystemVerilog for parameterized synchronous components (fw-hdl step()/comb()).

A component is *parameterized sync* when it has ``template_params``, a
process with ``metadata["order"] == "blocking"``, or instantiates such a
component. :class:`SyncParamEmitter` writes:

- ``module name #(parameter type T = pkg::t, parameter int N = 4) (...)``;
  port and state types keep their symbolic widths (``DataTypeInt.width_expr``)
  and sizes (``DataTypeArray.size_expr``). An array *port* is packed
  (``input T [N-1:0] in_data``), an array of state unpacked (``T mem [DEPTH]``);
- a ``comb`` process as ``always_comb``; locals (``metadata["locals"]``) are
  declared at the top of the block and zeroed;
- a ``step()`` process with blocking (class) order as next-state logic:
  ``always_comb`` computes ``x_n`` for every register ``x`` (``x_n = x``
  first, then the body on the ``_n`` names), one ``always_ff`` registers
  ``x <= x_n``. An output with a reset-force ``F`` is the register ``x_q``
  with ``assign x = reset ? F : x_q``; reading it in step() gives
  ``reset ? F : x_n`` (``x_n``/``F`` where the path fixes reset). An array
  register written from one place in step() (not in a loop) has no ``_n``
  copy: the write is ``a_we``/``a_wa``/``a_wd`` and an enabled write in the
  ``always_ff``, a read of it in step() is ``(a_we && a_wa == i) ? a_wd : a[i]``
  (the value after the writes before it, in class order). Other arrays copy
  element by element (a whole-array copy turns into packed index arithmetic
  through sv2v);
- functions of the component as ``function automatic``; a call without
  arguments on current values reads ``<name>_v``, from
  ``always_comb <name>_v = <name>();`` (one value per cycle, and no call
  inside a conditional, where yosys infers latches for the function's
  locals); a function that
  reads registers, called from step() after a register may have been written
  on the path, gets a variant ``<name>_n`` reading the ``_n`` names;
- a composite's nets and ``module_instances`` with ``#(.P(arg))``.

Packed structs named ``<pkg>::<name>`` in the context become ``package <pkg>``
(:func:`packages`).
"""
from __future__ import annotations

from typing import Dict, List, Optional

import zuspec.ir.core as ir
import zuspec.ir.core.expr as ir_expr

_BIN = {ir.BinOp.Add: "+", ir.BinOp.Sub: "-", ir.BinOp.Mult: "*", ir.BinOp.Div: "/",
        ir.BinOp.Mod: "%", ir.BinOp.LShift: "<<", ir.BinOp.RShift: ">>",
        ir.BinOp.ARShift: ">>>",
        ir.BinOp.BitOr: "|", ir.BinOp.BitXor: "^", ir.BinOp.BitAnd: "&",
        ir.BinOp.CaseEq: "===", ir.BinOp.CaseNotEq: "!=="}
_CMP = {ir.CmpOp.Eq: "==", ir.CmpOp.NotEq: "!=", ir.CmpOp.Lt: "<", ir.CmpOp.LtE: "<=",
        ir.CmpOp.Gt: ">", ir.CmpOp.GtE: ">=",
        ir.CmpOp.CaseEq: "===", ir.CmpOp.CaseNotEq: "!=="}
_UN = {ir.UnaryOp.Not: "!", ir.UnaryOp.Invert: "~", ir.UnaryOp.USub: "-",
       ir.UnaryOp.UAdd: "+"}
_SYS = {"clog2": "$clog2", "bits": "$bits"}
_IFEXP = (ir.ExprIfExp, ir_expr.ExprIfExp)


def is_sync_param(comp: ir.DataTypeComponent, type_m: Dict[str, ir.DataType]) -> bool:
    if getattr(comp, "template_params", None):
        return True
    if any(fn.metadata.get("order") == "blocking"
           for fn in comp.sync_processes + comp.comb_processes):
        return True
    return any(isinstance(type_m.get(mi.module), ir.DataTypeComponent)
               and is_sync_param(type_m[mi.module], type_m)
               for mi in comp.module_instances)


def packages(type_m: Dict[str, ir.DataType]) -> Dict[str, str]:
    """``{package: text}`` for the packed structs named ``<pkg>::<name>``."""
    by_pkg: Dict[str, List[str]] = {}
    for t in type_m.values():
        if isinstance(t, ir.DataTypeStruct) and not isinstance(t, ir.DataTypeClass) \
                and t.name and "::" in t.name:
            pkg, name = t.name.rsplit("::", 1)
            lines = [f"  typedef struct packed {{"]
            for f in t.fields:
                lines.append(f"    {_decl(f.datatype, f.name)};")
            lines.append(f"  }} {name};")
            by_pkg.setdefault(pkg, []).extend(lines)
    return {pkg: f"package {pkg};\n" + "\n".join(lines) + "\nendpackage\n"
            for pkg, lines in by_pkg.items()}


def _cv(e) -> Optional[int]:
    return e.value if isinstance(e, ir.ExprConstant) and isinstance(e.value, int) else None


def _width(dt: ir.DataTypeInt) -> str:
    """``[w-1:0]`` of an integral type (empty for one bit)."""
    w = getattr(dt, "width_expr", None)
    if w is not None:
        return f"[{_cexpr(w, wrap=True)}-1:0]"
    if dt.bits in (1, -1):
        return ""
    return f"[{dt.bits - 1}:0]"


def _base(dt: ir.DataType) -> str:
    if isinstance(dt, ir.DataTypeRef):
        return dt.ref_name
    if isinstance(dt, ir.DataTypeInt):
        if dt.bits == 32 and dt.signed and getattr(dt, "width_expr", None) is None \
                and getattr(dt, "four_state", None) is not True:
            return "int"
        w = _width(dt)
        return "logic" + (" signed" if dt.signed else "") + (f" {w}" if w else "")
    raise ValueError(f"unsupported type {dt}")


def _size(dt: ir.DataTypeArray) -> str:
    return _cexpr(dt.size_expr) if getattr(dt, "size_expr", None) is not None \
        else str(dt.size)


def _decl(dt: ir.DataType, name: str, packed: bool = False) -> str:
    """``<type> name``; an array is packed (``T [N-1:0] name``) or unpacked
    (``T name [N]``)."""
    if isinstance(dt, ir.DataTypeArray):
        et = dt.element_type
        if packed:
            n = _size(dt)
            if isinstance(et, ir.DataTypeInt) and _width(et) == "" \
                    and not (et.bits == 32 and et.signed):
                return f"logic [{_wrap(n)}-1:0] {name}"
            return f"{_base(et)} [{_wrap(n)}-1:0] {name}"
        return f"{_base(et)} {name} [{_size(dt)}]"
    return f"{_base(dt)} {name}"


def _wrap(s: str) -> str:
    return s if s.replace("_", "").isalnum() or s.startswith("$") and s.endswith(")") \
        else f"({s})"


def _cexpr(e, wrap: bool = False) -> str:
    """A constant (parameter) expression: over template parameters
    (``ExprRefParam``) and genvars (``ExprRefLocal``). ``wrap``
    parenthesizes a compound expression (an operand of another)."""
    if isinstance(e, ir.ExprConstant):
        v = e.value
        return str(int(v)) if isinstance(v, bool) else str(v)
    if isinstance(e, (ir.ExprRefParam, ir.ExprRefLocal)):
        return e.name
    if isinstance(e, ir.ExprCall) and isinstance(e.func, ir.ExprRefUnresolved):
        return f"{_SYS.get(e.func.name, e.func.name)}(" \
            + ", ".join(_cexpr(a) for a in e.args) + ")"
    if isinstance(e, ir.ExprBin):
        s = f"{_cexpr(e.lhs, True)}{_BIN[e.op]}{_cexpr(e.rhs, True)}"
    elif isinstance(e, ir.ExprCompare):
        s = f"{_cexpr(e.left, True)} {_CMP[e.ops[0]]} {_cexpr(e.comparators[0], True)}"
    elif isinstance(e, ir.ExprBool):
        s = (" && " if e.op == ir.BoolOp.And else " || ").join(_cexpr(v, True) for v in e.values)
    elif isinstance(e, ir.ExprUnary):
        return f"{_UN[e.op]}{_cexpr(e.operand, True)}"
    elif isinstance(e, _IFEXP):
        s = f"{_cexpr(e.test, True)} ? {_cexpr(e.body, True)} : {_cexpr(e.orelse, True)}"
    else:
        raise ValueError(f"unsupported constant expression {e}")
    return f"({s})" if wrap else s


def _fresh(name: str, used: set) -> str:
    n, i = name, 0
    while n in used:
        i += 1
        n = f"{name}_{i}"
    return n


def _may_return(body: list) -> bool:
    for s in body:
        if isinstance(s, ir.StmtReturn):
            return True
        if isinstance(s, ir.StmtIf) and (_may_return(s.body) or _may_return(s.orelse)):
            return True
        if isinstance(s, ir.StmtFor) and _may_return(s.body):
            return True
    return False


def _return_in_loop(body: list) -> bool:
    for s in body:
        if isinstance(s, ir.StmtFor) and _may_return(s.body):
            return True
        if isinstance(s, ir.StmtIf) and (_return_in_loop(s.body)
                                         or _return_in_loop(s.orelse)):
            return True
    return False


def _flag_returns(body: list, ret: str, done: str) -> list:
    """``body`` with ``return v`` as ``ret = v; done = 1`` and what follows a
    possible return guarded by ``!done``. A loop stays unguarded (its body is
    guarded), so its variable is assigned on every path."""
    not_done = ir.ExprUnary(op=ir.UnaryOp.Not, operand=ir.ExprRefLocal(name=done))
    out: list = []
    run: list = []                  # guarded statements not yet emitted

    def flush():
        if run:
            out.append(ir.StmtIf(test=not_done, body=list(run)))
            run.clear()
    guard = False
    for s in body:
        if isinstance(s, ir.StmtReturn):
            s2 = [ir.StmtAssign(targets=[ir.ExprRefLocal(name=ret)], value=s.value),
                  ir.StmtAssign(targets=[ir.ExprRefLocal(name=done)],
                                value=ir.ExprConstant(value=1))]
        elif isinstance(s, ir.StmtIf):
            s2 = [ir.StmtIf(test=s.test, body=_flag_returns(s.body, ret, done),
                            orelse=_flag_returns(s.orelse, ret, done))]
        elif isinstance(s, ir.StmtFor):
            b = _flag_returns(s.body, ret, done)
            if guard or _may_return(s.body):
                b = [ir.StmtIf(test=not_done, body=b)]
            flush()
            out.append(ir.StmtFor(target=s.target, iter=s.iter, body=b))
            guard = guard or _may_return(s.body)
            continue
        else:
            s2 = [s]
        if guard:
            run.extend(s2)
        else:
            out.extend(s2)
        if isinstance(s, ir.StmtReturn):
            break                       # the rest is not reached
        guard = guard or _may_return([s])
    flush()
    return out


def _type_text(t) -> str:
    """A type parameter's value."""
    return _base(t)


class SyncParamEmitter:

    def __init__(self, comp: ir.DataTypeComponent, type_m: Dict[str, ir.DataType]):
        self.comp, self.type_m = comp, type_m
        self.fields = comp.fields
        self.fns = {fn.name: fn for fn in comp.functions}
        self.lines: List[str] = []
        self.reset = self.clock = None
        for fn in comp.sync_processes:
            self.clock = self._self(fn.metadata.get("clock"))
            self.reset = self._self(fn.metadata.get("reset"))
        # Registers: state and step-driven outputs.
        self.regs = [i for i, f in enumerate(self.fields)
                     if f.drive == ir.ProcessKind.SYNC]
        self.forced = {i for i in self.regs if isinstance(self.fields[i], ir.FieldInOut)
                       and self.fields[i].reset_force is not None}
        self.variants: Dict[str, bool] = {}     # function name -> step variant used
        self.values: set = set()                # no-argument functions read as <name>_v
        # Array registers with one write site in step(): enabled writes.
        sites: Dict[int, int] = {}
        for fn in comp.sync_processes:
            self._array_writes(fn.body, sites, False)
        self.we = {i for i, n in sites.items() if n == 1}

    def _array_writes(self, body: list, sites: Dict[int, int], loop: bool):
        """Counts the write sites of each array register (a write in a loop,
        or of a part of an element, counts as many)."""
        for s in body:
            if isinstance(s, ir.StmtAssign):
                t = s.targets[0]
                simple = isinstance(t, ir.ExprSubscript) and self._self(t.value) is not None
                while isinstance(t, (ir.ExprSubscript, ir.ExprAttribute)):
                    t = t.value
                i = self._self(t)
                if i is not None and isinstance(self.fields[i].datatype, ir.DataTypeArray):
                    sites[i] = sites.get(i, 0) + (1 if simple and not loop else 2)
            elif isinstance(s, ir.StmtIf):
                self._array_writes(s.body, sites, loop)
                self._array_writes(s.orelse, sites, loop)
            elif isinstance(s, ir.StmtFor):
                self._array_writes(s.body, sites, True)

    @staticmethod
    def _self(e) -> Optional[int]:
        if isinstance(e, ir.ExprRefField) and isinstance(e.base, ir.TypeExprRefSelf):
            return e.index
        return None

    # -- names --------------------------------------------------------------

    def reg_name(self, i: int) -> str:
        """The register variable of field ``i``."""
        f = self.fields[i]
        return f"{f.name}_q" if i in self.forced else f.name

    def read(self, i: int, ctx: dict) -> str:
        f = self.fields[i]
        if ctx["step"] and i in self.regs:
            if i in self.forced:
                r = ctx["reset"]
                if r is False:
                    return f"{f.name}_n"
                force = self._force(i)
                if r is True:
                    return force
                return f"({self.fields[self.reset].name} ? {force} : {f.name}_n)"
            return f"{f.name}_n"
        return f.name

    def write(self, i: int, ctx: dict) -> str:
        f = self.fields[i]
        return f"{f.name}_n" if ctx["step"] else f.name

    def _force(self, i: int) -> str:
        v = int(self.fields[i].reset_force)
        return "'0" if v == 0 else str(v)

    # -- expressions --------------------------------------------------------

    def expr(self, e, ctx: dict, top: bool = True) -> str:
        s = self._expr(e, ctx)
        if not top and isinstance(e, (ir.ExprBin, ir.ExprBool, ir.ExprCompare) + _IFEXP):
            return f"({s})"
        return s

    def _expr(self, e, ctx: dict) -> str:
        if isinstance(e, ir.ExprConstant):
            v = e.value
            return str(int(v)) if isinstance(v, bool) else str(v)
        if isinstance(e, ir.ExprRefField):
            i = self._self(e)
            if i is None:
                raise ValueError(f"unsupported field reference {e}")
            if ctx["step"] and i in self.we:
                raise ValueError(f"'{self.fields[i].name}' is read whole in step()")
            return self.read(i, ctx)
        if isinstance(e, (ir.ExprRefLocal, ir.ExprRefParam)):
            return e.name
        if isinstance(e, ir.ExprBin):
            return f"{self.expr(e.lhs, ctx, False)} {_BIN[e.op]} {self.expr(e.rhs, ctx, False)}"
        if isinstance(e, ir.ExprBool):
            op = " && " if e.op == ir.BoolOp.And else " || "
            return op.join(self.expr(v, ctx, False) for v in e.values)
        if isinstance(e, ir.ExprCompare):
            return f"{self.expr(e.left, ctx, False)} {_CMP[e.ops[0]]} " \
                   f"{self.expr(e.comparators[0], ctx, False)}"
        if isinstance(e, ir.ExprUnary):
            return f"{_UN[e.op]}{self.expr(e.operand, ctx, False)}"
        if isinstance(e, _IFEXP):
            return f"{self.expr(e.test, ctx, False)} ? {self.expr(e.body, ctx, False)} : " \
                   f"{self.expr(e.orelse, ctx, False)}"
        if isinstance(e, ir.ExprSubscript):
            a = self._self(e.value)
            if ctx["step"] and a in self.we:
                n = self.fields[a].name
                x = self.expr(e.slice, ctx, False)
                return f"({n}_we && {n}_wa == {x}) ? {n}_wd : {n}[{self.expr(e.slice, ctx)}]"
            return f"{self.expr(e.value, ctx, False)}[{self.expr(e.slice, ctx)}]"
        if isinstance(e, ir.ExprAttribute):
            return f"{self.expr(e.value, ctx, False)}.{e.attr}"
        if isinstance(e, ir.ExprCast):
            return f"{_base(e.target_type)}'({self.expr(e.value, ctx)})"
        if isinstance(e, ir.ExprCall):
            args = ", ".join(self.expr(a, ctx) for a in e.args)
            if isinstance(e.func, ir.ExprRefUnresolved):
                return f"{_SYS.get(e.func.name, e.func.name)}({args})"
            if isinstance(e.func, ir.ExprAttribute) \
                    and isinstance(e.func.value, ir.TypeExprRefSelf):
                name = e.func.attr
                if ctx["step"] and ctx["dirty"] and self._reads_regs(name, set()):
                    self.variants[name] = True
                    name = f"{name}_n"
                else:
                    self.variants.setdefault(name, False)
                    if not e.args:
                        # One value per cycle: computed once, read where called.
                        self.values.add(name)
                        return f"{name}_v"
                return f"{name}({args})"
        raise ValueError(f"unsupported expression {type(e).__name__}")

    def _reads_regs(self, name: str, seen: set) -> bool:
        """Whether function ``name`` (or one it calls) reads a register."""
        if name in seen:
            return False
        seen.add(name)
        found = []

        def v(x):
            if isinstance(x, ir.ExprRefField) and self._self(x) in self.regs:
                found.append(x)
            elif isinstance(x, ir.ExprCall) and isinstance(x.func, ir.ExprAttribute) \
                    and isinstance(x.func.value, ir.TypeExprRefSelf):
                if self._reads_regs(x.func.attr, seen):
                    found.append(x)
            if hasattr(x, "__dataclass_fields__"):
                for k in x.__dataclass_fields__:
                    y = getattr(x, k)
                    for z in (y if isinstance(y, list) else [y]):
                        if isinstance(z, (ir.Expr, ir.Stmt)):
                            v(z)
        for s in self.fns[name].body:
            v(s)
        return bool(found)

    # -- statements ---------------------------------------------------------

    def _writes_regs(self, body: list) -> bool:
        for s in body:
            if isinstance(s, ir.StmtAssign):
                t = s.targets[0]
                while isinstance(t, (ir.ExprSubscript, ir.ExprAttribute)):
                    t = t.value
                if self._self(t) in self.regs:
                    return True
            elif isinstance(s, ir.StmtIf) and (self._writes_regs(s.body)
                                               or self._writes_regs(s.orelse)):
                return True
            elif isinstance(s, ir.StmtFor) and self._writes_regs(s.body):
                return True
        return False

    def target(self, t, ctx: dict) -> str:
        if isinstance(t, ir.ExprRefField):
            return self.write(self._self(t), ctx)
        if isinstance(t, ir.ExprRefLocal):
            return t.name
        if isinstance(t, ir.ExprSubscript):
            return f"{self.target(t.value, ctx)}[{self.expr(t.slice, ctx)}]"
        if isinstance(t, ir.ExprAttribute):
            return f"{self.target(t.value, ctx)}.{t.attr}"
        raise ValueError(f"unsupported target {t}")

    def stmts(self, body: list, ctx: dict, ind: int) -> List[str]:
        out: List[str] = []
        p = "  " * ind
        for s in body:
            t = s.targets[0] if isinstance(s, ir.StmtAssign) else None
            if t is not None and ctx["step"] and isinstance(t, ir.ExprSubscript) \
                    and self._self(t.value) in self.we:
                n = self.fields[self._self(t.value)].name
                out.append(f"{p}{n}_we = 1'b1;")
                out.append(f"{p}{n}_wa = {self.expr(t.slice, ctx)};")
                out.append(f"{p}{n}_wd = {self.expr(s.value, ctx)};")
                ctx["dirty"] = True
            elif isinstance(s, ir.StmtAssign):
                out.append(f"{p}{self.target(s.targets[0], ctx)} = {self.expr(s.value, ctx)};")
                ctx["dirty"] = ctx["dirty"] or self._writes_regs([s])
            elif isinstance(s, ir.StmtIf):
                then, other = dict(ctx), dict(ctx)
                r = self._self(s.test)
                if r is not None and r == self.reset:
                    then["reset"], other["reset"] = True, False
                out.append(f"{p}if ({self.expr(s.test, ctx)}) begin")
                out.extend(self.stmts(s.body, then, ind + 1))
                if s.orelse:
                    if len(s.orelse) == 1 and isinstance(s.orelse[0], ir.StmtIf):
                        nested = self.stmts(s.orelse, other, ind)
                        out.append(f"{p}end else {nested[0].lstrip()}")
                        out.extend(nested[1:])
                        ctx["dirty"] = then["dirty"] or other["dirty"]
                        continue
                    out.append(f"{p}end else begin")
                    out.extend(self.stmts(s.orelse, other, ind + 1))
                out.append(f"{p}end")
                ctx["dirty"] = then["dirty"] or other["dirty"]
            elif isinstance(s, ir.StmtFor):
                lo, hi = s.iter.args
                k = s.target.name
                ctx["dirty"] = ctx["dirty"] or self._writes_regs(s.body)
                out.append(f"{p}for (int {k} = {self.expr(lo, ctx)}; {k} < "
                           f"{self.expr(hi, ctx)}; {k}++) begin")
                out.extend(self.stmts(s.body, ctx, ind + 1))
                out.append(f"{p}end")
            elif isinstance(s, ir.StmtReturn):
                out.append(f"{p}return" + (f" {self.expr(s.value, ctx)};"
                                           if s.value is not None else ";"))
            else:
                raise ValueError(f"unsupported statement {type(s).__name__}")
        return out

    def locals_(self, fn: ir.Function, ind: int, zero: bool) -> List[str]:
        p = "  " * ind
        out = [f"{p}{_decl(t, n)};" for n, t in fn.metadata.get("locals", [])]
        if zero:
            out += [f"{p}{n} = '0;" for n, _ in fn.metadata.get("locals", [])]
        return out

    # -- module -------------------------------------------------------------

    def header(self) -> List[str]:
        c = self.comp
        lines = []
        tps = getattr(c, "template_params", [])
        if tps:
            lines.append(f"module {c.name} #(")
            ps = []
            for tp in tps:
                if isinstance(tp, ir.TemplateParamType):
                    d = f" = {_type_text(tp.default_value)}" if tp.default_value else ""
                    ps.append(f"  parameter type {tp.name}{d}")
                else:
                    d = f" = {_cexpr(tp.default_value)}" if tp.default_value is not None \
                        else ""
                    ps.append(f"  parameter {_base(tp.value_type)} {tp.name}{d}")
            lines.append(",\n".join(ps))
            lines.append(") (")
        else:
            lines.append(f"module {c.name} (")
        ports = [f"  {'output' if f.is_out else 'input '} {_decl(f.datatype, f.name, True)}"
                 for f in c.fields if isinstance(f, ir.FieldInOut)]
        lines.append(",\n".join(ports))
        lines.append(");")
        return lines

    def module(self) -> str:
        c = self.comp
        L = self.header()
        body: List[str] = []
        regs = self.regs
        if c.module_instances:
            return "\n".join(L + self.composite()) + "\n"
        # Registers and next state.
        decl = []
        for i in regs:
            f = self.fields[i]
            if not isinstance(f, ir.FieldInOut):
                decl.append(f"  {_decl(f.datatype, f.name)};")
            elif i in self.forced:
                decl.append(f"  {_decl(f.datatype, f.name + '_q')};")
        if c.sync_processes:
            for i in regs:
                f = self.fields[i]
                if i in self.we:
                    decl += [f"  logic {f.name}_we;", f"  int {f.name}_wa;",
                             f"  {_decl(f.datatype.element_type, f.name + '_wd')};"]
                else:
                    decl.append(f"  {_decl(f.datatype, f.name + '_n')};")
        if decl:
            body.append("")
            body.extend(decl)
        n_decl = len(body)
        for fn in c.comb_processes:
            ctx = {"step": False, "reset": None, "dirty": False}
            stmts = self.stmts(fn.body, ctx, 2)
            body.append("")
            body.append(f"  // [{fn.name}]")
            body.append(f"  always_comb begin : {fn.name}_p")
            body.extend(self.locals_(fn, 2, True))
            body.extend(stmts)
            body.append("  end")
        for fn in c.sync_processes:
            ctx = {"step": True, "reset": None, "dirty": False}
            stmts = self.stmts(fn.body, ctx, 2)
            body.append("")
            body.append(f"  // [{fn.name}] next state, in the class's (blocking) order")
            body.append(f"  always_comb begin : {fn.name}_p")
            body.extend(self.locals_(fn, 2, False))
            for i in regs:
                body.extend(self._init_next(i))
            body.extend(f"    {n} = '0;" for n, _ in fn.metadata.get("locals", []))
            body.extend(stmts)
            body.append("  end")
            body.append("")
            body.append(f"  always_ff @(posedge {self.fields[self.clock].name}) begin")
            for i in regs:
                body.extend(self._register(i))
            body.append("  end")
        if self.forced:
            body.append("")
            rst = self.fields[self.reset].name
            for i in sorted(self.forced):
                f = self.fields[i]
                body.append(f"  assign {f.name} = {rst} ? {self._force(i)} : {f.name}_q;")
        # Functions (after the processes: they say which variants are used).
        fns: List[str] = []
        done = set()
        while set(self.variants) - done:
            name = sorted(set(self.variants) - done)[0]
            done.add(name)
            fns += self.function(self.fns[name], step=False)
            if self.variants[name]:
                fns += self.function(self.fns[name], step=True)
        for name in sorted(self.values):
            fns += ["", f"  {_decl(self.fns[name].returns, name + '_v')};",
                    f"  always_comb {name}_v = {name}();"]
        if fns:
            body = body[:n_decl] + fns + body[n_decl:]
        return "\n".join(L + body + ["endmodule"]) + "\n"

    def _init_next(self, i: int) -> List[str]:
        """The start of next-state: ``x_n = x``."""
        f = self.fields[i]
        if i in self.we:
            return [f"    {f.name}_we = 1'b0;", f"    {f.name}_wa = 0;",
                    f"    {f.name}_wd = '0;"]
        if isinstance(f.datatype, ir.DataTypeArray):
            return [f"    for (int j = 0; j < {_size(f.datatype)}; j++) "
                    f"{f.name}_n[j] = {f.name}[j];"]
        return [f"    {f.name}_n = {self.reg_name(i)};"]

    def _register(self, i: int) -> List[str]:
        f = self.fields[i]
        if i in self.we:
            return [f"    if ({f.name}_we) {f.name}[{f.name}_wa] <= {f.name}_wd;"]
        if isinstance(f.datatype, ir.DataTypeArray):
            return [f"    for (int j = 0; j < {_size(f.datatype)}; j++) "
                    f"{f.name}[j] <= {f.name}_n[j];"]
        return [f"    {self.reg_name(i)} <= {f.name}_n;"]

    def function(self, fn: ir.Function, step: bool) -> List[str]:
        ctx = {"step": step, "reset": None, "dirty": step}
        args = ", ".join(f"input {_decl(a.datatype, a.arg)}" for a in fn.args.args) \
            if fn.args is not None else ""
        name = f"{fn.name}_n" if step else fn.name
        body = fn.body
        decls: List[str] = []
        inits: List[str] = []
        if _return_in_loop(body):
            # A return inside a loop: a result and a done flag instead (sv2v's
            # conversion of such a return leaves latches for yosys).
            used = {n for n, _ in fn.metadata.get("locals", [])}
            ret, done = _fresh("ret", used), _fresh("done", used)
            body = _flag_returns(body, ret, done) + [
                ir.StmtReturn(value=ir.ExprRefLocal(name=ret))]
            decls = [f"    {_decl(fn.returns, ret)};", f"    logic {done};"]
            inits = [f"    {ret} = '0;", f"    {done} = 1'b0;"]
        stmts = self.stmts(body, ctx, 2)
        out = ["", f"  // {fn.name}()" + (" on the next-state values (called from step())"
                                           if step else ""),
               f"  function automatic {_base(fn.returns)} {name}({args});"]
        # Locals zeroed, as the class's are (and no latch where a function is
        # inlined into an always_comb).
        out += self.locals_(fn, 2, False) + decls
        out += [f"    {n} = '0;" for n, _ in fn.metadata.get("locals", [])] + inits
        out += stmts
        out.append("  endfunction")
        return out

    def composite(self) -> List[str]:
        c = self.comp
        out = [""]
        # Wires between the children's ports, which are packed.
        out += [f"  {_decl(f.datatype, f.name, True)};" for f in c.fields
                if not isinstance(f, ir.FieldInOut)]
        for mi in c.module_instances:
            params = ""
            if mi.parameters:
                ps = []
                for k, v in mi.parameters.items():
                    if isinstance(v, str):
                        ps.append(f".{k}({v})")
                    elif isinstance(v, ir.DataType):
                        ps.append(f".{k}({_type_text(v)})")
                    else:
                        ps.append(f".{k}({_cexpr(v)})")
                params = " #(" + ", ".join(ps) + ")"
            out.append("")
            out.append(f"  {mi.module}{params} {mi.name} (")
            out.append(",\n".join(f"    .{pc.port}({pc.signal})" for pc in mi.connections))
            out.append("  );")
        out.append("endmodule")
        return out

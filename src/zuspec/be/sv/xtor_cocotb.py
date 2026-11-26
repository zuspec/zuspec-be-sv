"""A transactor's cocotb bridge from its lowered core (fw-hdl
xtor-shell-plan.md T7, D9).

The cocotb counterpart of the SV interface and bridge (``xtor_shell``,
``xtor_bridge``): one Python module per transactor, generated from the
core's IR alone, with no SV interface or shell. The bench puts the core
itself in its HDL top; the bridge is built with the core's handle and
its clock, and drives and samples the core's crossing ports:

- the task side of each crossing (``sync_xings``), with the semantics of
  the SV interface (processes.md X3-X8): one slot per crossing, a
  ``put`` coroutine for a crossing to the core and a ``get`` coroutine
  for one from it;
- the API boundary (``api_endpoints``), as in the plain SV flavor (D10):
  an export makes the bridge implement that API, a port is a handle to
  the API given by ``set_<port>(imp)``, and ``run()`` is a target's task
  code (an initiator's returns at once);
- the task code (``task_blocks``), lowered to Python: crossing calls
  become the bridge's put/get coroutines, port calls ``await`` the
  implementation's methods.

The API is a second module, named after the API's scope (its SV package,
``wb_api_pkg.py``): the API as a ``typing.Protocol`` and its types as
dataclasses. In Python, a method's outputs are its return value (several
as a tuple) and its inputs its arguments.

Timing. The bridge keeps the SV interface's state in Python and copies
its code: its writes are deferred (``<=``) and land in cocotb's
ReadWrite phase of the time step, and it reads the core's outputs from a
sample taken in the ReadOnly phase before each edge. Code that resumes
at a rising edge (the bridge's, and any caller's) so sees the values the
edge sampled, whatever order cocotb runs it in. Calls are made at an
edge, or before the first.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import zuspec.ir.core as ir

from .xtor_bridge import XtorIr

__all__ = ["XtorCocotbEmitter"]

_IN, _OUT = ir.SignalDirection.INPUT, ir.SignalDirection.OUTPUT

_BIN = {ir.BinOp.Add: "+", ir.BinOp.Sub: "-", ir.BinOp.Mult: "*",
        ir.BinOp.Div: "//", ir.BinOp.FloorDiv: "//", ir.BinOp.Mod: "%",
        ir.BinOp.BitAnd: "&", ir.BinOp.BitOr: "|", ir.BinOp.BitXor: "^",
        ir.BinOp.LShift: "<<", ir.BinOp.RShift: ">>",
        ir.BinOp.Eq: "==", ir.BinOp.NotEq: "!=", ir.BinOp.CaseEq: "==",
        ir.BinOp.CaseNotEq: "!=", ir.BinOp.Lt: "<", ir.BinOp.LtE: "<=",
        ir.BinOp.Gt: ">", ir.BinOp.GtE: ">=", ir.BinOp.And: "and", ir.BinOp.Or: "or"}
_CMP = {ir.CmpOp.Eq: "==", ir.CmpOp.NotEq: "!=", ir.CmpOp.Lt: "<", ir.CmpOp.LtE: "<=",
        ir.CmpOp.Gt: ">", ir.CmpOp.GtE: ">="}


def _short(n: str) -> str:
    return n.split("::")[-1]


def _at(port: str) -> str:
    return f'self._at("{port}")'


class XtorCocotbEmitter:
    """The cocotb bridge ``name`` of the core ``core`` (a lowered, flat
    component with ``task_blocks``). ``type_m`` has the APIs and the
    crossings' item types. ``wait`` is how the task side waits: ``event``
    (on ready/valid changes) or ``edge`` (a sample at every edge)."""

    def __init__(self, core: ir.DataTypeComponent, name: str,
                 type_m: Dict[str, ir.DataType], wait: str = "event"):
        if wait not in ("event", "edge"):
            raise ValueError(f"wait '{wait}' (event or edge)")
        self.wait = wait
        self.ir = XtorIr(core, type_m)
        self.core = core
        self.name = name
        self.type_m = type_m
        for n, x in self.ir.xings.items():
            if x.depth != 1:
                raise NotImplementedError(f"crossing '{n}': depth {x.depth} (1 only)")
        # the APIs' modules and types: module -> {name: struct}, in order
        self.api_mods: Dict[str, Dict[str, ir.DataTypeStruct]] = {}
        self.api_of: Dict[str, str] = {}        # API name -> module
        for n, e in self.ir.eps.items():
            mod = e.api_scope or f"{e.api}_api"
            self.api_of[e.api] = mod
            tds = self.api_mods.setdefault(mod, {})
            for td in self.ir.apis[n].typedefs:
                if isinstance(td.datatype, ir.DataTypeStruct):
                    tds[td.name] = td.datatype
        # the crossings' item types: a packed struct (SV type key -> (Python
        # name, struct)) or an int (its width, by crossing)
        self.items: Dict[str, Tuple[str, ir.DataTypeStruct]] = {}
        self.ints: Dict[str, int] = {}
        for n, x in self.ir.xings.items():
            dt = x.element_type
            if isinstance(dt, ir.DataTypeInt):
                self.ints[n] = dt.bits
                continue
            st = type_m.get(dt.ref_name) if isinstance(dt, ir.DataTypeRef) else None
            if not isinstance(st, ir.DataTypeStruct):
                raise NotImplementedError(f"crossing '{n}': item type "
                                          f"{getattr(dt, 'ref_name', type(dt).__name__)} "
                                          "is not an int or a packed struct")
            self.items[dt.ref_name] = (_short(dt.ref_name), st)
        self.local_types: List[str] = []    # item types no API declares
        for k, (pn, st) in self.items.items():
            api = self._api_type(pn)
            if api is None:
                self.local_types.append(k)
            elif [f.name for f in api.fields] != [f.name for f in st.fields]:
                raise ValueError(f"crossing item '{k}' and the API's '{pn}' have "
                                 "different fields")

    def _api_type(self, n: str) -> Optional[ir.DataTypeStruct]:
        for tds in self.api_mods.values():
            if n in tds:
                return tds[n]
        return None

    # -- the API module ---------------------------------------------------------

    def api_modules(self) -> Dict[str, str]:
        """Module name -> text, one per API scope the endpoints use."""
        out = {}
        for mod, tds in self.api_mods.items():
            lines = [f'"""{mod}: the transaction-level API, for cocotb (zuspec-be-sv',
                     "xtor_cocotb). A method's outputs are its return value (several as a",
                     'tuple); its inputs are its arguments."""',
                     "from dataclasses import dataclass",
                     "from typing import Protocol"]
            for tn, st in tds.items():
                lines += ["", "", *self._dataclass(tn, st)]
            for n, e in self.ir.eps.items():
                if self.api_of[e.api] != mod or any(
                        l == f"class {e.api}(Protocol):" for l in lines):
                    continue
                lines += ["", "", f"class {e.api}(Protocol):"]
                for m in self.ir.apis[n].methods:
                    ins = [f"{a.arg}: {self._py_type(a.datatype)}" for a in m.args.args
                           if a.direction != _OUT]
                    outs = [self._py_type(a.datatype) for a in m.args.args
                            if a.direction == _OUT]
                    ret = ("None" if not outs else outs[0] if len(outs) == 1
                           else "tuple[" + ", ".join(outs) + "]")
                    kw = "async def" if m.is_async else "def"
                    lines.append(f"    {kw} {m.name}(self{''.join(', ' + i for i in ins)}) "
                                 f"-> {ret}: ...")
            out[mod] = "\n".join(lines) + "\n"
        return out

    def _dataclass(self, n: str, st: ir.DataTypeStruct) -> List[str]:
        lines = ["@dataclass", f"class {n}:"]
        for f in st.fields:
            lines.append(f"    {f.name}: {self._py_type(f.datatype)} = "
                         f"{self._default(f.datatype)}")
        return lines

    def _py_type(self, dt) -> str:
        if isinstance(dt, ir.DataTypeInt):
            return "int"
        if isinstance(dt, ir.DataTypeRef):
            return _short(dt.ref_name)
        raise NotImplementedError(f"API type {type(dt).__name__}")

    def _default(self, dt) -> str:
        if isinstance(dt, ir.DataTypeInt):
            return "0"
        raise NotImplementedError(f"API field type {type(dt).__name__}")

    # -- the bridge module --------------------------------------------------------

    def module_text(self) -> str:
        c = self.core.name
        imports: Dict[str, List[str]] = {}
        for n, e in self.ir.eps.items():
            mod = self.api_of[e.api]
            names = imports.setdefault(mod, [])
            for tn in self.api_mods[mod]:
                if tn not in names:
                    names.append(tn)
            if e.api not in names:
                names.append(e.api)
        exports = [e.api for e in self.ir.eps.values() if e.kind == "export"]
        lines = [f'"""{self.name}: {c}\'s API and task code, for cocotb',
                 "(zuspec-be-sv xtor_cocotb). Built with the core's handle and its clock,",
                 "it drives and samples the core's crossing ports, as the SV interface",
                 'does in a shell. Make calls at a rising edge, or before the first."""',
                 "from cocotb import start_soon"]
        if self.wait == "edge":
            lines += ["from cocotb.triggers import ReadOnly, ReadWrite, RisingEdge", ""]
        else:
            lines += ["from cocotb.triggers import Event, Lock, ReadOnly, RisingEdge", ""]
        for mod, names in imports.items():
            lines.append(f"from {mod} import {', '.join(names)}")
        for k in self.local_types:
            pn, st = self.items[k]
            lines += ["", "", *self._dataclass(pn, st)]
        for k, (pn, st) in self.items.items():
            lines += ["", "", *self._packers(pn, st)]
        if self.wait == "event" and any(x.direction == ir.SyncXingDir.FROM_SYNC
                                        for x in self.ir.xings.values()):
            lines += ["", "", "class _Waiter:",
                      '    """A get waiting for the edge that gives an item."""',
                      "    def __init__(self):",
                      "        self.ev = Event()",
                      "        self.item = None"]
        head = f"class {self.name}" + (f"({', '.join(exports)})" if exports else "")
        lines += ["", "", head + ":"]
        if self.wait == "edge":
            lines += self._init() + self._api_side() + self._task_side()
        else:
            lines += self._init_event() + self._api_side() + self._task_side_event()
        return "\n".join(lines) + "\n"

    def _packers(self, pn: str, st: ir.DataTypeStruct) -> List[str]:
        """SV packed layout: the first field is the most significant."""
        fs = []
        lsb = 0
        for f in reversed(st.fields):
            if not isinstance(f.datatype, ir.DataTypeInt):
                raise NotImplementedError(f"item type '{pn}': field '{f.name}' is "
                                          f"{type(f.datatype).__name__}")
            w = f.datatype.bits
            fs.append((f.name, lsb, (1 << w) - 1))
            lsb += w
        fs.reverse()
        pk = " | ".join(f"((v.{n} & {m:#x}) << {s})" if s else f"(v.{n} & {m:#x})"
                        for n, s, m in fs)
        up = ", ".join(f"{n}=(x >> {s}) & {m:#x}" if s else f"{n}=x & {m:#x}"
                       for n, s, m in fs)
        return [f"def _pack_{pn}(v):", f"    return {pk}", "", "",
                f"def _unpack_{pn}(x):", f"    return {pn}({up})"]

    def _new(self, n: str) -> str:
        """Crossing ``n``'s empty item."""
        if n in self.ints:
            return "0"
        return self.items[self.ir.xings[n].element_type.ref_name][0] + "()"

    def _pack(self, n: str, v: str) -> str:
        if n in self.ints:
            return f"{v} & {(1 << self.ints[n]) - 1:#x}"
        return f"_pack_{self.items[self.ir.xings[n].element_type.ref_name][0]}({v})"

    def _unpack(self, n: str, v: str) -> str:
        if n in self.ints:
            return v
        return f"_unpack_{self.items[self.ir.xings[n].element_type.ref_name][0]}({v})"

    def _init(self) -> List[str]:
        L = ["    def __init__(self, core, clock):",
             "        self._core = core",
             "        self._clock = clock",
             "        self._nba = {}          # deferred writes (<=), landed at ReadWrite",
             "        self._smp = None        # the core's outputs, as the next edge sees them"]
        for n in self.ir.ports():
            L.append(f"        self._{n} = None")
        for n, x in self.ir.xings.items():
            if x.direction == ir.SyncXingDir.TO_SYNC:
                L += [f"        self._{n}_put_t = 0   # {n}: task side -> core",
                      f"        self._{n}_took = 0",
                      f"        self._{n}_data = {self._new(n)}"]
            else:
                L += [f"        self._{n}_gave = 0    # {n}: core -> task side",
                      f"        self._{n}_got = 0",
                      f"        self._{n}_wait = 0",
                      f"        self._{n}_q = {self._new(n)}"]
        L.append("        start_soon(self._clocked())")
        return L

    # -- API side ----------------------------------------------------------------

    def _api_side(self) -> List[str]:
        L: List[str] = []
        ports = self.ir.ports()
        for n in ports:
            L += ["",
                  f"    def set_{n}(self, imp):",
                  f'        """Gives port \'{n}\' its implementation of '
                  f'{self.ir.eps[n].api}; before run()."""',
                  f"        self._{n} = imp"]
        for b in self.core.task_blocks:
            if b.name != "run":
                L += [""] + self._block(b)
        L.append("")
        check = [f"        if self._{n} is None:\n"
                 f"            raise RuntimeError(\"{self.name}: run() with port '{n}' "
                 f"unset (call set_{n}())\")" for n in ports]
        run = self.ir.blocks.get("run")
        if run is not None:
            L += self._block(run, check)
        else:
            L += ["    async def run(self):",
                  '        """Nothing to run: the API\'s calls drive the crossings."""'] + check
        return L

    def _block(self, b: ir.ProcBlock, pre: Optional[List[str]] = None) -> List[str]:
        if b.name in self.ir.imp:
            ep, m = self.ir.imp[b.name]
            args = m.args.args
            doc = f"{self.core.name}::{b.name} (API '{self.ir.eps[ep].api}', export '{ep}')."
        else:
            args = b.args
            doc = f"{self.core.name}::{b.name}."
        self.outs = [a.arg for a in args if a.direction == _OUT]
        ins = [a.arg for a in args if a.direction != _OUT]
        L = [f"    async def {b.name}(self{''.join(', ' + i for i in ins)}):",
             f'        """{doc}"""']
        L += pre or []
        for a in args:
            if a.direction == _OUT:
                L.append(f"        {a.arg} = {self._init_value(a.datatype)}")
        L += self.stmts(b.body, "        ")
        if self.outs:
            L.append(f"        return {self._ret()}")
        return L

    def _init_value(self, dt) -> str:
        if isinstance(dt, ir.DataTypeRef):
            return f"{_short(dt.ref_name)}()"
        return "0"

    def _ret(self) -> str:
        return ", ".join(self.outs)

    def stmts(self, body: list, p: str) -> List[str]:
        out: List[str] = []
        for s in body:
            if isinstance(s, ir.StmtExpr):
                e = s.expr.value if isinstance(s.expr, ir.ExprAwait) else s.expr
                if not isinstance(e, ir.ExprCall):
                    raise ValueError(f"unsupported expression statement {type(e).__name__}")
                out.append(p + self.call(e, isinstance(s.expr, ir.ExprAwait)))
            elif isinstance(s, ir.StmtAnnAssign):
                if s.value is not None:
                    out.append(f"{p}{s.target.name} = {self.expr(s.value)}")
            elif isinstance(s, ir.StmtAssign):
                if len(s.targets) != 1:
                    raise NotImplementedError("assignment to several targets")
                out.append(f"{p}{self.expr(s.targets[0])} = {self.expr(s.value)}")
            elif isinstance(s, ir.StmtAugAssign):
                op = _BIN.get(s.op) if isinstance(s.op, ir.BinOp) else None
                if op is None:
                    raise NotImplementedError(f"augmented assignment {s.op}")
                out.append(f"{p}{self.expr(s.target)} {op}= {self.expr(s.value)}")
            elif isinstance(s, ir.StmtIf):
                out.append(f"{p}if {self.expr(s.test)}:")
                out += self._suite(s.body, p)
                if s.orelse:
                    out.append(f"{p}else:")
                    out += self._suite(s.orelse, p)
            elif isinstance(s, ir.StmtWhile):
                if s.orelse:
                    raise ValueError("unsupported while ... else in task code")
                t = s.test
                c = "True" if isinstance(t, ir.ExprConstant) and t.value in (True, 1) \
                    else self.expr(t)
                out.append(f"{p}while {c}:")
                out += self._suite(s.body, p)
            elif isinstance(s, ir.StmtBreak):
                out.append(f"{p}break")
            elif isinstance(s, ir.StmtContinue):
                out.append(f"{p}continue")
            elif isinstance(s, ir.StmtPass):
                out.append(f"{p}pass")
            elif isinstance(s, ir.StmtReturn) and s.value is None:
                out.append(f"{p}return {self._ret()}".rstrip())
            else:
                raise NotImplementedError(f"task code: {type(s).__name__}")
        return out

    def _suite(self, body: list, p: str) -> List[str]:
        return self.stmts(body, p + "    ") or [p + "    pass"]

    def call(self, e: ir.ExprCall, blocking: bool) -> str:
        """A call on a crossing (the bridge's put/get) or a port (its
        implementation's method; the outputs assigned from its result)."""
        f = e.func
        if not (isinstance(f, ir.ExprAttribute) and isinstance(f.value, ir.ExprHierarchical)
                and len(f.value.elements) == 1):
            raise ValueError(f"unsupported call {f!r} in task code")
        n, m = f.value.elements[0].name, f.attr
        if e.keywords:
            raise ValueError(f"call '{n}.{m}': keyword arguments")
        if n in self.ir.xings:
            x = self.ir.xings[n]
            want = "put" if x.direction == ir.SyncXingDir.TO_SYNC else "get"
            if m != want or not blocking:
                raise NotImplementedError(
                    f"crossing '{n}': '{m}' (the bridge has '{n}_{want}' only)")
            if len(e.args) != 1:
                raise ValueError(f"crossing '{n}': {m} takes one item")
            if m == "put":
                return f"await self._{n}_put({self.expr(e.args[0])})"
            return f"{self.expr(e.args[0])} = await self._{n}_get()"
        if n in self.ir.eps and self.ir.eps[n].kind == "port":
            api = self.ir.apis[n]
            fn = next((x for x in api.methods if x.name == m), None)
            if fn is None:
                raise ValueError(f"call '{n}.{m}': API '{api.name}' has no '{m}'")
            if len(fn.args.args) != len(e.args):
                raise ValueError(f"call '{n}.{m}': {len(e.args)} arguments, "
                                 f"the API has {len(fn.args.args)}")
            ins = [self.expr(v) for a, v in zip(fn.args.args, e.args) if a.direction != _OUT]
            outs = [self.expr(v) for a, v in zip(fn.args.args, e.args) if a.direction == _OUT]
            c = f"self._{n}.{m}({', '.join(ins)})"
            if fn.is_async:
                c = "await " + c
            return f"{', '.join(outs)} = {c}" if outs else c
        raise ValueError(f"call '{n}.{m}': '{n}' is neither a crossing nor a port")

    def expr(self, e) -> str:
        if isinstance(e, ir.ExprRefLocal):
            return e.name
        if isinstance(e, ir.ExprConstant):
            v = e.value
            return str(int(v)) if isinstance(v, bool) else repr(v)
        if isinstance(e, ir.ExprAttribute):
            return f"{self.expr(e.value)}.{e.attr}"
        if isinstance(e, ir.ExprBin):
            op = _BIN.get(e.op)
            if op is None:
                raise NotImplementedError(f"task code: operator {e.op}")
            return f"({self.expr(e.lhs)} {op} {self.expr(e.rhs)})"
        if isinstance(e, ir.ExprCompare):
            s = self.expr(e.left)
            for op, r in zip(e.ops, e.comparators):
                if op not in _CMP:
                    raise NotImplementedError(f"task code: comparison {op}")
                s += f" {_CMP[op]} {self.expr(r)}"
            return f"({s})"
        if isinstance(e, ir.ExprBool):
            j = " and " if e.op == ir.BoolOp.And else " or "
            return "(" + j.join(self.expr(v) for v in e.values) + ")"
        if isinstance(e, ir.ExprUnary):
            if e.op == ir.UnaryOp.Not:
                return f"(not {self.expr(e.operand)})"
            if e.op == ir.UnaryOp.USub:
                return f"(-{self.expr(e.operand)})"
            raise NotImplementedError(f"task code: operator {e.op} (needs a width)")
        raise NotImplementedError(f"task code: expression {type(e).__name__}")

    # -- task side of the crossings (as the SV interface) ------------------------

    def _task_side(self) -> List[str]:
        L = ["",
             "    # -- the crossings' task side: the SV interface's code (X3-X8) --",
             "",
             "    def _at(self, port):",
             '        """A core output, as the edge at hand sees it."""',
             "        if self._smp is None:      # before the first sample",
             "            return int(getattr(self._core, port).value)",
             "        return self._smp[port]",
             "",
             "    def _set(self, var, v):",
             '        """var <= v."""',
             "        self._nba[var] = v",
             "",
             "    async def _edge(self):",
             "        await RisingEdge(self._clock)"]
        for n, x in self.ir.xings.items():
            pv, pr, pd = (x.ports[r] for r in ("valid", "ready", "data"))
            if x.direction == ir.SyncXingDir.TO_SYNC:
                L += ["",
                      f"    # {n}: task side -> core (to_sync, depth 1). Full from",
                      f"    # {n}_put until the edge that takes its item.",
                      f"    def _{n}_valid(self):",
                      f"        return self._{n}_put_t ^ self._{n}_took",
                      "",
                      "    # put: waits for room (the slot empty, or its item taken at this",
                      "    # edge), fills the slot, returns at the edge that takes it (X6).",
                      f"    async def _{n}_put(self, item):",
                      f'        while self._{n}_valid() and not self._at("{pr}"):',
                      "            await self._edge()",
                      f'        self._set("_{n}_data", item)',
                      f'        self._set("_{n}_put_t", self._{n}_put_t ^ 1)',
                      "        await self._edge()",
                      f'        while not self._at("{pr}"):',
                      "            await self._edge()"]
            else:
                L += ["",
                      f"    # {n}: core -> task side (from_sync, depth 1). The slot",
                      f"    # fills at a giving edge that no {n}_get takes.",
                      f"    def _{n}_full(self):",
                      f"        return self._{n}_gave ^ self._{n}_got",
                      "",
                      f"    def _{n}_given(self):",
                      f'        return self._at("{pv}") and not self._{n}_full()',
                      "",
                      "    # get: the slot's item; else one given at this edge with no get",
                      "    # waiting for it; else wait for the edge that gives one (it goes to",
                      "    # the waiter, not the slot) (X5).",
                      f"    async def _{n}_get(self):",
                      f"        if self._{n}_full():",
                      f"            item = self._{n}_q",
                      f'            self._set("_{n}_got", self._{n}_got ^ 1)',
                      f"        elif self._{n}_given() and not self._{n}_wait:",
                      f'            item = {self._unpack(n, _at(pd))}',
                      f'            self._set("_{n}_got", self._{n}_got ^ 1)',
                      "        else:",
                      f'            self._set("_{n}_wait", 1)',
                      "            await self._edge()",
                      f"            while not self._{n}_given():",
                      "                await self._edge()",
                      f'            item = {self._unpack(n, _at(pd))}',
                      f'            self._set("_{n}_wait", 0)',
                      "        return item"]
        # the clocked loop: land the writes, drive, sample, then the edge's
        # always blocks
        drive, sample, always = [], [], []
        for n, x in self.ir.xings.items():
            pv, pr, pd = (x.ports[r] for r in ("valid", "ready", "data"))
            if x.direction == ir.SyncXingDir.TO_SYNC:
                drive += [f"            c.{pv}.value = self._{n}_valid()",
                          f"            c.{pd}.value = {self._pack(n, f'self._{n}_data')}"]
                sample.append(f'"{pr}": int(c.{pr}.value)')
                always += [f'            if self._{n}_valid() and self._at("{pr}"):',
                           f'                self._set("_{n}_took", self._{n}_took ^ 1)']
            else:
                drive.append(f"            c.{pr}.value = int(not self._{n}_full())")
                sample += [f'"{pv}": int(c.{pv}.value)', f'"{pd}": int(c.{pd}.value)']
                always += [f"            if self._{n}_given() and not self._{n}_wait:",
                           f'                self._set("_{n}_gave", self._{n}_gave ^ 1)',
                           f'                self._set("_{n}_q", {self._unpack(n, _at(pd))})']
        L += ["",
              "    async def _clocked(self):",
              "        c = self._core",
              "        while True:",
              "            await ReadWrite()",
              "            for k, v in self._nba.items():",
              "                setattr(self, k, v)",
              "            self._nba = {}"] + drive + [
              "            await ReadOnly()",
              "            self._smp = {" + ", ".join(sample) + "}",
              "            await RisingEdge(self._clock)",
              "            # the always blocks: this edge's writes"] + always
        return L

    # -- the task side, waiting on events (wait="event") -----------------------

    def _init_event(self) -> List[str]:
        L = ["    def __init__(self, core, clock):",
             "        self._core = core",
             "        self._clock = clock"]
        for n in self.ir.ports():
            L.append(f"        self._{n} = None")
        for n, x in self.ir.xings.items():
            pv, pr, pd = (x.ports[r] for r in ("valid", "ready", "data"))
            if x.direction == ir.SyncXingDir.TO_SYNC:
                L += [f"        self._{n}_lock = Lock()      # {n}: task side -> core",
                      f"        core.{pv}.value = 0",
                      f"        core.{pd}.value = 0"]
            else:
                L += [f"        self._{n}_full = False       # {n}: core -> task side",
                      f"        self._{n}_q = None",
                      f"        self._{n}_room = Event()",
                      f"        self._{n}_next = None       # the item the next edge takes",
                      f"        self._{n}_waiters = []",
                      f"        core.{pr}.value = 1",
                      f"        start_soon(self._{n}_recv())"]
        return L

    def _task_side_event(self) -> List[str]:
        """Each crossing's task side, as the SV interface's, but waking only
        when a transfer can happen. A transfer is at the first rising edge
        whose sampled ready and valid are both high. The value an edge
        samples is the one settled in the ReadOnly phase of the time step
        before it: ready and valid change only in the time steps of this
        clock's edges (synchronous ready/valid). Writes are made after a
        rising edge, so they land after what it sampled (cocotb applies
        them in ReadWrite)."""
        L = ["",
             "    # -- the crossings' task side (X3-X8): wakes only for transfers --"]
        for n, x in self.ir.xings.items():
            pv, pr, pd = (x.ports[r] for r in ("valid", "ready", "data"))
            if x.direction == ir.SyncXingDir.TO_SYNC:
                L += ["",
                      f"    # {n}: task side -> core (to_sync, depth 1). put raises valid",
                      "    # with its item, waits (settled) for ready, then for the edge",
                      "    # that takes the item, and returns at it (X6). Puts take turns;",
                      "    # one made at that edge keeps valid up with its own item.",
                      f"    async def _{n}_put(self, item):",
                      "        c = self._core",
                      f"        async with self._{n}_lock:",
                      f"            c.{pd}.value = {self._pack(n, 'item')}",
                      f"            c.{pv}.value = 1",
                      "            while True:",
                      "                await ReadOnly()",
                      f"                if c.{pr}.value:",
                      "                    break",
                      f"                await RisingEdge(c.{pr})",
                      "            await RisingEdge(self._clock)",
                      f"            c.{pv}.value = 0"]
            else:
                L += ["",
                      f"    # {n}: core -> task side (from_sync, depth 1). ready is up while",
                      "    # the slot is empty. get: the slot's item; else the one the edge",
                      "    # at hand takes, if no get waits for it; else wait for the edge",
                      "    # that gives one (it goes to the waiter, not the slot) (X5).",
                      f"    async def _{n}_get(self):",
                      f"        if self._{n}_full:",
                      f"            item = self._{n}_q",
                      f"            self._{n}_full = False",
                      f"            self._core.{pr}.value = 1",
                      f"            self._{n}_room.set()",
                      "            return item",
                      f"        if self._{n}_next is not None and not self._{n}_waiters:",
                      f"            item, self._{n}_next = self._{n}_next, None",
                      "            return item",
                      "        w = _Waiter()",
                      f"        self._{n}_waiters.append(w)",
                      "        await w.ev.wait()",
                      "        return w.item",
                      "",
                      "    # Watches for the core's items while the slot has room: valid",
                      "    # settled high before an edge means that edge gives an item.",
                      f"    async def _{n}_recv(self):",
                      "        c = self._core",
                      "        while True:",
                      f"            if self._{n}_full:",
                      f"                await self._{n}_room.wait()",
                      "            await ReadOnly()",
                      f"            if not c.{pv}.value:",
                      f"                await RisingEdge(c.{pv})",
                      "                continue",
                      f"            self._{n}_next = {self._unpack(n, f'int(c.{pd}.value)')}",
                      "            await RisingEdge(self._clock)",
                      f"            item, self._{n}_next = self._{n}_next, None",
                      "            if item is None:          # a get at this edge took it",
                      "                continue",
                      f"            if self._{n}_waiters:",
                      f"                w = self._{n}_waiters.pop(0)",
                      "                w.item = item",
                      "                w.ev.set()",
                      "            else:",
                      f"                self._{n}_q, self._{n}_full = item, True",
                      f"                self._{n}_room = Event()",
                      f"                c.{pr}.value = 0"]
        return L

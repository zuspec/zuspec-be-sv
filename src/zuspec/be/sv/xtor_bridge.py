"""A transactor's bridge class from its lowered core (fw-hdl
xtor-shell-plan.md T4, D8).

The bridge is the class side of a transactor shell (``xtor_shell``): the
API boundary a class environment uses in place of the class transactor.
It is generated from the core's IR alone:

- its API endpoints (``api_endpoints``): an export the transactor
  implements itself (an initiator's ``t``) makes the bridge implement that
  API; a port (a target's ``p``) is held, and called through
  ``get_if()``;
- its task code (``task_blocks``, kept by ``sync_xing_lower``): an API
  method's block becomes the bridge's implementation of it, with the API's
  signature; ``run()`` becomes the bridge's ``run()`` (``fw_run_if``);
- its crossings (``sync_xings``): a crossing call in the task code, named
  by its path, becomes a call of the interface's task through the virtual
  interface ``vif`` (``reqs.put(x)`` -> ``vif.reqs_put(x)``).

The rest of the task code is written as it is. The crossings' items are
the interface's types (the core's types package); an API's types are the
API's own. The two are packed structs of one layout, so they assign.

Two texts (SV style: one class per ``.svh``, included in its package):
``package_text()`` (``<name>_pkg``) and ``class_text()`` (``<name>.svh``).

Flavors (fw-hdl xtor-shell-plan D10); the task code is the same:

- ``fw-hdl``: ``extends fw_component``, its endpoints ``fw_export`` /
  ``fw_port``, ``run()`` as ``fw_run_if``; ``vif`` is set by whoever
  integrates it (D4).
- ``plain``: no fw-hdl dependency (UVM, any class code). Built with its
  interface, ``new(u_if)``. It implements the APIs of its exports; each
  port is a handle to the API, given by ``set_<port>(imp)``. Every
  bridge has ``task run()``, which the environment calls when it is
  ready: a target's is its task code (no ``run()``, no calls on its
  ports), an initiator's returns at once. ``run()`` stops with
  ``$fatal`` if a port is unset.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import zuspec.ir.core as ir

from .proc_emit import ProcEmitter
from .structural import ValueTypes, _decl, _ref, _value_text

__all__ = ["XtorBridgeEmitter", "XtorIr"]

_DIR = {ir.SignalDirection.INPUT: "input", ir.SignalDirection.OUTPUT: "output",
        ir.SignalDirection.INOUT: "inout"}


class XtorIr:
    """What a bridge of any flavor is made from, checked: the core's
    crossings (``xings``, by name), API endpoints (``eps``) and their APIs
    (``apis``), task blocks (``blocks``), and the API methods the bridge
    implements (``imp``: method -> (export, ``Function``))."""

    def __init__(self, core: ir.DataTypeComponent, type_m: Dict[str, ir.DataType]):
        self.core = core
        self.xings = {x.path[0]: x for x in core.sync_xings if len(x.path) == 1}
        self.eps: Dict[str, ir.ApiEndpoint] = {}
        self.apis: Dict[str, ir.DataTypeProtocol] = {}
        for e in core.api_endpoints:
            if e.kind not in ("port", "export"):
                raise NotImplementedError(f"'{core.name}': endpoint '{e.name}' is a {e.kind}")
            if e.kind == "export" and e.imp:
                raise NotImplementedError(f"'{core.name}': export '{e.name}' is implemented "
                                          "by a child (a bridge has none)")
            t = type_m.get(e.api)
            if isinstance(t, ir.DataTypeParameterized):
                t = t.body
            if not isinstance(t, ir.DataTypeProtocol):
                raise ValueError(f"'{core.name}': endpoint '{e.name}': API '{e.api}' "
                                 "is not in the IR")
            self.eps[e.name] = e
            self.apis[e.name] = t
        self.blocks = {b.name: b for b in core.task_blocks}
        for b in core.task_blocks:
            if not b.body:
                raise ValueError(f"'{core.name}': task '{b.name}' has no code "
                                 "(see FW-W-TASK-MAP)")
        if "run" in self.blocks and self.blocks["run"].args:
            raise ValueError(f"'{core.name}': run() has arguments")
        self.imp: Dict[str, tuple] = {}
        for n, e in self.eps.items():
            if e.kind != "export":
                continue
            for m in self.apis[n].methods:
                if m.name in self.imp:
                    raise NotImplementedError(f"'{core.name}': method '{m.name}' is in two "
                                              "implemented APIs")
                if m.name not in self.blocks:
                    raise ValueError(f"'{core.name}': API method '{m.name}' (export "
                                     f"'{n}') has no task code")
                if not m.is_async:
                    raise NotImplementedError(f"'{core.name}': API method '{m.name}' is "
                                              "a function, not a task")
                self.imp[m.name] = (n, m)

    def ports(self) -> List[str]:
        return [n for n, e in self.eps.items() if e.kind == "port"]

    def walk_ports(self, x) -> List[str]:
        """The ports called in ``x``, in order of first use."""
        out: List[str] = []

        def walk(n):
            if isinstance(n, ir.ExprHierarchical) and n.elements:
                p = n.elements[0].name
                e = self.eps.get(p)
                if e is not None and e.kind == "port" and p not in out:
                    out.append(p)
            if isinstance(n, list):
                for v in n:
                    walk(v)
            elif isinstance(n, ir.Base) and not isinstance(n, ir.DataType):
                for k in n.__dataclass_fields__:
                    if k not in ("loc", "provenance", "anchor"):
                        walk(getattr(n, k))
        walk(x)
        return out


class XtorBridgeEmitter(ProcEmitter):
    """The bridge class ``name`` of the core ``core`` (a lowered, flat
    component with ``task_blocks``), over the interface ``iface``
    (``XtorShellEmitter``'s ``<shell>_if``). ``type_m`` has the APIs;
    ``types`` names the core's packed types."""

    def __init__(self, core: ir.DataTypeComponent, name: str, iface: str,
                 type_m: Dict[str, ir.DataType], types: Optional[ValueTypes] = None,
                 flavor: str = "fw-hdl"):
        if flavor not in ("fw-hdl", "plain"):
            raise ValueError(f"bridge flavor '{flavor}' (fw-hdl or plain)")
        self.flavor = flavor
        super().__init__(core, decl=lambda dt, n: _decl(dt, n, types),
                         type_name=lambda dt: _ref(dt, types))
        self.fields = []        # the core's state is not the bridge's
        self.name = name
        self.iface = iface
        self.types = types
        x = self.ir = XtorIr(core, type_m)
        self.xings, self.eps, self.apis = x.xings, x.eps, x.apis
        self.blocks, self.imp = x.blocks, x.imp

    # -- names -----------------------------------------------------------------

    def api(self, ep: str) -> str:
        """Endpoint ``ep``'s API, spelled: ``wb_pkg::wb_if #(32, 32)``."""
        e = self.eps[ep]
        s = f"{e.api_scope}::{e.api}" if e.api_scope else e.api
        if e.api_params:
            s += " #(" + ", ".join(self._param(v) for v in e.api_params.values()) + ")"
        return s

    @staticmethod
    def _param(v) -> str:
        if isinstance(v, ir.DataTypeRef):
            return v.ref_name
        if isinstance(v, ir.ExprConstant):
            return str(int(v.value) if isinstance(v.value, bool) else v.value)
        raise NotImplementedError(f"API parameter {type(v).__name__}")

    def _typedefs(self) -> Dict[str, str]:
        """Local names of the implemented APIs' typedefs -> their spelling."""
        out: Dict[str, str] = {}
        for n, e in self.eps.items():
            if e.kind != "export":
                continue
            for td in self.apis[n].typedefs:
                s = f"{self.api(n)}::{td.name}"
                if out.get(td.name, s) != s:
                    raise NotImplementedError(f"'{self.comp.name}': typedef '{td.name}' "
                                              "is in two implemented APIs")
                out[td.name] = s
        return out

    def _arg_type(self, ep: str, dt: ir.DataType) -> str:
        """An API argument's type, as the API spells it."""
        if isinstance(dt, ir.DataTypeRef):
            p = self.eps[ep].api_params.get(dt.ref_name)
            return self._param(p) if p is not None else dt.ref_name
        return _value_text(dt)

    # -- the texts ---------------------------------------------------------------

    def package_text(self) -> str:
        if self.flavor == "plain":
            return "\n".join([
                f"// {self.name}: the class side of {self.comp.name}'s shell, with no "
                "fw-hdl",
                "// dependency (zuspec-be-sv xtor_bridge, plain flavor).",
                f"package {self.name}_pkg;",
                f'    `include "{self.name}.svh"',
                "endpackage",
                ""])
        return "\n".join([
            f"// {self.name}: the class side of {self.comp.name}'s shell "
            "(zuspec-be-sv xtor_bridge).",
            '`include "fw_hdl_macros.svh"',
            "",
            f"package {self.name}_pkg;",
            "    import fw_hdl_pkg::*;",
            "",
            f'    `include "{self.name}.svh"',
            "endpackage",
            ""])

    def class_text(self) -> str:
        if self.flavor == "plain":
            return self._plain_text()
        imps = [self.api(n) for n, e in self.eps.items() if e.kind == "export"]
        if "run" in self.blocks:
            imps.append("fw_run_if")
        head = f"class {self.name} extends fw_component"
        if imps:
            head += " implements " + ", ".join(imps)
        lines = [f"// {self.name}: {self.comp.name}'s API and task code, its crossings",
                 f"// carried by {self.iface} (zuspec-be-sv xtor_bridge).",
                 head + ";",
                 f"    `fw_component_utils({self.name})"]
        tds = self._typedefs()
        lines += [f"    typedef {s} {n};" for n, s in tds.items()]
        lines.append("")
        lines += [f"    fw_{e.kind} #({self.api(n)}) {n};" for n, e in self.eps.items()]
        lines.append(f"    virtual {self.iface} vif;")
        lines += ["", "    virtual function void build();"]
        lines += [f'        {n} = fw_{e.kind} #({self.api(n)})::create("{n}", this);'
                  for n, e in self.eps.items()]
        lines.append("    endfunction")
        for b in self.comp.task_blocks:
            lines.append("")
            lines += self.task(b)
        lines += ["endclass", ""]
        return "\n".join(lines)

    def _plain_text(self) -> str:
        imps = [self.api(n) for n, e in self.eps.items() if e.kind == "export"]
        ports = [n for n, e in self.eps.items() if e.kind == "port"]
        run = self.blocks.get("run")
        head = f"class {self.name}"
        if imps:
            head += " implements " + ", ".join(imps)
        lines = [f"// {self.name}: {self.comp.name}'s API and task code, its crossings",
                 f"// carried by {self.iface} (zuspec-be-sv xtor_bridge, plain flavor).",
                 head + ";"]
        lines += [f"    typedef {s} {n};" for n, s in self._typedefs().items()]
        lines.append("")
        lines += [f"    local {self.api(n)} {n};" for n in ports]
        lines += [f"    local virtual {self.iface} vif;",
                  "",
                  f"    function new(virtual {self.iface} u_if);",
                  "        vif = u_if;",
                  "    endfunction"]
        for n in ports:
            lines += ["",
                      f"    // Gives port '{n}' its implementation; before run().",
                      f"    function void set_{n}({self.api(n)} imp);",
                      f"        {n} = imp;",
                      "    endfunction"]
        for b in self.comp.task_blocks:
            if b.name == "run":
                continue
            lines.append("")
            lines += self.task(b)
        lines.append("")
        check = [f'        if ({n} == null) $fatal(1, "{self.name}: run() with port \'{n}\' '
                 f'unset (call set_{n}())");' for n in ports]
        if run is not None:
            lines += self.task(run, check)
        else:
            lines += ["    // Nothing to run: the API's calls drive the crossings.",
                      "    virtual task run();"] + check + ["    endtask"]
        lines += ["endclass", ""]
        return "\n".join(lines)

    # -- task code ---------------------------------------------------------------

    def task(self, b: ir.ProcBlock, pre: Optional[List[str]] = None) -> List[str]:
        if b.name in self.imp:
            ep, m = self.imp[b.name]
            args = [f"{_DIR.get(a.direction, 'input')} {self._arg_type(ep, a.datatype)} {a.arg}"
                    for a in m.args.args]
            head = f"    // {self.comp.name}::{b.name} (API '{self.eps[ep].api}', export '{ep}')."
        else:
            args = [f"{_DIR.get(a.direction, 'input')} {_decl(a.datatype, a.arg, self.types)}"
                    for a in b.args]
            head = f"    // {self.comp.name}::{b.name}."
        names = {a.arg for a in b.args} | set(self.eps) | {"vif"} | set(self._typedefs())
        out = [head, f"    virtual task {b.name}({', '.join(args)});"]
        # a port called in the body: its implementation, from get_if()
        self.port_if: Dict[str, str] = {}
        for p in self._ports(b.body):
            if self.flavor == "plain":      # the port is the API handle
                self.port_if[p] = p
                continue
            v = f"{p}_if"
            while v in names:
                v += "_"
            names.add(v)
            self.port_if[p] = v
            out.append(f"        {self.api(p)} {v} = {p}.get_if();")
        out += self._locals(b, "        ", names)
        out += pre or []
        out += self.stmts(b.body, "=", "        ")
        self.vars = {}
        out.append("    endtask")
        return out

    def _ports(self, x) -> List[str]:
        return self.ir.walk_ports(x)

    def stmts(self, body: list, op: str, p: str) -> List[str]:
        out: List[str] = []
        for s in body:
            if isinstance(s, ir.StmtExpr):
                e = s.expr.value if isinstance(s.expr, ir.ExprAwait) else s.expr
                if not isinstance(e, ir.ExprCall):
                    raise ValueError(f"unsupported expression statement {type(e).__name__}")
                out.append(f"{p}{self.call(e, isinstance(s.expr, ir.ExprAwait))};")
            elif isinstance(s, ir.StmtAnnAssign):
                if s.value is not None:
                    out.append(f"{p}{self.vars[s.target.name]} = {self.expr(s.value)};")
            elif isinstance(s, ir.StmtWhile):
                if s.orelse:
                    raise ValueError("unsupported while ... else in task code")
                t = s.test
                if isinstance(t, ir.ExprConstant) and t.value in (True, 1):
                    out.append(f"{p}forever begin")
                else:
                    out.append(f"{p}while ({self.expr(t)}) begin")
                out += self.stmts(s.body, op, p + "    ")
                out.append(f"{p}end")
            elif isinstance(s, ir.StmtBreak):
                out.append(f"{p}break;")
            elif isinstance(s, ir.StmtContinue):
                out.append(f"{p}continue;")
            elif isinstance(s, ir.StmtReturn) and s.value is None:
                out.append(f"{p}return;")
            else:
                out += super().stmts([s], op, p)
        return out

    def call(self, e: ir.ExprCall, blocking: bool) -> str:
        """A call on a crossing (through ``vif``) or a port (its ``get_if()``)."""
        f = e.func
        if not (isinstance(f, ir.ExprAttribute) and isinstance(f.value, ir.ExprHierarchical)
                and len(f.value.elements) == 1):
            raise ValueError(f"unsupported call {f!r} in task code")
        n, m = f.value.elements[0].name, f.attr
        if e.keywords:
            raise ValueError(f"call '{n}.{m}': keyword arguments")
        args = ", ".join(self.expr(a) for a in e.args)
        if n in self.xings:
            x = self.xings[n]
            want = "put" if x.direction == ir.SyncXingDir.TO_SYNC else "get"
            if m != want or not blocking:
                raise NotImplementedError(
                    f"crossing '{n}': '{m}' (the interface has '{n}_{want}' only)")
            return f"vif.{n}_{m}({args})"
        if n in self.port_if:
            return f"{self.port_if[n]}.{m}({args})"
        raise ValueError(f"call '{n}.{m}': '{n}' is neither a crossing nor a port")

    def _expr(self, e) -> str:
        if isinstance(e, ir.ExprCall):
            return self.call(e, False)
        return super()._expr(e)

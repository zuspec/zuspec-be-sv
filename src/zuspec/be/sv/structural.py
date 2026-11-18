"""SystemVerilog for a *flat structural* component: ports, nets,
``assign``\\ s and module instances.

This is the form zuspec-synth's structural flattening
(:func:`zuspec.synth.passes.struct_flatten.flatten_structure`) produces:
``FieldInOut`` ports, ``Field`` nets, ``net_assigns`` and
``module_instances`` whose ``PortConnection.signal`` is a net name, an
``ExprConstant`` tie or ``None`` (open), and an empty ``bind_map``.
:func:`is_flat_structural` says which components it covers;
:class:`~zuspec.be.sv.generator.SVGenerator` dispatches them here after its
parameterized-sync and fw-sync paths.

:func:`sv_module_name` is the one rule for a component type's module name,
shared with the flattening pass. :func:`write_filelist` writes a filelist:
extern sources first, then the generated files, children before parents.

Parameterized structure (core-layer1.md §6.6, Z5): a component the
flattening pass marked ``flat_structural`` may have ``template_params``
(``module m #(parameter type T = logic [7:0], parameter int n = 2)``),
ports and nets typed by a type parameter (``DataTypeRef``), array nets
(``DataTypeArray``, ``T u_in_data [n]``), ``generates``
(``GenerateFor``/``GenerateIf`` around ``NetAssign``\\ s and
``ModuleInstance``\\ s, and ``Field`` nets declared in the block),
array ports (``FieldInOut`` of a ``DataTypeArray``: ``input T in_data
[n]``), references to an element of an array net or port (an
``ExprHierarchical`` of one element with a ``subscript``), and parameter
expressions as instance parameters (rendered by
:func:`zuspec.be.sv.sync_param._cexpr`; a ``DataType`` value is a type
parameter's).

Packed structs and enums (core-layer1.md §6.3, §6.5). A port, net or type
parameter value may be a ``DataTypeRef`` to a packed ``DataTypeStruct`` or
a ``DataTypeEnum`` in ``type_m``. :class:`ValueTypes` collects the ones the
flat structural components use. Those the IR declares are written once
each, in dependency order, in one generated package
(:meth:`ValueTypes.package`, ``<top>_types_pkg`` by default), and are
referred to as ``<package>::<name>``; one with an ``extern_name`` is
declared by sources the back end does not generate (the RTL an extern
brings) and is referred to by that name.

Two- and four-state. Ports and nets are ``logic``: signals between modules
carry X and Z in simulation, whatever the source type. A type as a value
(a type parameter's default or override, ``bit [11:0]``), a value
parameter's type and a member of a generated struct follow
``DataTypeInt.four_state``: ``bit`` when False, ``logic`` when True or
unstated.

Process blocks (fw-hdl layer 2). A component's ``proc_blocks``, their
domains resolved to its nets, follow the instances as ``always_ff`` and
``always_comb`` blocks (:mod:`.proc_emit`).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Union

import zuspec.ir.core as ir

from .proc_emit import proc_block_lines
from .sync_param import _cexpr

__all__ = [
    "sv_module_name",
    "is_flat_structural",
    "StructuralEmitter",
    "ValueTypes",
    "default_types_package",
    "dependency_order",
    "write_filelist",
]


def sv_module_name(name: str) -> str:
    """A component type's name as a SystemVerilog identifier: ``.``, ``<``
    and ``>`` become ``__``, any other invalid character ``_``, and a
    leading digit gets a ``_`` (``fifo_chain#(bit[7:0])`` ->
    ``fifo_chain__bit_7_0__``)."""
    name = name.replace('.', '__').replace('<', '__').replace('>', '__')
    name = re.sub(r'[^a-zA-Z0-9_]', '_', name)
    if name and name[0].isdigit():
        name = '_' + name
    return name


def _is_plain_signal(f: ir.Field) -> bool:
    return (not f.is_const and f.kind == ir.FieldKind.Field
            and getattr(f, "role", None) is None
            and getattr(f, "drive", None) is None
            and isinstance(f.datatype, ir.DataTypeInt))


def _no_behavior(comp: ir.DataTypeComponent) -> bool:
    return not (comp.sync_processes or comp.comb_processes or comp.wire_processes
                or comp.proc_processes or comp.functions or comp.pipeline_root_ir
                or comp.stage_method_irs or comp.sync_method_irs)


def is_flat_structural(comp: ir.DataType) -> bool:
    """True for a component in the flat structural form:

    - a ``DataTypeComponent``, not a ``DataTypeExtern``;
    - no behavior: no sync, comb, wire or proc processes, no functions, no
      pipeline IR;
    - no ``template_params``, no pools, an empty ``bind_map``;
    - every field is a ``FieldInOut`` port or a plain ``Field`` net (not
      const, kind ``Field``, no role, no drive), with a ``DataTypeInt``
      type;
    - and it has ``module_instances`` or ``net_assigns``, or only ports.
    """
    if not isinstance(comp, ir.DataTypeComponent) or isinstance(comp, ir.DataTypeExtern):
        return False
    if not _no_behavior(comp):
        return False
    if getattr(comp, "flat_structural", False):
        return not comp.bind_map and not comp.pools and not comp.pool_binds
    if comp.template_params or comp.pools or comp.pool_binds or comp.bind_map:
        return False
    if not all(_is_plain_signal(f) for f in comp.fields):
        return False
    return bool(comp.module_instances or comp.net_assigns
                or all(isinstance(f, ir.FieldInOut) for f in comp.fields))


def _is_value_type(dt) -> bool:
    if isinstance(dt, ir.DataTypeEnum):
        return True
    return isinstance(dt, ir.DataTypeStruct) and dt.packed and not isinstance(
        dt, (ir.DataTypeClass, ir.DataTypeBundle))


def _ident(name: str) -> str:
    name = re.sub(r'[^a-zA-Z0-9_]', '_', name)
    return "_" + name if name and name[0].isdigit() else name


class ValueTypes:
    """The packed structs and enums that flat structural components use
    (through ports, nets, type parameters and instance parameters), and how
    SV names them. ``package`` is the generated package's name."""

    def __init__(self, type_m: Dict[str, ir.DataType], package: str,
                 comps: Optional[Iterable[ir.DataType]] = None):
        self.type_m = type_m
        self.package_name = package
        self.order: List[str] = []           # IR-declared types, dependencies first
        self.extern: List[str] = []          # types declared outside the IR
        self._name: Dict[str, str] = {}      # type key -> SV name in the package
        state: Dict[str, int] = {}

        def visit_type(dt):
            if isinstance(dt, ir.DataTypeArray):
                visit_type(dt.element_type)
            elif isinstance(dt, ir.DataTypeRef):
                t = type_m.get(dt.ref_name)
                if _is_value_type(t):
                    visit(dt.ref_name, t)

        def visit(key: str, t):
            if state.get(key) == 2:
                return
            if state.get(key) == 1:
                raise ValueError(f"type cycle through '{key}'")
            state[key] = 1
            if t.extern_name:
                self.extern.append(key)
            else:
                if isinstance(t, ir.DataTypeEnum):
                    visit_type(t.base_type)
                else:
                    for f in t.fields:
                        visit_type(f.datatype)
                self.order.append(key)
            state[key] = 2

        if comps is None:
            comps = [t for t in type_m.values() if isinstance(t, ir.DataTypeComponent)
                     and not isinstance(t, ir.DataTypeExtern)
                     and getattr(t, "flat_structural", False)]
        for c in comps:
            for tp in getattr(c, "template_params", None) or []:
                visit_type(getattr(tp, "default_value", None))
                visit_type(getattr(tp, "value_type", None))
            for f in c.fields:
                visit_type(f.datatype)

            def items(xs):
                for x in xs:
                    if isinstance(x, ir.Field):
                        visit_type(x.datatype)
                    elif isinstance(x, ir.ModuleInstance):
                        for v in x.parameters.values():
                            visit_type(v)
                    elif isinstance(x, (ir.GenerateFor, ir.GenerateIf)):
                        items(x.items)
                        items(getattr(x, "orelse", []) or [])
            items(c.module_instances)
            items(getattr(c, "generates", None) or [])

        # SV names: the type's own name, else qualified by its scope.
        used: set = set()
        for key in self.order:
            full = self.type_m[key].name or key
            parts = full.split("::")
            cands = [_ident(parts[-1]), _ident("__".join(parts))]
            name = next((c for c in cands if c not in used), None)
            if name is None:
                n = 1
                while f"{cands[-1]}_{n}" in used:
                    n += 1
                name = f"{cands[-1]}_{n}"
            used.add(name)
            self._name[key] = name

    @property
    def keys(self) -> set:
        """Every type key these cover: generated and extern."""
        return set(self.order) | set(self.extern)

    def sv(self, ref: str, inside: bool = False) -> Optional[str]:
        """How SV refers to type ``ref`` (a ``type_m`` key): ``pkg::name``
        (``name`` inside the package), the type's ``extern_name``, or None
        when it is not a packed struct or enum."""
        t = self.type_m.get(ref)
        if not _is_value_type(t):
            return None
        if t.extern_name:
            return t.extern_name
        name = self._name.get(ref)
        if name is None:
            return None
        return name if inside else f"{self.package_name}::{name}"

    def package(self) -> Optional[str]:
        """The package declaring the IR-declared types, or None if none."""
        if not self.order:
            return None
        lines = [f"package {self.package_name};", ""]
        for key in self.order:
            t = self.type_m[key]
            name = self._name[key]
            if isinstance(t, ir.DataTypeEnum):
                lines.append(self._enum(t, name))
            else:
                lines.append("  typedef struct packed {")
                for f in t.fields:
                    lines.append(f"    {_value_text(f.datatype, self, True)} {f.name};")
                lines.append(f"  }} {name};")
            lines.append("")
        lines.append(f"endpackage : {self.package_name}")
        return "\n".join(lines) + "\n"

    def _enum(self, t: ir.DataTypeEnum, name: str) -> str:
        base = t.base_type
        if base is None and t.width:
            base = ir.DataTypeInt(bits=t.width, signed=False)
        w = None
        if isinstance(base, ir.DataTypeInt) and base.width_expr is None:
            w = abs(base.bits)
        bt = f" {_value_text(base, self, True)}" if base is not None else ""
        items = []
        for lab, v in t.items.items():
            if w is None:
                items.append(f"{lab} = {v}")
            elif v < 0:
                items.append(f"{lab} = -{w}'sd{-v}")
            else:
                items.append(f"{lab} = {w}'d{v}")
        return f"  typedef enum{bt} {{\n    " + ",\n    ".join(items) + f"\n  }} {name};"


def _vec(kind: str, dt: ir.DataTypeInt) -> str:
    if dt.width_expr is not None:
        sgn = " signed" if dt.signed else ""
        return f"{kind}{sgn} [{_cexpr(dt.width_expr, True)}-1:0]"
    if dt.bits in (1, -1):
        return kind
    sgn = " signed" if dt.signed else ""
    return f"{kind}{sgn} [{dt.bits - 1}:0]"


def _ref(dt: ir.DataTypeRef, types: Optional[ValueTypes], inside: bool = False) -> str:
    if types is not None:
        n = types.sv(dt.ref_name, inside)
        if n is not None:
            return n
    return dt.ref_name                      # a type parameter


def _type(dt: ir.DataType, types: Optional[ValueTypes] = None) -> str:
    """A port's or net's type: ``logic``, whatever its source type's state
    count, or a named type."""
    if isinstance(dt, ir.DataTypeRef):
        return _ref(dt, types)
    return _vec("logic", dt)


def _value_text(dt: ir.DataType, types: Optional[ValueTypes] = None,
                inside: bool = False) -> str:
    """A type as a value (a type parameter's default or override, a struct
    member): ``bit`` for a two-state integer, else as :func:`_type`."""
    if isinstance(dt, ir.DataTypeRef):
        return _ref(dt, types, inside)
    if isinstance(dt, ir.DataTypeInt):
        return _vec("bit" if dt.four_state is False else "logic", dt)
    raise NotImplementedError(f"type {type(dt).__name__}")


def _value_type(dt: ir.DataType, types: Optional[ValueTypes] = None) -> str:
    """The type of a value parameter: ``int``, ``bit``, else a vector; a
    stated ``four_state`` picks ``bit`` or ``logic``."""
    if isinstance(dt, ir.DataTypeInt) and dt.width_expr is None:
        if dt.bits == 32 and dt.signed and dt.four_state is not True:
            return "int"
        if dt.bits in (1, -1) and not dt.signed and dt.four_state is not True:
            return "bit"
        if dt.four_state is not None:
            return _value_text(dt, types)
    return _type(dt, types)


def _decl(dt: ir.DataType, name: str, types: Optional[ValueTypes] = None) -> str:
    """A net declaration: ``T name`` or, for an array net, ``T name [n]``."""
    if isinstance(dt, ir.DataTypeArray):
        n = _cexpr(dt.size_expr) if dt.size_expr is not None else str(dt.size)
        return f"{_type(dt.element_type, types)} {name} [{n}]"
    return f"{_type(dt, types)} {name}"


def const_text(e: ir.ExprConstant) -> str:
    """A constant as an SV literal, sized by its width (``4'h0``); an unsized
    one is decimal."""
    v = e.value
    if isinstance(v, bool):
        v = int(v)
    if not isinstance(v, int):
        raise NotImplementedError(f"constant {v!r}")
    w = e.width or 0
    if w <= 0:
        return str(v)
    v &= (1 << w) - 1
    s = "s" if e.signed else ""
    return f"{w}'{s}h{v:x}"


def _signal(sig) -> str:
    if sig is None:
        return ""
    if isinstance(sig, str):
        return sig
    if isinstance(sig, ir.ExprConstant):
        if not sig.width and isinstance(sig.value, int) and not isinstance(sig.value, bool):
            return str(sig.value)
        return const_text(sig)
    if isinstance(sig, ir.ExprHierarchical):
        return ".".join(el.name if el.subscript is None
                        else f"{el.name}[{_cexpr(el.subscript)}]" for el in sig.elements)
    if isinstance(sig, ir.Expr):
        return _cexpr(sig)
    raise NotImplementedError(f"connection signal {type(sig).__name__}")


def _param(v, types: Optional[ValueTypes] = None) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, bool):
        return str(int(v))
    if isinstance(v, int):
        return str(v)
    if isinstance(v, ir.DataType):
        return _value_text(v, types)
    if isinstance(v, ir.ExprStaticRef):        # an enum label: pkg::LABEL
        return ("::" if v.is_global else "") + "::".join(v.path)
    if isinstance(v, ir.ExprConstant) and isinstance(v.value, str):
        return '"' + v.value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(v, ir.ExprConstant):
        if isinstance(v.value, int) and not v.width:
            return str(int(v.value))
        return const_text(v)
    if isinstance(v, ir.Expr):
        return _cexpr(v)
    raise NotImplementedError(f"parameter value {type(v).__name__}")


class StructuralEmitter:
    """Writes one flat structural component as a module."""

    def __init__(self, comp: ir.DataTypeComponent, types: Optional[ValueTypes] = None):
        self.comp = comp
        self.types = types

    def module(self) -> str:
        c = self.comp
        tps = getattr(c, "template_params", None) or []
        if tps:
            lines: List[str] = [f"module {sv_module_name(c.name)} #("]
            ps = []
            for tp in tps:
                if isinstance(tp, ir.TemplateParamType):
                    d = f" = {_value_text(tp.default_value, self.types)}" \
                        if tp.default_value is not None else ""
                    ps.append(f"  parameter type {tp.name}{d}")
                else:
                    d = f" = {_cexpr(tp.default_value)}" if tp.default_value is not None else ""
                    ps.append(f"  parameter {_value_type(tp.value_type, self.types)} "
                              f"{tp.name}{d}")
            lines.append(",\n".join(ps))
            lines.append(") (")
        else:
            lines = [f"module {sv_module_name(c.name)}("]
        ports = []
        for f in c.fields:
            if isinstance(f, ir.FieldInOut):
                d = "inout" if f.is_inout else ("output" if f.is_out else "input")
                ports.append(f"  {d} {_decl(f.datatype, f.name, self.types)}")
        lines.append(",\n".join(ports))
        lines.append(");")
        lines.append("")
        nets = [f for f in c.fields if not isinstance(f, ir.FieldInOut)]
        for f in nets:
            lines.append(f"  {_decl(f.datatype, f.name, self.types)};")
        if nets:
            lines.append("")
        for a in c.net_assigns:
            lines.append(f"  assign {_signal(a.lhs)} = {_signal(a.rhs)};")
        if c.net_assigns:
            lines.append("")
        for inst in c.module_instances:
            lines.extend(self._instance(inst, "  "))
            lines.append("")
        for g in getattr(c, "generates", None) or []:
            lines.extend(self._generate(g, "  "))
            lines.append("")
        lines.extend(proc_block_lines(c))
        lines.append("endmodule")
        return "\n".join(lines) + "\n"

    def _instance(self, inst: ir.ModuleInstance, ind: str) -> List[str]:
        # A connection with no signal is an empty ``.pin()``, the standard
        # form of an unconnected pin. The flattening pass leaves one only
        # for an open extern pin whose type is unknown; any other output
        # nothing reads has an ``_unused`` net.
        lines = []
        if inst.parameters:
            ps = ", ".join(f".{k}({_param(v, self.types)})" for k, v in inst.parameters.items())
            lines.append(f"{ind}{inst.module} #({ps}) {inst.name} (")
        else:
            lines.append(f"{ind}{inst.module} {inst.name} (")
        lines.append(",\n".join(f"{ind}  .{pc.port}({_signal(pc.signal)})"
                                for pc in inst.connections))
        lines.append(f"{ind});")
        return lines

    def _items(self, items, ind: str) -> List[str]:
        lines = []
        for it in items:
            if isinstance(it, ir.Field):
                lines.append(f"{ind}{_decl(it.datatype, it.name, self.types)};")
            elif isinstance(it, ir.NetAssign):
                lines.append(f"{ind}assign {_signal(it.lhs)} = {_signal(it.rhs)};")
            elif isinstance(it, ir.ModuleInstance):
                lines.extend(self._instance(it, ind))
            elif isinstance(it, (ir.GenerateFor, ir.GenerateIf)):
                lines.extend(self._generate(it, ind))
            else:
                raise NotImplementedError(f"{type(it).__name__} in a generate block")
        return lines

    def _generate(self, g, ind: str) -> List[str]:
        if isinstance(g, ir.GenerateFor):
            label = f" : {g.label}" if g.label else ""
            v = g.var
            lines = [f"{ind}for (genvar {v} = {_cexpr(g.start)}; {_cexpr(g.cond)}; "
                     f"{v} = {v} + 1) begin{label}"]
            lines.extend(self._items(g.items, ind + "  "))
            lines.append(f"{ind}end")
            return lines
        label = f" : {g.label}" if g.label else ""
        lines = [f"{ind}if ({_cexpr(g.cond)}) begin{label}"]
        lines.extend(self._items(g.items, ind + "  "))
        if g.orelse:
            el = f" : {g.label}_else" if g.label else ""
            lines.append(f"{ind}end else begin{el}")
            lines.extend(self._items(g.orelse, ind + "  "))
        lines.append(f"{ind}end")
        return lines


def default_types_package(type_m: Dict[str, ir.DataType]) -> str:
    """``<top>_types_pkg``: ``<top>`` the module of the one flat structural
    component that no other instantiates; ``zuspec_types_pkg`` when there
    is not exactly one."""
    comps = [t for t in type_m.values() if isinstance(t, ir.DataTypeComponent)
             and not isinstance(t, ir.DataTypeExtern) and getattr(t, "flat_structural", False)]
    inst: set = set()

    def walk(items):
        for x in items:
            if isinstance(x, ir.ModuleInstance):
                inst.add(x.module)
            elif isinstance(x, (ir.GenerateFor, ir.GenerateIf)):
                walk(x.items)
                walk(getattr(x, "orelse", []) or [])
    for c in comps:
        walk(c.module_instances)
        walk(getattr(c, "generates", None) or [])
    roots = [sv_module_name(c.name) for c in comps if sv_module_name(c.name) not in inst]
    return f"{roots[0]}_types_pkg" if len(roots) == 1 else "zuspec_types_pkg"


def dependency_order(ctxt: ir.Context) -> List[str]:
    """Generated components' type names (``type_m`` keys), each after every
    component it instantiates (by module name). Externs and other types are
    left out."""
    comps = {k: t for k, t in ctxt.type_m.items()
             if isinstance(t, ir.DataTypeComponent) and not isinstance(t, ir.DataTypeExtern)}
    by_mod = {sv_module_name(t.name): k for k, t in comps.items()}
    out: List[str] = []
    state: Dict[str, int] = {}

    def visit(k: str):
        if state.get(k) == 2:
            return
        if state.get(k) == 1:
            raise ValueError(f"instance cycle through '{k}'")
        state[k] = 1
        for mi in comps[k].module_instances:
            child = by_mod.get(mi.module)
            if child is not None:
                visit(child)
        state[k] = 2
        out.append(k)

    for k in comps:
        visit(k)
    return out


PathLike = Union[str, Path]


def write_filelist(path: PathLike, generated_files: Sequence[PathLike],
                   extern_files: Iterable[PathLike] = (),
                   ctxt: Optional[ir.Context] = None) -> Path:
    """Write a filelist, one path per line: ``extern_files`` first, as
    given, then ``generated_files``. With ``ctxt``, a generated module's
    file (named ``<sv_module_name(type key)>.sv``, as SVGenerator writes
    it) follows the files of the modules it instantiates; other generated
    files (packages) come first, in the given order."""
    gen = [Path(p) for p in generated_files]
    if ctxt is not None:
        rank = {sv_module_name(k): i for i, k in enumerate(dependency_order(ctxt))}
        gen = sorted(gen, key=lambda p: (p.stem in rank, rank.get(p.stem, -1)))
    lines = [str(p) for p in extern_files] + [str(p) for p in gen]
    path = Path(path)
    path.write_text("\n".join(lines) + "\n")
    return path

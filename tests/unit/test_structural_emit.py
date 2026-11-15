"""Flat structural emission: flatten structural IR (zuspec-synth), emit SV,
lint the result with verilator."""
import shutil
import subprocess

import pytest

import zuspec.ir.core as ir
from zuspec.be.sv import SVGenerator
from zuspec.be.sv.structural import (StructuralEmitter, is_flat_structural, sv_module_name,
                                     write_filelist)

flatten_structure = pytest.importorskip("zuspec.synth.passes.struct_flatten").flatten_structure

R = ir.BundleRole


def u(bits):
    return ir.DataTypeInt(bits=bits, signed=False)


def inp(name, bits=1):
    return ir.FieldInOut(name=name, datatype=u(bits), is_out=False)


def outp(name, bits=1):
    return ir.FieldInOut(name=name, datatype=u(bits), is_out=True)


def bundle(name, role, btype="rv"):
    return ir.Field(name=name, datatype=ir.DataTypeRef(ref_name=btype), role=role)


def child(name, tname):
    return ir.Field(name=name, datatype=ir.DataTypeRef(ref_name=tname))


def hp(path):
    return ir.ExprHierarchical(elements=[ir.ExprHierarchicalElem(name=n) for n in path.split(".")])


def bind(a, b):
    return ir.Bind(lhs=hp(a), rhs=hp(b) if isinstance(b, str) else b)


def comp(name, fields, binds=()):
    return ir.DataTypeComponent(name=name, super=None, fields=list(fields), bind_map=list(binds))


def rv_bundle():
    return ir.DataTypeBundle(name="rv", super=None, fields=[
        outp("valid"), inp("ready"), outp("data", 8)])


def design():
    """(a) a pass-through rv hierarchy and (c) an extern child, under one top."""
    stage = comp("stage", [bundle("in", R.MIRROR), bundle("out", R.INITIATOR)],
                 [bind("in", "out")])
    mid = comp("mid", [bundle("in", R.MIRROR), bundle("out", R.INITIATOR), child("s", "stage")],
               [bind("in", "s.in"), bind("s.out", "out")])
    fifo = ir.DataTypeExtern(
        name="rv_fifo#(bit[7:0])", super=None, extern_name="fw_rv_fifo",
        fields=[inp("clk"), bundle("in", R.MIRROR), bundle("out", R.INITIATOR),
                inp("en"), outp("cnt", 4)],
        extern_params={"WIDTH": ir.ExprConstant(value=8), "DEPTH": ir.ExprConstant(value=2)},
        pins=[ir.ExternPin(name="clk", path=["clk"]),
              ir.ExternPin(name="in_vld", path=["in", "valid"]),
              ir.ExternPin(name="in_rdy", path=["in", "ready"]),
              ir.ExternPin(name="in_dat", path=["in", "data"]),
              ir.ExternPin(name="out_vld", path=["out", "valid"]),
              ir.ExternPin(name="out_rdy", path=["out", "ready"]),
              ir.ExternPin(name="out_dat", path=["out", "data"]),
              ir.ExternPin(name="cfg", tie=ir.ExprConstant(value=0, width=4)),
              ir.ExternPin(name="en", path=["en"]),
              ir.ExternPin(name="dbg", open=True, datatype=u(1)),
              ir.ExternPin(name="cnt_o", path=["cnt"])])
    top = comp("top", [inp("clk"), bundle("in", R.MIRROR), bundle("out", R.INITIATOR),
                       outp("k", 3), child("u[0]", "mid"), child("u[1]", "stage"),
                       child("f", fifo.name)],
               [bind("clk", "f.clk"),
                bind("in", "u[0].in"), bind("u[0].out", "u[1].in"),
                bind("u[1].out", "f.in"), bind("f.out", "out"),
                bind("f.en", ir.ExprConstant(value=1, width=1)),
                bind("k", ir.ExprConstant(value=5, width=3))])
    c = ir.Context()
    for t in (rv_bundle(), stage, mid, fifo, top):
        c.type_m[t.name] = t
    return c


STUB = """\
/* verilator lint_off UNUSEDPARAM */
/* verilator lint_off UNUSEDSIGNAL */
module fw_rv_fifo #(parameter int WIDTH = 8, parameter int DEPTH = 2) (
  input  logic             clk,
  input  logic             in_vld,
  output logic             in_rdy,
  input  logic [WIDTH-1:0] in_dat,
  output logic             out_vld,
  input  logic             out_rdy,
  output logic [WIDTH-1:0] out_dat,
  input  logic [3:0]       cfg,
  input  logic             en,
  output logic             dbg,
  output logic [3:0]       cnt_o
);
  logic unused;
  assign unused = clk ^ ^cfg ^ en;
  assign in_rdy = out_rdy;
  assign out_vld = in_vld;
  assign out_dat = in_dat;
  assign dbg = 1'b0;
  assign cnt_o = 4'h0;
endmodule
/* verilator lint_on UNUSEDSIGNAL */
/* verilator lint_on UNUSEDPARAM */
"""


def _generate(tmp_path):
    c = design()
    flatten_structure(c)
    for k in ("stage", "mid", "top"):
        assert is_flat_structural(c.type_m[k])
    files = SVGenerator(tmp_path / "rtl").generate(c)
    return c, files


def test_emit_text(tmp_path):
    c, files = _generate(tmp_path)
    assert sorted(f.name for f in files) == ["mid.sv", "stage.sv", "top.sv"]
    top = (tmp_path / "rtl" / "top.sv").read_text()
    assert top.startswith("module top(\n  input logic clk,\n  input logic in_valid,\n")
    assert "  input logic [7:0] in_data,\n" in top
    assert "  output logic [2:0] k\n);" in top
    assert "  logic u_0_out_valid;\n" in top and "  logic [7:0] u_1_out_data;\n" in top
    assert "  assign k = 3'h5;\n" in top
    assert "  fw_rv_fifo #(.WIDTH(8), .DEPTH(2)) f (\n" in top
    # Outputs nothing reads (an open pin, an unconnected port) each get a
    # net named *_unused: connected, intent in the name, no lint pragma.
    assert "    .cfg(4'h0),\n    .en(1'h1),\n    .dbg(f_dbg_unused),\n" in top
    assert "    .cnt_o(f_cnt_unused)\n  );\n" in top
    assert "  logic f_cnt_unused;" not in top and "  logic [3:0] f_cnt_unused;\n" in top
    assert "  logic f_dbg_unused;\n" in top
    assert "lint_" not in top
    assert "  mid u_0 (\n    .in_valid(in_valid),\n" in top
    stage = (tmp_path / "rtl" / "stage.sv").read_text()
    assert "  assign out_valid = in_valid;\n" in stage


def test_filelist_order(tmp_path):
    c, files = _generate(tmp_path)
    fl = write_filelist(tmp_path / "files.f", files, [tmp_path / "fw_rv_fifo.sv"], ctxt=c)
    names = [p.rsplit("/", 1)[-1] for p in fl.read_text().split()]
    assert names == ["fw_rv_fifo.sv", "stage.sv", "mid.sv", "top.sv"]


@pytest.mark.skipif(shutil.which("verilator") is None, reason="verilator not on PATH")
def test_verilator_lint(tmp_path):
    c, files = _generate(tmp_path)
    stub = tmp_path / "fw_rv_fifo.sv"
    stub.write_text(STUB)
    fl = write_filelist(tmp_path / "files.f", files, [stub], ctxt=c)
    r = subprocess.run(["verilator", "--lint-only", "-Wall", "--top-module", "top",
                        "-f", str(fl)], capture_output=True, text=True, cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "%Warning" not in r.stderr, r.stderr


def test_module_name_rule():
    assert sv_module_name("fifo_chain#(bit[7:0])") == "fifo_chain__bit_7_0__"
    assert SVGenerator._sanitize_sv_name(None, "a.<locals>.B") == sv_module_name("a.<locals>.B")


def test_same_text_as_generic_path(tmp_path):
    """A component the generic path already emitted (ports, nets, instances
    with string parameters) comes out the same from the structural emitter."""
    comp_ = ir.DataTypeComponent(
        name="top", super=None,
        fields=[inp("clk"), outp("o", 4), ir.Field(name="n", datatype=u(4))],
        module_instances=[ir.ModuleInstance(
            module="fw_rv_fifo", name="u_c1", parameters={"WIDTH": "9", "DEPTH": "2"},
            connections=[ir.PortConnection(port="clk", signal="clk")])])
    assert is_flat_structural(comp_)
    gen = SVGenerator(tmp_path)
    gen._ctxt = ir.Context()
    # The generic path, with the structural dispatch bypassed.
    import zuspec.be.sv.structural as st
    orig = st.is_flat_structural
    st.is_flat_structural = lambda c: False
    try:
        generic = gen._generate_component(comp_)
    finally:
        st.is_flat_structural = orig
    assert StructuralEmitter(comp_).module() == generic


def test_not_flat_structural():
    assert not is_flat_structural(design().type_m["top"])          # bundles, binds
    assert not is_flat_structural(comp("x", [inp("a"), ir.Field(name="n", datatype=u(1))]))


def test_parameterized_flat_text():
    """Template parameters, array nets, generate loops, indexed references
    and parameter expressions (core-layer1.md §6.6)."""
    i, n = ir.ExprRefLocal(name="i"), ir.ExprRefParam(name="n")
    one = ir.ExprConstant(value=1)
    T = ir.DataTypeRef(ref_name="T")

    def el(name, idx):
        return ir.ExprHierarchical(elements=[ir.ExprHierarchicalElem(name=name, subscript=idx)])
    c = ir.DataTypeComponent(
        name="pc", super=None, flat_structural=True,
        template_params=[
            ir.TemplateParamType(name="T", kind=ir.TemplateParamKind.TYPE, default_value=u(8)),
            ir.TemplateParamValue(name="n", kind=ir.TemplateParamKind.VALUE,
                                  value_type=ir.DataTypeInt(bits=32, signed=True),
                                  default_value=ir.ExprConstant(value=2)),
            ir.TemplateParamValue(name="b", kind=ir.TemplateParamKind.VALUE,
                                  value_type=u(1), default_value=ir.ExprConstant(value=0))],
        fields=[ir.FieldInOut(name="d", datatype=T, is_out=False),
                ir.FieldInOut(name="q", datatype=T, is_out=True),
                ir.Field(name="u_d", datatype=ir.DataTypeArray(element_type=T, size=2, size_expr=n)),
                ir.Field(name="u_q", datatype=ir.DataTypeArray(element_type=T, size=2, size_expr=n))],
        net_assigns=[ir.NetAssign(lhs=el("u_d", ir.ExprConstant(value=0)), rhs="d"),
                     ir.NetAssign(lhs="q", rhs=el("u_q", ir.ExprBin(lhs=n, op=ir.BinOp.Sub,
                                                                     rhs=one)))],
        generates=[
            ir.GenerateFor(var="i", start=ir.ExprConstant(value=0), label="u",
                           cond=ir.ExprCompare(left=i, ops=[ir.CmpOp.Lt], comparators=[n]),
                           items=[ir.ModuleInstance(
                               module="stage", name="u",
                               parameters={"W": ir.ExprCall(func=ir.ExprRefUnresolved(name="bits"),
                                                            args=[ir.ExprRefParam(name="T")]),
                                           "D": ir.ExprIfExp(
                                               test=ir.ExprBool(op=ir.BoolOp.And, values=[
                                                   ir.ExprRefParam(name="b"),
                                                   ir.ExprCompare(left=i, ops=[ir.CmpOp.Eq],
                                                                  comparators=[ir.ExprBin(
                                                                      lhs=n, op=ir.BinOp.Sub,
                                                                      rhs=one)])]),
                                               body=ir.ExprConstant(value=4),
                                               orelse=ir.ExprConstant(value=2))},
                               connections=[ir.PortConnection(port="d", signal=el("u_d", i)),
                                            ir.PortConnection(port="q", signal=el("u_q", i))])]),
            ir.GenerateFor(var="i", start=ir.ExprConstant(value=0), label="g_1",
                           cond=ir.ExprCompare(left=ir.ExprBin(lhs=i, op=ir.BinOp.Add, rhs=one),
                                               ops=[ir.CmpOp.Lt], comparators=[n]),
                           items=[ir.NetAssign(lhs=el("u_d", ir.ExprBin(lhs=i, op=ir.BinOp.Add,
                                                                         rhs=one)),
                                               rhs=el("u_q", i))])])
    assert is_flat_structural(c)
    sv = SVGenerator.__new__(SVGenerator)
    sv._ctxt = None
    text = sv._generate_component(c)
    assert text == StructuralEmitter(c).module()
    assert "module pc #(\n  parameter type T = logic [7:0],\n  parameter int n = 2,\n" \
           "  parameter bit b = 0\n) (\n  input T d,\n  output T q\n);" in text
    assert "  T u_d [n];" in text
    assert "  assign u_d[0] = d;\n  assign q = u_q[n-1];" in text
    assert "  for (genvar i = 0; i < n; i = i + 1) begin : u\n" \
           "    stage #(.W($bits(T)), .D((b && (i == (n-1))) ? 4 : 2)) u (\n" \
           "      .d(u_d[i]),\n      .q(u_q[i])\n    );\n  end" in text
    assert "  for (genvar i = 0; (i+1) < n; i = i + 1) begin : g_1\n" \
           "    assign u_d[i+1] = u_q[i];\n  end" in text


def test_generate_if_nets_and_array_ports():
    """A generate if with nets declared in its arms (a child that exists in
    one arm only), unpacked array ports, a width over a value parameter, and
    an element of an array port (core-layer1.md §6.6, M8)."""
    n, w = ir.ExprRefParam(name="n"), ir.ExprRefParam(name="W")
    i = ir.ExprRefLocal(name="i")
    dw = ir.DataTypeInt(bits=8, signed=False, width_expr=w)

    def el(name, idx):
        return ir.ExprHierarchical(elements=[ir.ExprHierarchicalElem(name=name, subscript=idx)])
    c = ir.DataTypeComponent(
        name="cp", super=None, flat_structural=True,
        template_params=[
            ir.TemplateParamValue(name="W", kind=ir.TemplateParamKind.VALUE,
                                  value_type=ir.DataTypeInt(bits=32, signed=True),
                                  default_value=ir.ExprConstant(value=8)),
            ir.TemplateParamValue(name="n", kind=ir.TemplateParamKind.VALUE,
                                  value_type=ir.DataTypeInt(bits=32, signed=True),
                                  default_value=ir.ExprConstant(value=2)),
            ir.TemplateParamValue(name="use_fifo", kind=ir.TemplateParamKind.VALUE,
                                  value_type=u(1), default_value=ir.ExprConstant(value=1))],
        fields=[ir.FieldInOut(name="d", datatype=ir.DataTypeArray(element_type=dw, size=2,
                                                                  size_expr=n), is_out=False),
                ir.FieldInOut(name="q", datatype=ir.DataTypeArray(element_type=dw, size=2,
                                                                  size_expr=n), is_out=True)],
        generates=[ir.GenerateIf(cond=ir.ExprRefParam(name="use_fifo"), label="g_1", items=[
            ir.Field(name="f_d", datatype=ir.DataTypeArray(element_type=dw, size=2, size_expr=n)),
            ir.ModuleInstance(module="fifo", name="f", parameters={"W": w},
                              connections=[ir.PortConnection(port="d", signal="f_d"),
                                           ir.PortConnection(port="q", signal="q")]),
            ir.GenerateFor(var="i", start=ir.ExprConstant(value=0), label="g_2",
                           cond=ir.ExprCompare(left=i, ops=[ir.CmpOp.Lt], comparators=[n]),
                           items=[ir.NetAssign(lhs=el("f_d", i), rhs=el("d", i))])],
            orelse=[ir.NetAssign(lhs="q", rhs="d")])])
    assert is_flat_structural(c)
    text = StructuralEmitter(c).module()
    assert "  input logic [W-1:0] d [n],\n  output logic [W-1:0] q [n]\n);" in text
    assert "  if (use_fifo) begin : g_1\n    logic [W-1:0] f_d [n];\n    fifo #(.W(W)) f (\n" in text
    assert "    for (genvar i = 0; i < n; i = i + 1) begin : g_2\n" \
           "      assign f_d[i] = d[i];\n    end\n" in text
    assert "  end else begin : g_1_else\n    assign q = d;\n  end" in text


# ------------------------------------- packed types, 2/4-state, open (M8)

def _typed_design():
    """A top passing a packed struct (with a nested struct, an enum, and an
    enum the RTL declares) through a generated child to an extern whose
    port is the RTL's own type."""
    from zuspec.ir.core import DataTypeEnum, DataTypeStruct
    mode = DataTypeEnum(name="prm_pkg::mode_e", items={"SLOW": 0, "FAST": 1}, width=1,
                        base_type=ir.DataTypeInt(bits=1, signed=False, four_state=True),
                        extern_name="prm_pkg::mode_e")
    kind = DataTypeEnum(name="d_pkg::kind_e", items={"K_A": 0, "K_B": 2, "K_C": 3}, width=2,
                        base_type=ir.DataTypeInt(bits=2, signed=False, four_state=False))
    hdr = DataTypeStruct(name="d_pkg::hdr_t", super=None, packed=True, fields=[
        ir.Field(name="k", datatype=ir.DataTypeRef(ref_name="d_pkg::kind_e")),
        ir.Field(name="len", datatype=ir.DataTypeInt(bits=4, signed=False, four_state=False))])
    pkt = DataTypeStruct(name="d_pkg::pkt_t", super=None, packed=True, fields=[
        ir.Field(name="h", datatype=ir.DataTypeRef(ref_name="d_pkg::hdr_t")),
        ir.Field(name="m", datatype=ir.DataTypeRef(ref_name="prm_pkg::mode_e")),
        ir.Field(name="v", datatype=ir.DataTypeInt(bits=1, signed=False, four_state=True))])
    unused = DataTypeEnum(name="d_pkg::other_e", items={"X": 0}, width=1)   # no port uses it
    P = ir.DataTypeRef(ref_name="d_pkg::pkt_t")
    M = ir.DataTypeRef(ref_name="prm_pkg::mode_e")
    T = ir.DataTypeRef(ref_name="T")
    stage = comp("stage", [ir.FieldInOut(name="i", datatype=T, is_out=False),
                           ir.FieldInOut(name="o", datatype=T, is_out=True)],
                 [bind("i", "o")])
    stage.template_params = [ir.TemplateParamType(name="T", kind=ir.TemplateParamKind.TYPE,
                                                  default_value=ir.DataTypeInt(
                                                      bits=8, signed=False, four_state=False))]
    ext = ir.DataTypeExtern(name="mx", super=None, extern_name="mode_rtl",
                            fields=[ir.FieldInOut(name="m", datatype=M, is_out=False)],
                            pins=[ir.ExternPin(name="m", path=["m"]),
                                  ir.ExternPin(name="dbg", open=True)])
    top = comp("ttop", [ir.FieldInOut(name="i", datatype=P, is_out=False),
                        ir.FieldInOut(name="o", datatype=P, is_out=True),
                        ir.FieldInOut(name="m", datatype=M, is_out=False),
                        ir.Field(name="s", datatype=ir.DataTypeRef(ref_name="stage"),
                                 inst_params={"T": P}),
                        child("x", "mx")],
               [bind("i", "s.i"), bind("s.o", "o"), bind("m", "x.m")])
    c = ir.Context()
    for t in (mode, kind, hdr, pkt, unused, stage, ext, top):
        c.type_m[t.name] = t
    return c


def test_types_package(tmp_path):
    """One generated package, first, declaring each type the components use
    once, dependencies first; a type the RTL declares is referred to by its
    own name; ports and type parameters use the package's names."""
    c = _typed_design()
    flatten_structure(c)
    files = SVGenerator(tmp_path / "rtl").generate(c)
    assert files[0].name == "ttop_types_pkg.sv"
    pkg = files[0].read_text()
    assert pkg == (
        "package ttop_types_pkg;\n\n"
        "  typedef enum bit [1:0] {\n    K_A = 2'd0,\n    K_B = 2'd2,\n    K_C = 2'd3\n"
        "  } kind_e;\n\n"
        "  typedef struct packed {\n    kind_e k;\n    bit [3:0] len;\n  } hdr_t;\n\n"
        "  typedef struct packed {\n    hdr_t h;\n    prm_pkg::mode_e m;\n    logic v;\n"
        "  } pkt_t;\n\nendpackage : ttop_types_pkg\n")
    top = (tmp_path / "rtl" / "ttop.sv").read_text()
    assert "  input ttop_types_pkg::pkt_t i,\n" in top
    assert "  input prm_pkg::mode_e m\n" in top
    assert "  stage #(.T(ttop_types_pkg::pkt_t)) s (\n" in top
    stage = (tmp_path / "rtl" / "stage.sv").read_text()
    assert "  parameter type T = bit [7:0]\n" in stage
    # An open pin of unknown type stays an empty connection, with no pragma.
    assert "    .dbg()\n" in top and "lint_" not in top
    fl = write_filelist(tmp_path / "files.f", files, [tmp_path / "prm.sv"], ctxt=c)
    names = [p.rsplit("/", 1)[-1] for p in fl.read_text().split()]
    assert names == ["prm.sv", "ttop_types_pkg.sv", "stage.sv", "ttop.sv"]


def test_types_package_name_and_collisions():
    from zuspec.be.sv.structural import ValueTypes
    from zuspec.ir.core import DataTypeStruct
    a = DataTypeStruct(name="p::s_t", super=None, packed=True,
                       fields=[ir.Field(name="x", datatype=u(2))])
    b = DataTypeStruct(name="q::s_t", super=None, packed=True,
                       fields=[ir.Field(name="y", datatype=u(3))])
    top = ir.DataTypeComponent(name="t", super=None, flat_structural=True, fields=[
        ir.FieldInOut(name="a", datatype=ir.DataTypeRef(ref_name="p::s_t"), is_out=False),
        ir.FieldInOut(name="b", datatype=ir.DataTypeRef(ref_name="q::s_t"), is_out=False)])
    vt = ValueTypes({"p::s_t": a, "q::s_t": b, "t": top}, "my_pkg")
    assert vt.sv("p::s_t") == "my_pkg::s_t" and vt.sv("q::s_t") == "my_pkg::q__s_t"
    assert vt.sv("t") is None
    from zuspec.be.sv.structural import default_types_package
    assert default_types_package({"t": top}) == "t_types_pkg"


def test_two_and_four_state():
    """A type as a value follows ``four_state`` (``bit`` when two-state);
    ports and nets stay ``logic``."""
    from zuspec.be.sv.structural import _param
    c = ir.DataTypeComponent(
        name="ts", super=None, flat_structural=True,
        template_params=[
            ir.TemplateParamType(name="A", kind=ir.TemplateParamKind.TYPE,
                                 default_value=ir.DataTypeInt(bits=12, signed=False,
                                                              four_state=False)),
            ir.TemplateParamType(name="B", kind=ir.TemplateParamKind.TYPE,
                                 default_value=ir.DataTypeInt(bits=12, signed=False,
                                                              four_state=True)),
            ir.TemplateParamType(name="C", kind=ir.TemplateParamKind.TYPE, default_value=u(12)),
            ir.TemplateParamValue(name="K", kind=ir.TemplateParamKind.VALUE,
                                  value_type=ir.DataTypeInt(bits=4, signed=False,
                                                            four_state=False),
                                  default_value=ir.ExprConstant(value=3)),
            ir.TemplateParamValue(name="L", kind=ir.TemplateParamKind.VALUE,
                                  value_type=ir.DataTypeInt(bits=4, signed=False,
                                                            four_state=True),
                                  default_value=ir.ExprConstant(value=3)),
            ir.TemplateParamValue(name="N", kind=ir.TemplateParamKind.VALUE,
                                  value_type=ir.DataTypeInt(bits=32, signed=True,
                                                            four_state=False),
                                  default_value=ir.ExprConstant(value=3))],
        fields=[ir.FieldInOut(name="d", is_out=False,
                              datatype=ir.DataTypeInt(bits=12, signed=False, four_state=False))])
    text = StructuralEmitter(c).module()
    assert "  parameter type A = bit [11:0],\n  parameter type B = logic [11:0],\n" \
           "  parameter type C = logic [11:0],\n  parameter bit [3:0] K = 3,\n" \
           "  parameter logic [3:0] L = 3,\n  parameter int N = 3\n" in text
    assert "  input logic [11:0] d\n" in text
    assert _param(ir.DataTypeInt(bits=12, signed=False, four_state=False)) == "bit [11:0]"


PRM_RTL = """\
package prm_pkg;
  typedef enum logic {SLOW, FAST} mode_e;
endpackage
/* verilator lint_off UNUSEDSIGNAL */
module mode_rtl import prm_pkg::*; (input mode_e m, output logic [1:0] dbg);
  assign dbg = 2'b0;
endmodule
/* verilator lint_on UNUSEDSIGNAL */
"""


@pytest.mark.skipif(shutil.which("verilator") is None, reason="verilator not on PATH")
def test_types_package_lint(tmp_path):
    """The package, the modules and the RTL (declaring the shared enum)
    compile together, lint-clean but for the open pin of unknown type."""
    c = _typed_design()
    flatten_structure(c)
    files = SVGenerator(tmp_path / "rtl").generate(c)
    rtl = tmp_path / "prm.sv"
    rtl.write_text(PRM_RTL)
    fl = write_filelist(tmp_path / "files.f", files, [rtl], ctxt=c)
    r = subprocess.run(["verilator", "--lint-only", "-Wall", "-Wno-DECLFILENAME",
                        "--top-module", "ttop", "-f", str(fl)],
                       capture_output=True, text=True, cwd=tmp_path)
    warns = [ln for ln in r.stderr.splitlines() if ln.startswith("%Warning")]
    assert all("PINCONNECTEMPTY" in w for w in warns) and len(warns) == 1, r.stderr

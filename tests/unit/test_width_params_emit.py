"""Zuspec's TypeParamWidthPass (zuspec.ir.core.xf) before be-sv: a
parameterized hierarchy with type parameters emits ``parameter int`` widths
only, which Verilator compiles and yosys ``read -formal`` (no ``parameter
type``) reads."""
import shutil
import subprocess

import pytest

import zuspec.ir.core as ir
from zuspec.be.sv import SVGenerator
from zuspec.be.sv.structural import write_filelist
from zuspec.ir.core.xf import lower_type_params_to_widths

K = ir.TemplateParamKind


def u(bits):
    return ir.DataTypeInt(bits=bits, signed=False)


def port(name, dt, out):
    return ir.FieldInOut(name=name, datatype=dt, is_out=out)


def design():
    """``top`` -> ``mid #(.U(logic [15:0]))`` -> ``leaf #(.T(U))``; ``mid``'s
    ``N`` defaults to ``$bits(U)`` and is passed to ``leaf``'s ``D``."""
    T, U = ir.DataTypeRef(ref_name="T"), ir.DataTypeRef(ref_name="U")
    bits_u = ir.ExprCall(func=ir.ExprRefUnresolved(name="bits"), args=[ir.ExprRefParam(name="U")])
    leaf = ir.DataTypeComponent(
        name="leaf", super=None, flat_structural=True,
        template_params=[ir.TemplateParamType(name="T", kind=K.TYPE, default_value=u(8)),
                         ir.TemplateParamValue(name="D", kind=K.VALUE,
                                               value_type=ir.DataTypeInt(bits=32, signed=True),
                                               default_value=ir.ExprConstant(value=1))],
        fields=[port("d", T, False), port("q", T, True)],
        net_assigns=[ir.NetAssign(lhs="q", rhs="d")])
    mid = ir.DataTypeComponent(
        name="mid", super=None, flat_structural=True,
        template_params=[ir.TemplateParamType(name="U", kind=K.TYPE, default_value=u(4)),
                         ir.TemplateParamValue(name="N", kind=K.VALUE,
                                               value_type=ir.DataTypeInt(bits=32, signed=True),
                                               default_value=bits_u)],
        fields=[port("a", U, False), port("b", U, True), ir.Field(name="w", datatype=U)],
        net_assigns=[ir.NetAssign(lhs="b", rhs="w")],
        module_instances=[ir.ModuleInstance(
            module="leaf", name="l", parameters={"T": U, "D": ir.ExprRefParam(name="N")},
            connections=[ir.PortConnection(port="d", signal="a"),
                         ir.PortConnection(port="q", signal="w")])])
    top = ir.DataTypeComponent(
        name="top", super=None, flat_structural=True,
        fields=[port("x", u(16), False), port("y", u(16), True)],
        module_instances=[ir.ModuleInstance(
            module="mid", name="m", parameters={"U": u(16)},
            connections=[ir.PortConnection(port="a", signal="x"),
                         ir.PortConnection(port="b", signal="y")])])
    c = ir.Context()
    for t in (leaf, mid, top):
        c.type_m[t.name] = t
    return c


def _generate(tmp_path):
    c = design()
    rep = lower_type_params_to_widths(c)
    assert sorted(w.width_param for w in rep.lowered) == ["T_W", "U_W"] and rep.kept == []
    files = SVGenerator(tmp_path / "rtl").generate(c)
    return c, files


def test_emit_text(tmp_path):
    _, files = _generate(tmp_path)
    text = {f.name: f.read_text() for f in files}
    assert all("parameter type" not in t for t in text.values()), text
    assert "  parameter int T_W = 8,\n  parameter int D = 1\n" in text["leaf.sv"]
    assert "input logic [T_W-1:0] d," in text["leaf.sv"]
    assert "  parameter int U_W = 4,\n  parameter int N = U_W\n" in text["mid.sv"]
    assert "  logic [U_W-1:0] w;" in text["mid.sv"]
    assert "leaf #(.T_W(U_W), .D(N)) l (" in text["mid.sv"]
    assert "mid #(.U_W(16)) m (" in text["top.sv"]


@pytest.mark.skipif(shutil.which("verilator") is None, reason="verilator not on PATH")
def test_verilator_lint(tmp_path):
    c, files = _generate(tmp_path)
    fl = write_filelist(tmp_path / "files.f", files, [], ctxt=c)
    r = subprocess.run(["verilator", "--lint-only", "-Wall", "-Wno-UNUSEDPARAM",
                        "--top-module", "top", "-f", str(fl)],
                       capture_output=True, text=True, cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "%Warning" not in r.stderr, r.stderr


@pytest.mark.skipif(shutil.which("yosys") is None, reason="yosys not on PATH")
def test_yosys_read_formal(tmp_path):
    c, files = _generate(tmp_path)
    fl = write_filelist(tmp_path / "files.f", files, [], ctxt=c)
    srcs = " ".join(fl.read_text().split())
    r = subprocess.run(["yosys", "-q", "-p", f"read -formal {srcs}; prep -top top; "
                        "select -assert-count 1 top/m"],
                       capture_output=True, text=True, cwd=tmp_path)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]

"""``translate_ir_package``: an IR ``Package`` (constants, functions and
types in source order), class-scope typedefs over a struct-valued
parameter, member-type references, package-member references and
attributes (fw-hdl protocol-kit-followups §18)."""
import shutil
import subprocess

import pytest

import zuspec.ir.core as ir
from zuspec.be.sv.ir.core_to_sv import translate_ir_package
from zuspec.be.sv.ir.sv_emit import SVEmitter


def _c(v):
    return ir.ExprConstant(value=v)


def _u32():
    return ir.DataTypeInt(bits=32, signed=False)


def _ann(name, value=1):
    return ir.Annotation(type_qname=name, values={"value": value})


def _defs():
    prm_t = ir.DataTypeStruct(super=None, packed=True, fields=[
        ir.Field(name="ADDR_W", datatype=_u32()), ir.Field(name="ID_W", datatype=_u32())])
    w = ir.ExprRefLocal(name="w")
    w1 = ir.Function(
        name="w1", metadata={"lifetime": "automatic"},
        args=ir.Arguments(args=[ir.Arg(arg="w", datatype=_u32())]), returns=_u32(),
        body=[ir.StmtReturn(value=ir.ExprIfExp(
            test=ir.ExprBin(lhs=w, op=ir.BinOp.Gt, rhs=_c(0)), body=w, orelse=_c(1)))])
    pkg = ir.Package(name="defs_pkg", items=[
        ir.PackageType(name="defs_pkg::prm_t"),
        ir.PackageConst(name="DEF", datatype=ir.DataTypeRef(ref_name="defs_pkg::prm_t"),
                        value=ir.ExprStructLiteral(fields=[
                            ir.ExprStructField(name="ADDR_W", value=_c(32)),
                            ir.ExprStructField(name="ID_W", value=_c(4))])),
        w1])
    return pkg, {"defs_pkg::prm_t": prm_t}


def _core():
    p = ir.ExprRefParam(name="P")
    width = ir.ExprCall(func=ir.ExprRefPackage(package="defs_pkg", name="w1"),
                        args=[ir.ExprAttribute(value=p, attr="ID_W")])
    addr_w = ir.ExprAttribute(value=p, attr="ADDR_W")
    id_f = ir.Field(name="id", datatype=ir.DataTypeInt(bits=4, signed=False, four_state=True,
                                                        width_expr=width))
    id_f.annotations = [_ann("fw_opt", "ID_W")]
    aw_t = ir.DataTypeStruct(super=None, packed=True, fields=[
        id_f,
        ir.Field(name="addr", datatype=ir.DataTypeInt(bits=32, signed=False, four_state=True,
                                                       width_expr=addr_w))])
    p_param = ir.TemplateParamValue(
        name="P", kind=ir.TemplateParamKind.VALUE,
        value_type=ir.DataTypeRef(ref_name="defs_pkg::prm_t"),
        default_value=ir.ExprRefPackage(package="defs_pkg", name="DEF"))
    types = ir.DataTypeParameterized(
        name="types", template_params=[p_param],
        body=ir.DataTypeClass(name="types", super=None,
                              typedefs=[ir.TypeDef(name="aw_t", datatype=aw_t)]))
    spec = ir.DataTypeSpecialized(name="types", base_template=types, template_args=[
        ir.TemplateArgValue(param_name="P", value_expr=p)])
    api_body = ir.DataTypeProtocol(
        name="req_if",
        typedefs=[ir.TypeDef(name="aw_t", datatype=ir.DataTypeMemberRef(base=spec, member="aw_t"))],
        methods=[
            ir.Function(name="put", is_async=True, args=ir.Arguments(args=[
                ir.Arg(arg="aw", datatype=ir.DataTypeRef(ref_name="aw_t"))])),
            ir.Function(name="ready", returns=ir.DataTypeInt(bits=1, signed=False))])
    api = ir.DataTypeParameterized(name="req_if", template_params=[p_param], body=api_body)
    api.annotations = [_ann("fw_api")]
    bundle_body = ir.DataTypeClass(
        name="bundle", super=None,
        typedefs=[ir.TypeDef(name="t", datatype=spec)],
        fields=[ir.Field(name="aw", datatype=ir.DataTypeMemberRef(
            base=ir.DataTypeRef(ref_name="t"), member="aw_t"))])
    bundle = ir.DataTypeParameterized(name="bundle", template_params=[p_param], body=bundle_body)
    bundle.annotations = [_ann("fw_proto_bundle", "x"), _ann("fw_roles", "mgr,sub")]
    pkg = ir.Package(name="core_pkg", items=[
        ir.PackageImport(package="defs_pkg"),
        ir.PackageType(name="core_pkg::types"),
        ir.PackageType(name="core_pkg::bundle"),
        ir.PackageType(name="core_pkg::req_if")])
    return pkg, [types, bundle, api]


@pytest.fixture(scope="module")
def emitted():
    dp, dt = _defs()
    cp, decls = _core()
    return (SVEmitter().emit_one(translate_ir_package(dp, dt, [])),
            SVEmitter().emit_one(translate_ir_package(cp, {}, decls)))


def test_defs_package(emitted):
    defs, _ = emitted
    assert defs.index("} prm_t;") < defs.index("localparam prm_t DEF = '{ADDR_W: 32, ID_W: 4};")
    assert "function automatic bit [31:0] w1(input bit [31:0] w);" in defs
    assert "virtual" not in defs


def test_core_package(emitted):
    _, core = emitted
    assert "import defs_pkg::*;" in core
    # imported package members are written unqualified, as in the source
    assert "class types #(prm_t P = DEF);" in core
    assert "(* fw_opt = \"ID_W\" *) logic [w1(P.ID_W)-1:0] id;" in core
    assert "logic [P.ADDR_W-1:0] addr;" in core
    assert "typedef types #(P) t;" in core
    assert "t::aw_t aw;" in core
    assert "(* fw_proto_bundle = \"x\", fw_roles = \"mgr,sub\" *)\n  class bundle #(" in core
    assert "(* fw_api *)\n  interface class req_if #(prm_t P = DEF);" in core
    assert "typedef types #(P)::aw_t aw_t;" in core
    # source order: types, bundle, req_if
    assert core.index("class types") < core.index("class bundle") < core.index("class req_if")
    assert core.index("task put") < core.index("function bit ready")


def test_qualified_outside_package():
    from zuspec.be.sv.ir.expr_emit import SVExprEmitter
    assert SVExprEmitter().emit(ir.ExprRefPackage(package="p", name="x")) == "p::x"


@pytest.mark.skipif(shutil.which("verilator") is None, reason="verilator not on PATH")
def test_verilator_lint(emitted, tmp_path):
    files = []
    for name, text in zip(("defs_pkg", "core_pkg"), emitted):
        f = tmp_path / f"{name}.sv"
        f.write_text(text + "\n")
        files.append(str(f))
    r = subprocess.run(
        ["verilator", "--lint-only", "-Wall", "-Wno-UNUSEDSIGNAL", "-Wno-UNDRIVEN",
         "-Wno-UNUSEDPARAM", *files],
        capture_output=True, text=True, cwd=tmp_path)
    assert r.returncode == 0, r.stderr + "\n" + "\n".join(emitted)

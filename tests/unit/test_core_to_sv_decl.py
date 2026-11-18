"""core_to_sv: class parameters, interface classes and method signatures (K5.3)."""
import zuspec.ir.core as ir
from zuspec.ir.core.base import Annotation

from zuspec.be.sv.ir.core_to_sv import (
    sv_type_str, sv_decl_type, translate_decl, translate_method)
from zuspec.be.sv.ir.sv import SVClass, SVInterfaceClass, SVTaskDecl
from zuspec.be.sv.ir.sv_emit import SVEmitter

INT = ir.DataTypeInt(bits=32, signed=True)
S_T = ir.DataTypeRef(ref_name="s_t")


def _args(*args):
    return ir.Arguments(args=list(args))


def _put(**kw):
    return ir.Function(
        name="put", is_async=True,
        args=_args(
            ir.Arg(arg="t", datatype=S_T, direction=ir.SignalDirection.INPUT),
            ir.Arg(arg="o", datatype=ir.DataTypeInt(bits=4, signed=False),
                   direction=ir.SignalDirection.OUTPUT),
            ir.Arg(arg="q", datatype=ir.DataTypeList(element_type=S_T),
                   direction=ir.SignalDirection.INOUT)),
        annotations=[Annotation(type_qname="fw_default", values={"value": 1})],
        **kw)


def _get(**kw):
    return ir.Function(name="get", args=_args(),
                       returns=ir.DataTypeInt(bits=8, signed=False), **kw)


def _template():
    w = ir.DataTypeInt(bits=8, signed=False,
                       width_expr=ir.ExprRefParam(name="W"))
    body = ir.DataTypeClass(
        name="gen_c", super=ir.DataTypeRef(ref_name="base_c"),
        implements=["api_if"],
        fields=[ir.Field(name="v", datatype=ir.DataTypeRef(ref_name="T")),
                ir.Field(name="w", datatype=w, annotations=[
                    Annotation(type_qname="fw_opt", values={"value": 2})])],
        functions=[_put(), _get(body=[ir.StmtReturn(
            value=ir.ExprConstant(value=0))])])
    return ir.DataTypeParameterized(
        name="gen_c", body=body, template_params=[
            ir.TemplateParamType(name="T", kind=ir.TemplateParamKind.TYPE,
                                 default_value=INT),
            ir.TemplateParamValue(name="W", kind=ir.TemplateParamKind.VALUE,
                                  value_type=INT,
                                  default_value=ir.ExprConstant(value=8))])


def test_protocol_to_interface_class():
    proto = ir.DataTypeProtocol(name="api_if", methods=[_put(), _get()])
    ic = translate_decl(proto)
    assert isinstance(ic, SVInterfaceClass)
    text = SVEmitter().emit_one(ic)
    assert text.splitlines()[0] == "interface class api_if;"
    assert ("(* fw_default *) pure virtual task put(input s_t t, "
            "output bit [3:0] o, inout s_t q [$]);") in text
    assert "pure virtual function bit [7:0] get();" in text


def test_method_task_vs_function():
    assert isinstance(translate_method(_put()), SVTaskDecl)
    fd = translate_method(_get(), pure=True)
    assert fd.return_type == "bit [7:0]" and fd.is_pure


def test_template_header_params_extends_implements():
    cls = translate_decl(_template())
    assert isinstance(cls, SVClass)
    assert cls.params == ["type T = int", "int W = 8"]
    text = SVEmitter().emit_one(cls)
    assert text.splitlines()[0] == (
        "class gen_c #(type T = int, int W = 8) extends base_c "
        "implements api_if;")
    assert "  T v;" in text
    assert "  (* fw_opt = 2 *) bit [W-1:0] w;" in text
    assert "virtual task put(" in text and "return 0;" in text


def test_specialized_and_unpacked_types():
    spec = ir.DataTypeSpecialized(
        name="gen_c", base_template=_template(), template_args=[
            ir.TemplateArgType(param_name="T", type_value=S_T),
            ir.TemplateArgValue(param_name="W",
                                value_expr=ir.ExprConstant(value=4))])
    assert sv_type_str(spec) == "gen_c #(s_t, 4)"
    assert sv_decl_type(ir.DataTypeList(element_type=S_T)) == ("s_t", "[$]")
    assert sv_decl_type(ir.DataTypeArray(element_type=INT, size=4)) == (
        "int", "[4]")


def test_plain_class_from_ir():
    cls = ir.DataTypeClass(name="c", super=None, implements=["a_if", "b_if"],
                           fields=[ir.Field(name="x", datatype=INT)])
    text = SVEmitter().emit_one(translate_decl(cls))
    assert text.splitlines()[0] == "class c implements a_if, b_if;"


def _typedefs():
    from zuspec.ir.core import Field
    e = ir.DataTypeEnum(name="p::e_t", items={"A": 0, "B": 2}, width=2,
                        base_type=ir.DataTypeInt(bits=2, signed=False))
    w = ir.DataTypeInt(name="p::w_t", bits=8, signed=False)
    s = ir.DataTypeStruct(name="p::s_t", super=None, packed=True, fields=[
        Field(name="k", datatype=ir.DataTypeRef(ref_name="p::e_t")),
        Field(name="v", datatype=ir.DataTypeRef(ref_name="p::w_t")),
        Field(name="f", datatype=ir.DataTypeInt(bits=1, signed=False))])
    a = ir.DataTypeRef(name="p::a_t", ref_name="p::s_t")
    return {"p::e_t": e, "p::w_t": w, "p::s_t": s, "p::a_t": a}


def test_translate_package_typedefs():
    from zuspec.be.sv.ir.core_to_sv import translate_package
    text = SVEmitter().emit_one(translate_package("p", _typedefs(), []))
    assert "typedef enum bit[1:0] {\n    A = 2'd0,\n    B = 2'd2\n  } e_t;" in text
    assert "typedef bit [7:0] w_t;" in text
    assert "typedef struct packed {\n    e_t k;\n    w_t v;\n    bit f;\n  } s_t;" in text
    assert "typedef s_t a_t;" in text
    assert text.index("} e_t;") < text.index("} s_t;") < text.index("a_t;")


def test_translate_package_class_order():
    """Base classes and interfaces precede their users; a class used by a
    field before its definition (a cycle) gets a ``typedef class``."""
    from zuspec.be.sv.ir.core_to_sv import translate_package
    proto = ir.DataTypeProtocol(name="api_if", methods=[_put()])
    base = ir.DataTypeClass(name="base_c", super=None)
    user = ir.DataTypeClass(name="user_c", super=ir.DataTypeRef(ref_name="base_c"))
    user.implements = ["api_if"]
    user.fields = [ir.Field(name="peer", datatype=ir.DataTypeRef(ref_name="peer_c"))]
    peer = ir.DataTypeClass(name="peer_c", super=None)
    peer.fields = [ir.Field(name="u", datatype=ir.DataTypeRef(ref_name="user_c"))]
    pkg = translate_package("p", {}, [user, peer, proto, base])
    text = SVEmitter().emit_one(pkg)
    i = {n: text.index(n) for n in ("interface class api_if;", "class base_c;",
                                     "class user_c extends", "class peer_c;")}
    assert i["class base_c;"] < i["class user_c extends"]
    assert i["interface class api_if;"] < i["class user_c extends"]
    assert text.count("typedef class") == 1
    fwd = text.index("typedef class")
    assert fwd < min(i.values())


def test_template_protocol_to_interface_class():
    tmpl = ir.DataTypeParameterized(
        name="api_if",
        body=ir.DataTypeProtocol(name="api_if", methods=[ir.Function(
            name="put", is_async=True, args=_args(ir.Arg(
                arg="t", datatype=ir.DataTypeRef(ref_name="T"),
                direction=ir.SignalDirection.INPUT)))]),
        template_params=[ir.TemplateParamType(
            name="T", kind=ir.TemplateParamKind.TYPE, default_value=INT)])
    ic = translate_decl(tmpl)
    assert isinstance(ic, SVInterfaceClass) and ic.params == ["type T = int"]
    text = SVEmitter().emit_one(ic)
    assert text.splitlines()[0] == "interface class api_if #(type T = int);"
    assert "pure virtual task put(input T t);" in text


def test_translate_package_imports():
    from zuspec.be.sv.ir.core_to_sv import translate_package
    text = SVEmitter().emit_one(translate_package(
        "p", {}, [ir.DataTypeProtocol(name="a_if", methods=[_get()])],
        imports=["fw_hdl_pkg", "core_pkg"]))
    lines = text.splitlines()
    assert lines[:4] == ["package p;", "", "  import fw_hdl_pkg::*;",
                         "  import core_pkg::*;"]
    assert lines.index("  interface class a_if;") > 4


def test_abstract_class_implements_specialized_api():
    # A generated base: ``virtual class`` implementing an API specialized
    # over the class's own parameter (implements given as a type).
    tp = ir.TemplateParamType(name="T", kind=ir.TemplateParamKind.TYPE, default_value=INT)
    api = ir.DataTypeParameterized(name="api_if", template_params=[tp],
                                   body=ir.DataTypeProtocol(name="api_if", methods=[]))
    ref = ir.DataTypeSpecialized(base_template=api, template_args=[ir.TemplateArgType(
        param_name="T", type_value=ir.DataTypeRef(ref_name="T"))])
    cls = ir.DataTypeParameterized(
        name="api_base", template_params=[tp],
        body=ir.DataTypeClass(name="api_base", super=None, is_abstract=True,
                              implements=[ref, "other_if"]))
    sv = translate_decl(cls)
    assert sv.is_virtual and sv.implements == ["api_if #(T)", "other_if"]
    assert SVEmitter().emit_one(sv).splitlines()[0] == (
        "virtual class api_base #(type T = int) implements api_if #(T), other_if;")


def test_ctor_default_arg_and_typed_local():
    """``new`` is neither virtual nor typed; ``Arguments.defaults`` align
    with the trailing arguments; a typed ``StmtAnnAssign`` declares a local
    (K7.2c)."""
    ctor = ir.Function(
        name="new",
        args=ir.Arguments(args=[
            ir.Arg(arg="a", datatype=INT, direction=ir.SignalDirection.INPUT),
            ir.Arg(arg="t", datatype=S_T, direction=ir.SignalDirection.INPUT)],
            defaults=[ir.ExprNull()]),
        body=[ir.StmtAnnAssign(target=ir.ExprRefLocal(name="x"),
                               annotation=ir.ExprRefUnresolved(name="int"),
                               value=ir.ExprConstant(value=1), ir_type=INT),
              ir.StmtAnnAssign(target=ir.ExprRefLocal(name="y"),
                               annotation=ir.ExprRefUnresolved(name="s_t"),
                               ir_type=S_T)])
    sv = SVEmitter().emit_function_decl(translate_method(ctor))
    assert "  function new(input int a, input s_t t = null);" in sv
    assert "virtual" not in sv
    assert "int x = 1;" in sv
    assert "s_t y;" in sv


def test_static_function():
    """G6: ``metadata["static"]`` gives a ``static`` (not virtual) function."""
    fn = ir.Function(
        name="f",
        args=ir.Arguments(args=[
            ir.Arg(arg="v", datatype=S_T, direction=ir.SignalDirection.INPUT),
            ir.Arg(arg="s", datatype=ir.DataTypeString(),
                   direction=ir.SignalDirection.OUTPUT)]),
        body=[ir.StmtReturn(value=ir.ExprConstant(value=1))],
        returns=ir.DataTypeInt(bits=1, signed=False), metadata={"static": True})
    sv = SVEmitter().emit_function_decl(translate_method(fn))
    assert "  static function bit f(input s_t v, output string s);" in sv
    assert "virtual" not in sv


def test_four_state_int_is_logic():
    # G5: a four-state integer (SV ``logic``) is emitted as ``logic``.
    w = ir.ExprRefParam(name="W")
    assert sv_type_str(ir.DataTypeInt(bits=1, signed=False, four_state=True)) == "logic"
    assert sv_type_str(ir.DataTypeInt(bits=8, signed=False, four_state=True)) == "logic [7:0]"
    assert sv_type_str(ir.DataTypeInt(bits=8, signed=False, four_state=True,
                                      width_expr=w)) == "logic [W-1:0]"
    assert sv_type_str(ir.DataTypeInt(bits=8, signed=True, four_state=True)) == \
        "logic signed [7:0]"
    assert sv_type_str(ir.DataTypeInt(bits=8, signed=False)) == "bit [7:0]"


def test_sync_param_four_state_32_is_not_int():
    # F8a: a 4-state 32-bit signed type (SV ``integer``) keeps its states in
    # the parameterized RTL; only a 2-state one becomes ``int``.
    from zuspec.be.sv.sync_param import _base
    assert _base(ir.DataTypeInt(bits=32, signed=True)) == "int"
    assert _base(ir.DataTypeInt(bits=32, signed=True, four_state=True)) == \
        "logic signed [31:0]"
    assert _base(ir.DataTypeInt(bits=4, signed=False, four_state=True)) == "logic [3:0]"


def test_pure_method_in_virtual_class():
    """G8: ``metadata["pure"]`` gives a ``pure virtual`` prototype in a class."""
    fn = ir.Function(name="sprint", args=_args(), body=[],
                     returns=ir.DataTypeString(), metadata={"pure": True})
    cls = ir.DataTypeClass(name="t_txn", super=None, is_abstract=True, functions=[fn])
    sv = SVEmitter().emit_class(translate_decl(cls))
    assert "virtual class t_txn;" in sv
    assert "pure virtual function string sprint();" in sv
    assert "endfunction" not in sv


def test_new_expr():
    """G9: ``ExprNew`` constructs the handle's declared type: ``new``, or
    ``new(args)``."""
    from zuspec.be.sv.ir.expr_emit import SVExprEmitter
    dt = ir.DataTypeRef(ref_name="t_txn")
    assert SVExprEmitter().emit(ir.ExprNew(datatype=dt)) == "new"
    assert SVExprEmitter().emit(ir.ExprNew(
        datatype=dt, args=[ir.ExprNull(), ir.ExprConstant(value=1)])) == "new(null, 1)"

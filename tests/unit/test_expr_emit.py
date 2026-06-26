"""Unit tests for SVExprEmitter (plan task C1 / decision D4).

Renders zuspec.ir.core expression nodes to SystemVerilog text. These pin the
conventions the constraint/procedural emitters rely on.
"""
import pytest

from zuspec.ir.core import expr as e
from zuspec.be.sv.ir.expr_emit import SVExprEmitter


def E():
    return SVExprEmitter()


def _self_attr(name):
    return e.ExprAttribute(value=e.TypeExprRefSelf(), attr=name)


def _c(v):
    return e.ExprConstant(value=v)


# ---------------------------------------------------------------- leaves

def test_constants():
    em = E()
    assert em.emit(_c(7)) == "7"
    assert em.emit(_c(-3)) == "-3"
    assert em.emit(_c(True)) == "1'b1"
    assert em.emit(_c(False)) == "1'b0"
    assert em.emit(_c("hi")) == '"hi"'
    assert em.emit(e.ExprNull()) == "null"


def test_self_attribute_is_bare():
    # self.addr -> addr
    assert E().emit(_self_attr("addr")) == "addr"


def test_nested_attribute():
    # self.in_buf.x -> in_buf.x
    inner = _self_attr("in_buf")
    assert E().emit(e.ExprAttribute(value=inner, attr="x")) == "in_buf.x"


def test_local_and_unresolved_refs():
    assert E().emit(e.ExprRefLocal(name="i")) == "i"
    assert E().emit(e.ExprRefUnresolved(name="foo")) == "foo"
    assert E().emit(e.ExprStaticRef(is_global=True, path=["pkg", "T"])) == "pkg::T"


# ---------------------------------------------------------------- operators

def test_binary_arithmetic():
    x = e.ExprBin(lhs=_self_attr("a"), op=e.BinOp.Add, rhs=_c(1))
    assert E().emit(x) == "(a + 1)"


def test_binary_eq_and_slice_alignment():
    # addr[1:0] == 0
    sl = e.ExprSubscript(value=_self_attr("addr"),
                         slice=e.ExprSlice(lower=_c(0), upper=_c(1)))
    cmp = e.ExprBin(lhs=sl, op=e.BinOp.Eq, rhs=_c(0))
    assert E().emit(cmp) == "(addr[1:0] == 0)"


def test_unary():
    x = e.ExprUnary(op=e.UnaryOp.Not, operand=_self_attr("flag"))
    assert E().emit(x) == "!(flag)"


def test_bool_and_or():
    a = e.ExprBin(lhs=_self_attr("a"), op=e.BinOp.Gt, rhs=_c(0))
    b = e.ExprBin(lhs=_self_attr("b"), op=e.BinOp.Lt, rhs=_c(10))
    x = e.ExprBool(op=e.BoolOp.And, values=[a, b])
    assert E().emit(x) == "(a > 0) && (b < 10)"


def test_compare_chain():
    x = e.ExprCompare(left=_self_attr("a"),
                      ops=[e.CmpOp.LtE, e.CmpOp.Lt],
                      comparators=[_self_attr("b"), _c(10)])
    assert E().emit(x) == "(a <= b < 10)"


def test_ifexp_ternary():
    x = e.ExprIfExp(test=e.ExprBin(lhs=_self_attr("a"), op=e.BinOp.Gt, rhs=_c(0)),
                    body=_self_attr("a"), orelse=_c(0))
    assert E().emit(x) == "((a > 0) ? a : 0)"


# ---------------------------------------------------------------- membership

def test_inside_rangelist():
    rl = e.ExprRangeList(ranges=[
        e.ExprRange(lower=_c(0), upper=_c(10)),
        e.ExprRange(lower=_c(20)),
        e.ExprRange(lower=_c(30), upper=_c(40)),
    ])
    x = e.ExprIn(value=_self_attr("addr"), container=rl)
    assert E().emit(x) == "addr inside {[0:10], 20, [30:40]}"


# ---------------------------------------------------------------- calls / casts

def test_implication_call():
    cond = e.ExprBin(lhs=_self_attr("mode"), op=e.BinOp.Eq, rhs=_c(1))
    body = e.ExprBin(lhs=_self_attr("addr"), op=e.BinOp.Gt, rhs=_c(0))
    x = e.ExprCall(func=e.ExprRefUnresolved(name="implies"), args=[cond, body])
    assert E().emit(x) == "((mode == 1) -> (addr > 0))"


def test_generic_call():
    x = e.ExprCall(func=e.ExprRefUnresolved(name="f"),
                   args=[_self_attr("a"), _c(2)])
    assert E().emit(x) == "f(a, 2)"


def test_call_hook_intercepts():
    def hook(expr):
        if isinstance(expr.func, e.ExprRefUnresolved) and expr.func.name == "doit":
            return "comp.import_if.doit()"
        return None
    em = SVExprEmitter(call_hook=hook)
    x = e.ExprCall(func=e.ExprRefUnresolved(name="doit"), args=[])
    assert em.emit(x) == "comp.import_if.doit()"


# ---------------------------------------------------------------- hooks / refs

def test_field_resolver():
    def resolve(base_sv, index):
        return {0: "addr", 1: "size"}[index]
    em = SVExprEmitter(field_resolver=resolve)
    x = e.ExprRefField(base=e.TypeExprRefSelf(), index=1)
    assert em.emit(x) == "size"


def test_field_default_naming():
    x = e.ExprRefField(base=e.TypeExprRefSelf(), index=2)
    assert E().emit(x) == "field_2"


def test_unknown_node_raises():
    class Bogus(e.Expr):
        pass
    with pytest.raises(NotImplementedError):
        E().emit(Bogus())

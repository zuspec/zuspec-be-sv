"""Concurrent SVA IR node (process→FSM unification, phase U-1 migrate: sva_gen).

be.sv previously had only *procedural* assert/cover (`SVStmtAssert`/`SVStmtCover`).
The migrated `sva_gen` builds concurrent assertions (`assert/assume/cover
property`) — these tests pin the `SVConcurrentAssert` node + emitter, over core
`ir.core` expressions.
"""
import zuspec.ir.core as ir

from zuspec.be.sv.ir.sv import SVConcurrentAssert
from zuspec.be.sv.ir.sv_emit import SVEmitter


def _ref(n):
    return ir.ExprRefLocal(name=n)


def _emit(node):
    return SVEmitter().emit_one(node)


def test_simple_assert_property():
    # assert property (@(posedge clk) valid);
    out = _emit(SVConcurrentAssert(expr=_ref("valid")))
    assert out == "assert property (@(posedge clk) valid);"


def test_labeled_disable_iff_and_implication():
    # Handshake Irrevocable: once vld && !rdy, next cycle vld holds.
    ante = ir.ExprBin(lhs=_ref("vld"), op=ir.BinOp.And, rhs=_ref("not_rdy"))
    node = SVConcurrentAssert(
        expr=_ref("vld"),
        clock="posedge clk",
        disable_iff=_ref("rst"),
        antecedent=ante,
        overlap=False,          # |=>  (next cycle)
        kind="assert",
        label="p_irrevocable",
    )
    out = _emit(node)
    assert out == (
        "p_irrevocable: assert property "
        "(@(posedge clk) disable iff (rst) (vld && not_rdy) |=> vld);"
    )


def test_assume_and_cover_kinds():
    assume = _emit(SVConcurrentAssert(expr=_ref("req"), kind="assume"))
    cover = _emit(SVConcurrentAssert(expr=_ref("done"), kind="cover"))
    assert assume.startswith("assume property (@(posedge clk) req)")
    assert cover.startswith("cover property (@(posedge clk) done)")


def test_overlapping_implication():
    node = SVConcurrentAssert(expr=_ref("b"), antecedent=_ref("a"), overlap=True)
    out = _emit(node)
    assert "a |-> b" in out

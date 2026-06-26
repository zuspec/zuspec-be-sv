"""Unit tests for SVConstraintEmitter + SVEmitter structured-constraint path
(plan task C2, decisions D3/D4)."""
import zuspec.ir.core as ir
from zuspec.be.sv.ir.constraint_emit import SVConstraintEmitter
from zuspec.be.sv.ir.sv_emit import SVEmitter
from zuspec.be.sv.ir.sv import SVClass, SVClassField


def _self(name):
    return ir.ExprAttribute(value=ir.TypeExprRefSelf(), attr=name)


def _c(v):
    return ir.ExprConstant(value=v)


def _emit_items(items):
    return SVConstraintEmitter().emit_items(items)


def test_expr_and_soft():
    sl = ir.ExprSubscript(value=_self("addr"),
                          slice=ir.ExprSlice(lower=_c(0), upper=_c(1)))
    items = [
        ir.ConstraintExpr(expr=ir.ExprBin(lhs=sl, op=ir.BinOp.Eq, rhs=_c(0))),
        ir.ConstraintSoft(expr=ir.ExprBin(lhs=_self("len"), op=ir.BinOp.Eq, rhs=_c(4))),
    ]
    assert _emit_items(items) == ["(addr[1:0] == 0);", "soft (len == 4);"]


def test_implies_expr_body():
    # all-expression body renders as `ant -> (conjunction);` (Verilator-safe)
    item = ir.ConstraintImplies(
        antecedent=ir.ExprBin(lhs=_self("mode"), op=ir.BinOp.Eq, rhs=_c(1)),
        body=[
            ir.ConstraintExpr(expr=ir.ExprBin(lhs=_self("addr"), op=ir.BinOp.Gt, rhs=_c(0))),
            ir.ConstraintExpr(expr=ir.ExprBin(lhs=_self("addr"), op=ir.BinOp.Lt, rhs=_c(16))),
        ])
    assert _emit_items([item]) == ["(mode == 1) -> ((addr > 0) && (addr < 16));"]


def test_implies_structured_body_uses_if():
    # structured body falls back to the equivalent `if (ant) { ... }`
    item = ir.ConstraintImplies(
        antecedent=ir.ExprBin(lhs=_self("mode"), op=ir.BinOp.Eq, rhs=_c(1)),
        body=[ir.ConstraintForeach(
            array=_self("data"), index_var="i",
            body=[ir.ConstraintExpr(expr=ir.ExprBin(
                lhs=ir.ExprSubscript(value=_self("data"), slice=ir.ExprRefLocal(name="i")),
                op=ir.BinOp.Gt, rhs=_c(0)))])])
    assert _emit_items([item]) == [
        "if ((mode == 1)) {", "  foreach (data[i]) {", "    (data[i] > 0);", "  }", "}"]


def test_if_else():
    item = ir.ConstraintIfElse(
        cond=ir.ExprBin(lhs=_self("kind"), op=ir.BinOp.Eq, rhs=_c(2)),
        then_body=[ir.ConstraintExpr(expr=ir.ExprBin(lhs=_self("size"), op=ir.BinOp.Eq, rhs=_c(4)))],
        else_body=[ir.ConstraintExpr(expr=ir.ExprBin(lhs=_self("size"), op=ir.BinOp.Eq, rhs=_c(8)))])
    assert _emit_items([item]) == [
        "if ((kind == 2)) {", "  (size == 4);", "} else {", "  (size == 8);", "}"]


def test_foreach():
    item = ir.ConstraintForeach(
        array=_self("data"), index_var="i",
        body=[ir.ConstraintExpr(expr=ir.ExprIn(
            value=ir.ExprSubscript(value=_self("data"), slice=ir.ExprRefLocal(name="i")),
            container=ir.ExprRangeList(ranges=[ir.ExprRange(lower=_c(0), upper=_c(255))])))])
    assert _emit_items([item]) == [
        "foreach (data[i]) {", "  data[i] inside {[0:255]};", "}"]


def test_unique():
    item = ir.ConstraintUnique(items=[_self("a"), _self("b")])
    assert _emit_items([item]) == ["unique {a, b};"]


def test_dist():
    item = ir.ConstraintDist(target=_self("opcode"), weights=[
        ir.DistWeight(rng=_c(0), weight=_c(10)),
        ir.DistWeight(rng=ir.ExprRange(lower=_c(1), upper=_c(3)), weight=_c(5), per_value=True),
        ir.DistWeight(rng=_c(7)),
    ])
    assert _emit_items([item]) == ["opcode dist {0 := 10, [1:3] :/ 5, 7};"]


def test_solve_before():
    item = ir.ConstraintSolveBefore(before=[_self("mode")], after=[_self("addr"), _self("size")])
    assert _emit_items([item]) == ["solve mode before addr, size;"]


def test_full_block_via_sv_emitter():
    blk = ir.ConstraintBlock(name="c_align", items=[
        ir.ConstraintExpr(expr=ir.ExprBin(
            lhs=ir.ExprSubscript(value=_self("addr"),
                                 slice=ir.ExprSlice(lower=_c(0), upper=_c(1))),
            op=ir.BinOp.Eq, rhs=_c(0))),
    ])
    out = SVEmitter().emit_constraint_block(blk, indent="")
    assert out == "constraint c_align {\n  (addr[1:0] == 0);\n}"


def test_sv_class_with_structured_constraint():
    """SVClass.constraints may hold core ConstraintBlock objects (D1 interim:
    pssc places structured constraints directly until the full translation pass)."""
    blk = ir.ConstraintBlock(name="c_size", items=[
        ir.ConstraintExpr(expr=ir.ExprIn(
            value=_self("size"),
            container=ir.ExprRangeList(ranges=[ir.ExprRange(lower=_c(1), upper=_c(64))]))),
    ])
    cls = SVClass(
        name="mem_write",
        fields=[SVClassField(name="size", dtype="bit [7:0]", is_rand=True)],
        constraints=[blk])
    out = SVEmitter().emit_class(cls)
    assert "rand bit [7:0] size;" in out
    assert "constraint c_size {" in out
    assert "size inside {[1:64]};" in out

"""Unit tests for structured SV procedural statements (plan task C3)."""
import zuspec.ir.core as ir
from zuspec.be.sv.ir import stmt as s
from zuspec.be.sv.ir.stmt_emit import SVStmtEmitter
from zuspec.be.sv.ir.sv_emit import SVEmitter
from zuspec.be.sv.ir.sv import SVTaskDecl, SVFunctionDecl


def _c(v):
    return ir.ExprConstant(value=v)


def _ref(n):
    return ir.ExprRefLocal(name=n)


def E():
    return SVStmtEmitter()


def _emit(stmts):
    return E().emit_stmts(stmts, indent="")


def test_vardecl_and_assign():
    out = _emit([
        s.SVVarDecl(name="x", dtype="int", init=_c(0)),
        s.SVStmtAssign(lhs=_ref("x"), rhs=ir.ExprBin(lhs=_ref("x"), op=ir.BinOp.Add, rhs=_c(1))),
    ])
    assert out == ["int x = 0;", "x = (x + 1);"]


def test_expr_call_and_return():
    call = ir.ExprCall(func=ir.ExprRefUnresolved(name="$display"),
                       args=[ir.ExprConstant(value="hi")])
    out = _emit([s.SVStmtExpr(expr=call), s.SVStmtReturn(value=_ref("x"))])
    assert out == ['$display("hi");', "return x;"]


def test_return_void_and_raw_comment():
    out = _emit([s.SVStmtReturn(), s.SVStmtRaw(text="$finish;"), s.SVStmtComment(text="done")])
    assert out == ["return;", "$finish;", "// done"]


def test_if_else():
    out = _emit([s.SVStmtIf(
        cond=ir.ExprBin(lhs=_ref("a"), op=ir.BinOp.Gt, rhs=_c(0)),
        then_body=[s.SVStmtExpr(expr=ir.ExprCall(func=ir.ExprRefUnresolved(name="f"), args=[]))],
        else_body=[s.SVStmtExpr(expr=ir.ExprCall(func=ir.ExprRefUnresolved(name="g"), args=[]))])])
    assert out == [
        "if ((a > 0)) begin", "  f();", "end else begin", "  g();", "end"]


def test_for_loop():
    out = _emit([s.SVStmtFor(var="i", limit=_c(5),
                             body=[s.SVStmtExpr(expr=ir.ExprCall(
                                 func=ir.ExprRefUnresolved(name="step"), args=[_ref("i")]))])])
    assert out == [
        "for (int i = 0; i < 5; i++) begin", "  step(i);", "end"]


def test_foreach_while_repeat():
    fe = s.SVStmtForeach(array=_ref("data"), index_var="i",
                         body=[s.SVStmtAssign(lhs=ir.ExprSubscript(value=_ref("data"), slice=_ref("i")), rhs=_c(0))])
    wh = s.SVStmtWhile(cond=_ref("busy"), body=[s.SVStmtRaw(text="@(posedge clk);")])
    rp = s.SVStmtRepeat(count=_c(3), body=[s.SVStmtRaw(text="tick();")])
    assert _emit([fe]) == ["foreach (data[i]) begin", "  data[i] = 0;", "end"]
    assert _emit([wh]) == ["while (busy) begin", "  @(posedge clk);", "end"]
    assert _emit([rp]) == ["repeat (3) begin", "  tick();", "end"]


def test_case():
    out = _emit([s.SVStmtCase(subject=_ref("kind"), items=[
        s.SVCaseItem(labels=[_c(0)], body=[s.SVStmtRaw(text="a();")]),
        s.SVCaseItem(labels=[_c(1), _c(2)], body=[s.SVStmtRaw(text="b();")]),
        s.SVCaseItem(labels=[], body=[s.SVStmtRaw(text="d();")]),
    ])])
    assert out == [
        "case (kind)",
        "  0: begin", "    a();", "  end",
        "  1, 2: begin", "    b();", "  end",
        "  default: begin", "    d();", "  end",
        "endcase",
    ]


def test_randomize_no_constraints():
    out = _emit([s.SVStmtRandomize(target=_ref("a"), fail_msg="rand of a failed")])
    assert out == ['if (!a.randomize()) $fatal(1, "rand of a failed");']


def test_randomize_with_constraints():
    out = _emit([s.SVStmtRandomize(
        target=_ref("p"),
        constraints=[ir.ConstraintExpr(expr=ir.ExprBin(
            lhs=ir.ExprAttribute(value=ir.TypeExprRefSelf(), attr="x"),
            op=ir.BinOp.Eq, rhs=_c(5)))],
        fail_msg="p failed")])
    assert out == [
        "if (!p.randomize() with {",
        "  (x == 5);",
        '}) $fatal(1, "p failed");',
    ]


def test_randomize_no_check_void_cast():
    out = _emit([s.SVStmtRandomize(target=_ref("a"), check=False)])
    assert out == ["void'(a.randomize());"]


def test_task_with_structured_body_via_sv_emitter():
    td = SVTaskDecl(name="body", body=[
        s.SVStmtExpr(expr=ir.ExprCall(func=ir.ExprRefUnresolved(name="$display"),
                                      args=[ir.ExprConstant(value="run")])),
    ])
    out = SVEmitter().emit_task_decl(td, indent="")
    assert out == 'task body();\n  $display("run");\nendtask'


def test_function_structured_body_takes_precedence_over_lines():
    fd = SVFunctionDecl(name="get", return_type="int",
                        body_lines=["// ignored"],
                        body=[s.SVStmtReturn(value=ir.ExprConstant(value=7))])
    out = SVEmitter().emit_function_decl(fd, indent="")
    assert out == "function int get();\n  return 7;\nendfunction"


def test_legacy_body_lines_still_work():
    td = SVTaskDecl(name="body", body_lines=['$display("legacy");'])
    out = SVEmitter().emit_task_decl(td, indent="")
    assert out == 'task body();\n  $display("legacy");\nendtask'


def test_fork_join():
    out = _emit([s.SVStmtFork(branches=[
        [s.SVStmtRaw(text="a();")],
        [s.SVStmtRaw(text="b();")],
    ])])
    assert out == [
        "fork", "  begin", "    a();", "  end",
        "  begin", "    b();", "  end", "join"]


def test_fork_join_none():
    out = _emit([s.SVStmtFork(branches=[[s.SVStmtRaw(text="x();")]], join="join_none")])
    assert out == ["fork", "  begin", "    x();", "  end", "join_none"]

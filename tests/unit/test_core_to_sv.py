"""Unit tests for the core->SV-IR translation pass (plan task C5, D1)."""
import zuspec.ir.core as ir
from zuspec.be.sv.ir.core_to_sv import (
    sv_type_str, translate_field, translate_class, translate_stmts)
from zuspec.be.sv.ir.sv_emit import SVEmitter
from zuspec.be.sv.ir.stmt_emit import SVStmtEmitter


def test_sv_type_str_scalars():
    # DataTypeInt defaults to signed=True; PSS `bit` fields are unsigned.
    assert sv_type_str(ir.DataTypeInt(bits=1, signed=False)) == "bit"
    assert sv_type_str(ir.DataTypeInt(bits=8, signed=False)) == "bit [7:0]"
    assert sv_type_str(ir.DataTypeInt(bits=32, signed=True)) == "int"
    assert sv_type_str(ir.DataTypeInt(bits=8, signed=True)) == "int signed [7:0]"
    assert sv_type_str(ir.DataTypeString()) == "string"


def test_sv_type_str_enum_uses_namer():
    enum = ir.DataTypeEnum(name="cmd_e", items={"RD": 0, "WR": 1})
    assert sv_type_str(enum) == "cmd_e"
    assert sv_type_str(enum, type_namer=lambda d: "pkg::" + d.name) == "pkg::cmd_e"


def test_translate_field_rand():
    f = ir.Field(name="addr", datatype=ir.DataTypeInt(bits=32, signed=False),
                 kind=ir.FieldKind.Field, rand_kind=ir.RandKind.RAND)
    cf = translate_field(f)
    assert cf.name == "addr" and cf.dtype == "bit [31:0]" and cf.is_rand


def test_translate_class_with_constraints():
    def _self(n):
        return ir.ExprAttribute(value=ir.TypeExprRefSelf(), attr=n)

    dtype = ir.DataTypeStruct(name="pkt", super=None, fields=[
        ir.Field(name="addr", datatype=ir.DataTypeInt(bits=32, signed=False),
                 kind=ir.FieldKind.Field, rand_kind=ir.RandKind.RAND),
        ir.Field(name="kind", datatype=ir.DataTypeInt(bits=8, signed=False),
                 kind=ir.FieldKind.Field, rand_kind=ir.RandKind.RANDC),
        ir.Field(name="tag", datatype=ir.DataTypeInt(bits=4, signed=False),
                 kind=ir.FieldKind.Field, rand_kind=None),
    ])
    blk = ir.ConstraintBlock(name="c0", items=[
        ir.ConstraintExpr(expr=ir.ExprBin(
            lhs=ir.ExprSubscript(value=_self("addr"),
                                 slice=ir.ExprSlice(lower=ir.ExprConstant(value=0),
                                                    upper=ir.ExprConstant(value=1))),
            op=ir.BinOp.Eq, rhs=ir.ExprConstant(value=0)))])

    cls = translate_class(dtype, extends="zsp_action", constraints=[blk])
    assert cls.name == "pkt"
    assert cls.extends_name == "zsp_action"
    assert [f.name for f in cls.fields] == ["addr", "kind", "tag"]
    assert cls.fields[0].is_rand and not cls.fields[0].is_randc
    assert cls.fields[1].is_randc
    assert not cls.fields[2].is_rand

    out = SVEmitter().emit_class(cls)
    assert "class pkt extends zsp_action;" in out
    assert "rand bit [31:0] addr;" in out
    assert "randc bit [7:0] kind;" in out
    assert "bit [3:0] tag;" in out
    assert "constraint c0 {" in out
    assert "(addr[1:0] == 0);" in out


def test_translate_stmts_procedural():
    """Core procedural Stmt list -> SVStmt -> SV text (plan task C5 bodies)."""
    body = [
        ir.StmtExpr(expr=ir.ExprCall(func=ir.ExprRefUnresolved(name="$display"),
                                     args=[ir.ExprConstant(value="hi")])),
        ir.StmtAssign(targets=[ir.ExprRefLocal(name="x")], value=ir.ExprConstant(value=1)),
        ir.StmtAugAssign(target=ir.ExprRefLocal(name="x"), op=ir.AugOp.Add,
                         value=ir.ExprConstant(value=2)),
        ir.StmtIf(test=ir.ExprBin(lhs=ir.ExprRefLocal(name="x"), op=ir.BinOp.Gt,
                                  rhs=ir.ExprConstant(value=0)),
                  body=[ir.StmtReturn(value=ir.ExprRefLocal(name="x"))],
                  orelse=[]),
        ir.StmtRepeat(count=ir.ExprConstant(value=3),
                      body=[ir.StmtExpr(expr=ir.ExprCall(
                          func=ir.ExprRefUnresolved(name="tick"), args=[]))]),
    ]
    sv = translate_stmts(body)
    lines = SVStmtEmitter().emit_stmts(sv, indent="")
    assert lines == [
        '$display("hi");',
        "x = 1;",
        "x += 2;",
        "if ((x > 0)) begin",
        "  return x;",
        "end",
        "repeat (3) begin",
        "  tick();",
        "end",
    ]


# --- WS1 increment 1: control-flow parity with legacy lower_stmts ---------- #

def _emit(stmts):
    return SVStmtEmitter().emit_stmts(translate_stmts(stmts), indent="")


def test_translate_stmt_for_as_foreach():
    # StmtFor projects onto foreach(iter[target]) (matches legacy lower_stmts).
    s = ir.StmtFor(
        target=ir.ExprRefLocal(name="i"), iter=ir.ExprRefLocal(name="data"),
        body=[ir.StmtAssign(targets=[ir.ExprSubscript(
            value=ir.ExprRefLocal(name="data"), slice=ir.ExprRefLocal(name="i"))],
            value=ir.ExprConstant(value=0))])
    assert _emit([s]) == [
        "foreach (data[i]) begin",
        "  data[i] = 0;",
        "end",
    ]


def test_translate_repeat_while_as_do_while():
    s = ir.StmtRepeatWhile(
        condition=ir.ExprBin(lhs=ir.ExprRefLocal(name="done"), op=ir.BinOp.Eq,
                             rhs=ir.ExprConstant(value=0)),
        body=[ir.StmtExpr(expr=ir.ExprCall(
            func=ir.ExprRefUnresolved(name="step"), args=[]))])
    assert _emit([s]) == [
        "do begin",
        "  step();",
        "end while ((done == 0));",
    ]


def test_translate_match_as_case():
    s = ir.StmtMatch(
        subject=ir.ExprRefLocal(name="kind"),
        cases=[
            ir.StmtMatchCase(
                pattern=ir.PatternValue(value=ir.ExprConstant(value=0)),
                body=[ir.StmtAssign(targets=[ir.ExprRefLocal(name="x")],
                                    value=ir.ExprConstant(value=1))]),
            # PatternOr -> comma-joined labels
            ir.StmtMatchCase(
                pattern=ir.PatternOr(patterns=[
                    ir.PatternValue(value=ir.ExprConstant(value=1)),
                    ir.PatternValue(value=ir.ExprConstant(value=2))]),
                body=[ir.StmtAssign(targets=[ir.ExprRefLocal(name="x")],
                                    value=ir.ExprConstant(value=2))]),
            # wildcard -> default
            ir.StmtMatchCase(
                pattern=ir.PatternAs(pattern=None),
                body=[ir.StmtAssign(targets=[ir.ExprRefLocal(name="x")],
                                    value=ir.ExprConstant(value=9))]),
        ])
    assert _emit([s]) == [
        "case (kind)",
        "  0: begin",
        "    x = 1;",
        "  end",
        "  1, 2: begin",
        "    x = 2;",
        "  end",
        "  default: begin",
        "    x = 9;",
        "  end",
        "endcase",
    ]


# --- WS1 increment 2: diagnostics (assert / cover / raise / yield) --------- #

def _cmp(lhs, op, rhs):
    return ir.ExprBin(lhs=ir.ExprRefLocal(name=lhs), op=op, rhs=ir.ExprConstant(value=rhs))


def test_translate_assert():
    assert _emit([ir.StmtAssert(test=_cmp("x", ir.BinOp.Gt, 0))]) == [
        "assert ((x > 0));",
    ]
    assert _emit([ir.StmtAssert(test=_cmp("x", ir.BinOp.Gt, 0),
                                msg=ir.ExprConstant(value="x must be positive"))]) == [
        'assert ((x > 0)) else $error("x must be positive");',
    ]


def test_translate_cover():
    assert _emit([ir.StmtCover(test=_cmp("hit", ir.BinOp.Eq, 1))]) == [
        "cover ((hit == 1));",
    ]
    assert _emit([ir.StmtCover(test=_cmp("hit", ir.BinOp.Eq, 1),
                               msg=ir.ExprConstant(value="hit_seen"))]) == [
        '`ZSP_TRACE("cover: "hit_seen"");',
        "cover ((hit == 1));",
    ]


def test_translate_raise():
    assert _emit([ir.StmtRaise(exc=ir.ExprConstant(value="boom"))]) == [
        '$fatal(1, "boom");',
    ]
    assert _emit([ir.StmtRaise()]) == [
        '$fatal(1, "error");',
    ]


def test_translate_yield_is_comment():
    assert _emit([ir.StmtYield()]) == [
        "// yield (no-op in SV class execution)",
    ]

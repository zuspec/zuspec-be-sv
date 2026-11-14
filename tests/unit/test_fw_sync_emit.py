"""Emission of fw-hdl synchronous components (fields with ``drive``):
always_ff / always_comb, reset-forced outputs, registers without reset."""
import zuspec.ir.core as ir
from zuspec.be.sv import SVGenerator

S = ir.ProcessKind.SYNC
C = ir.ProcessKind.COMB


def _u(bits):
    return ir.DataTypeInt(bits=bits, signed=False)


def _r(i):
    return ir.ExprRefField(base=ir.TypeExprRefSelf(), index=i)


def _set(i, v):
    return ir.StmtAssign(targets=[_r(i)], value=v)


def _k(v):
    return ir.ExprConstant(value=v)


def _emit(tmp_path, comp):
    files = SVGenerator(tmp_path).generate(ir.Context(type_m={comp.name: comp}))
    return files[0].read_text()


def _reg_comp():
    # 0 clock, 1 reset, 2 d (in), 3 vld (out, forced 0), 4 q (out), 5 cnt (state)
    comp = ir.DataTypeComponent(name="reg", super=None, fields=[
        ir.FieldInOut(name="clock", datatype=_u(1), is_out=False),
        ir.FieldInOut(name="reset", datatype=_u(1), is_out=False),
        ir.FieldInOut(name="d", datatype=_u(8), is_out=False),
        ir.FieldInOut(name="vld", datatype=_u(1), is_out=True, drive=S, reset_force=0),
        ir.FieldInOut(name="q", datatype=_u(8), is_out=True, drive=S),
        ir.Field(name="cnt", datatype=_u(4), drive=S),
    ])
    body = [ir.StmtIf(test=_r(1), body=[_set(3, _k(0)), _set(5, _k(0))], orelse=[
        _set(3, ir.ExprUnary(op=ir.UnaryOp.Not, operand=_r(3))),
        _set(5, ir.ExprBin(lhs=_r(5), op=ir.BinOp.Add, rhs=_k(1))),
        ir.StmtIf(test=_r(3), body=[_set(4, _r(2))])])]
    comp.sync_processes.append(ir.Function(
        name="step", process_kind=S, body=body,
        metadata={"clock": _r(0), "reset": _r(1)}))
    return comp


def test_sync_split_and_force(tmp_path):
    sv = _emit(tmp_path, _reg_comp())
    assert "  output logic vld,\n" in sv
    assert "  logic [3:0] cnt;\n" in sv and "  logic vld_q;\n" in sv
    reset_ff, noreset_ff = sv.split("always_ff @(posedge clock) begin")[1:]
    # Reset registers: vld (renamed to its register) and cnt; q is not reset.
    assert "vld_q <= 0;" in reset_ff and "cnt <= 0;" in reset_ff
    assert "vld_q <= !vld_q;" in reset_ff.replace("(", "").replace(")", "")
    assert " q <=" not in reset_ff
    assert "if (vld_q) begin" in noreset_ff and "q <= d;" in noreset_ff
    assert "reset" not in noreset_ff.split("end\n\n")[0]
    assert "assign vld = reset ? 1'd0 : vld_q;" in sv
    assert sv.rstrip().endswith("endmodule")


def test_sync_all_reset_is_one_block(tmp_path):
    comp = _reg_comp()
    comp.sync_processes[0].body[0].orelse.pop()       # drop the q write
    sv = _emit(tmp_path, comp)
    assert sv.count("always_ff") == 1
    assert "Not reset" not in sv


def test_comb(tmp_path):
    comp = ir.DataTypeComponent(name="pass1", super=None, fields=[
        ir.FieldInOut(name="i", datatype=_u(4), is_out=False),
        ir.FieldInOut(name="o", datatype=_u(4), is_out=True, drive=C),
    ])
    comp.comb_processes.append(ir.Function(
        name="comb", process_kind=C, body=[_set(1, _r(0))]))
    sv = _emit(tmp_path, comp)
    assert "always_comb begin\n    o = i;\n  end" in sv
    assert "always_ff" not in sv and "assign" not in sv

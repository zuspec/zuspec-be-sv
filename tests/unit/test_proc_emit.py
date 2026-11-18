"""Process blocks (fw-hdl layer 2, ``ProcBlock``) in a structural module:
step blocks as always_ff, comb blocks as always_comb; lint with verilator."""
import shutil
import subprocess

import pytest

import zuspec.ir.core as ir
from zuspec.be.sv.proc_emit import proc_block_lines
from zuspec.be.sv.structural import StructuralEmitter


def u(bits):
    return ir.DataTypeInt(bits=bits, signed=False)


def port(name, bits=1, out=False):
    return ir.FieldInOut(name=name, datatype=u(bits), is_out=out)


def f(i):
    return ir.ExprRefField(base=ir.TypeExprRefSelf(), index=i)


def cnt(active_low=True, asynch=True):
    """The ``cnt`` class of zuspec-fe-sv's test_proc_mapper, as ProcMapper
    maps it, with its default clock and reset resolved to ``clk``/``rst_n``."""
    rst = ir.DomainRef(kind="fw_reset_kind", target=[])
    step = ir.ProcBlock(
        name="step", kind=ir.ProcKind.STEP, func="step",
        clock=ir.DomainRef(kind="fw_clock_kind", target=[]), reset=rst,
        body=[ir.StmtIf(
            test=ir.ExprUnary(op=ir.UnaryOp.Not, operand=ir.ExprInReset(
                reset=rst, net="rst_n" if active_low else "rst",
                active_low=active_low)),
            body=[ir.StmtAssign(targets=[f(0)], value=ir.ExprBin(
                lhs=f(0), op=ir.BinOp.Add, rhs=ir.ExprConstant(value=1, width=8)))])],
        reset_body=[ir.StmtAssign(targets=[f(0)], value=ir.ExprConstant(value=0x5a, width=8))],
        writes=[["q"]], clock_net="clk", reset_net="rst_n" if active_low else "rst",
        reset_active_low=active_low, reset_async=asynch)
    c = ir.DataTypeComponent(name="cnt", super=None, fields=[
        port("q", 8, out=True), port("clk"), port("rst_n" if active_low else "rst")])
    c.flat_structural = True
    c.proc_blocks = [step]
    return c


def two():
    y = ir.ProcBlock(name="comb", kind=ir.ProcKind.COMB, func="comb", body=[
        ir.StmtAssign(targets=[f(2)], value=ir.ExprBin(lhs=f(0), op=ir.BinOp.BitAnd, rhs=f(1)))])
    c = ir.DataTypeComponent(name="two", super=None,
                             fields=[port("a"), port("b"), port("y", out=True)])
    c.flat_structural = True
    c.proc_blocks = [y]
    return c


def gen():
    """fw-hdl-spec's test_rst_gen: state ``n`` counts to 2 (a blocking write
    in the class: ``rst_n`` sees the new ``n``)."""
    two_ = ir.ExprConstant(value=2, width=2)
    step = ir.ProcBlock(
        name="step", kind=ir.ProcKind.STEP, func="step",
        clock=ir.DomainRef(kind="fw_clock_kind", target=[]), clock_net="clk",
        body=[ir.StmtIf(test=ir.ExprBin(lhs=f(2), op=ir.CmpOp.NotEq, rhs=two_),
                        body=[ir.StmtAssign(targets=[f(2)], value=ir.ExprBin(
                            lhs=f(2), op=ir.BinOp.Add, rhs=ir.ExprConstant(value=1, width=2)))]),
              ir.StmtAssign(targets=[f(1)], value=ir.ExprBin(lhs=f(2), op=ir.CmpOp.Eq, rhs=two_))],
        writes=[["rst_n"], ["n"]], state=["n"])
    c = ir.DataTypeComponent(name="gen", super=None, fields=[
        port("clk"), port("rst_n", out=True), ir.Field(name="n", datatype=u(2))])
    c.flat_structural = True
    c.proc_blocks = [step]
    return c


def test_step_state():
    lines = [ln.strip() for ln in proc_block_lines(gen())]
    i = lines.index("always_ff @(posedge clk) begin")
    assert lines[i + 1:i + 3] == ["logic [1:0] n_v;", "n_v = n;"]
    assert "n_v = n_v + 2'd1;" in lines
    assert "rst_n <= n_v == 2'd2;" in lines
    assert lines[-3:] == ["n <= n_v;", "end", ""]


def test_step_async_active_low():
    text = "\n".join(proc_block_lines(cnt()))
    assert "always_ff @(posedge clk or negedge rst_n) begin" in text
    assert "if (!rst_n) begin" in text
    assert "q <= 8'd90;" in text
    assert "if (rst_n) begin" in text
    assert "q <= q + 8'd1;" in text


def test_step_sync_active_high():
    text = "\n".join(proc_block_lines(cnt(active_low=False, asynch=False)))
    assert "always_ff @(posedge clk) begin" in text
    assert "if (rst) begin" in text
    assert "if (!rst) begin" in text


def test_comb():
    text = "\n".join(proc_block_lines(two()))
    assert "always_comb begin" in text
    assert "y = a & b;" in text


def test_unresolved_clock():
    c = cnt()
    c.proc_blocks[0].clock_net = ""
    with pytest.raises(ValueError, match="clock not resolved"):
        proc_block_lines(c)


@pytest.mark.skipif(shutil.which("verilator") is None, reason="verilator not on PATH")
@pytest.mark.parametrize("make,top", [(cnt, "cnt"), (two, "two"), (gen, "gen")])
def test_verilator_lint(tmp_path, make, top):
    sv = tmp_path / f"{top}.sv"
    sv.write_text(StructuralEmitter(make()).module())
    r = subprocess.run(["verilator", "--lint-only", "-Wall", "--top-module", top, str(sv)],
                       capture_output=True, text=True, cwd=tmp_path)
    assert r.returncode == 0, sv.read_text() + r.stdout + r.stderr
    assert "%Warning" not in r.stderr, r.stderr

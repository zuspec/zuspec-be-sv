"""RTLAlways ↔ structured SVStmt bridge (process→FSM unification, phase U-1).

An FSM lowered to be.sv IR emits its next-state/output logic as an ``always``
block whose body is a structured ``SVStmtCase``/``SVStmtIf`` (not raw strings).
These tests pin that ``RTLEmitter`` delegates ``SVStmt`` items in an
``RTLAlways.body`` to ``SVStmtEmitter`` while remaining backward-compatible with
string and ``RTLExpr`` bodies.
"""
import zuspec.ir.core as ir

from zuspec.be.sv.ir import stmt as s
from zuspec.be.sv.ir.rtl import RTLModule, RTLPort, RTLWire, RTLAlways, PortDirection
from zuspec.be.sv.ir.rtl_emit import RTLEmitter


def _ref(n):
    return ir.ExprRefLocal(name=n)


def test_always_body_accepts_structured_case():
    # A minimal FSM next-state block: case(state) IDLE: next=RUN; default: next=state;
    case = s.SVStmtCase(
        subject=_ref("state"),
        items=[
            s.SVCaseItem(
                labels=[_ref("IDLE")],
                body=[s.SVStmtAssign(lhs=_ref("next_state"), rhs=_ref("RUN"))],
            ),
            s.SVCaseItem(  # empty labels => default
                labels=[],
                body=[s.SVStmtAssign(lhs=_ref("next_state"), rhs=_ref("state"))],
            ),
        ],
    )
    mod = RTLModule(
        name="fsm",
        ports=[RTLPort(name="clk", direction=PortDirection.INPUT)],
        wires=[RTLWire(name="state", dtype="state_t"),
               RTLWire(name="next_state", dtype="state_t")],
        always_blocks=[RTLAlways(sensitivity=["posedge clk"], body=[case])],
    )
    sv = RTLEmitter().emit_module(mod)
    assert "always @(posedge clk) begin" in sv
    assert "case (state)" in sv
    assert "IDLE: begin" in sv
    assert "next_state = RUN;" in sv
    assert "default: begin" in sv
    assert "endcase" in sv


def test_always_body_accepts_structured_if():
    stmt = s.SVStmtIf(
        cond=ir.ExprBin(lhs=_ref("ack"), op=ir.BinOp.Add, rhs=ir.ExprConstant(value=0)),
        then_body=[s.SVStmtAssign(lhs=_ref("data"), rhs=_ref("rdata"))],
    )
    mod = RTLModule(name="m", always_blocks=[RTLAlways(sensitivity=[], body=[stmt])])
    sv = RTLEmitter().emit_module(mod)
    assert "always @(*) begin" in sv
    assert "if (" in sv
    assert "data = rdata;" in sv


def test_always_body_still_accepts_strings():
    # Backward compatibility: raw-string bodies (the pre-existing contract).
    mod = RTLModule(
        name="m",
        always_blocks=[RTLAlways(sensitivity=["posedge clk"], body=["x <= y;"])],
    )
    sv = RTLEmitter().emit_module(mod)
    assert "x <= y;" in sv

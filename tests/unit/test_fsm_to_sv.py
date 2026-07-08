"""FsmToSVPass core-shape builder (process→FSM unification, phase U-2).

Builds a hand-constructed mem_reader-shaped ``FSMModule`` and checks that
``build_fsm_sv`` maps it to be.sv IR whose ``SVEmitter`` output has the expected
state enum, ports, and next-state/output ``case`` structure — i.e. the FSM SV is
produced by be.sv, not by ``sprtl/sv_codegen``.
"""
import pytest

from zuspec.synth.sprtl.fsm_ir import (
    FSMModule, FSMState, FSMPort, FSMRegister, FSMTransition, FSMAssign, FSMCond,
    FSMPortCall, FSMPortOutput,
)
from zuspec.be.sv.passes.fsm_to_sv import build_fsm_sv, FsmSVUnsupported
from zuspec.be.sv.ir.sv_emit import SVEmitter


def _mem_reader_fsm() -> FSMModule:
    idle = FSMState(id=0, name="IDLE", transitions=[FSMTransition(target_state=1)])
    req = FSMState(
        id=1, name="MEM_READ_WORD_REQ",
        transitions=[FSMTransition(target_state=0, condition="mem_read_word_ack")],
        operations=[
            FSMAssign(target="mem_read_word_valid", value=1),
            FSMAssign(target="mem_read_word_arg0", value=0),
            FSMCond(condition="mem_read_word_ack",
                    then_ops=[FSMAssign(target="data", value="mem_read_word_rdata")]),
        ],
    )
    return FSMModule(
        name="MemReader",
        ports=[
            FSMPort(name="mem_read_word_valid", direction="output", width=1),
            FSMPort(name="mem_read_word_arg0", direction="output", width=32),
            FSMPort(name="mem_read_word_ack", direction="input", width=1),
            FSMPort(name="mem_read_word_rdata", direction="input", width=32),
        ],
        registers=[FSMRegister(name="data", width=32)],
        states=[idle, req],
        initial_state=0,
    )


def _sv(fsm):
    return SVEmitter().emit_all(build_fsm_sv(fsm))


def test_state_enum_and_module():
    sv = _sv(_mem_reader_fsm())
    assert "typedef enum" in sv
    assert "IDLE" in sv and "MEM_READ_WORD_REQ" in sv
    assert "module MemReader" in sv
    assert "state_t state;" in sv and "state_t next_state;" in sv


def test_ports_present():
    sv = _sv(_mem_reader_fsm())
    assert "input" in sv and "output" in sv
    for p in ("mem_read_word_valid", "mem_read_word_arg0",
              "mem_read_word_ack", "mem_read_word_rdata"):
        assert p in sv


def test_registered_signals_are_logic_and_typedef_vars_bare():
    sv = _sv(_mem_reader_fsm())
    assert "output logic mem_read_word_valid" in sv   # procedurally driven
    assert "logic [31:0] data;" in sv                 # register
    assert "state_t state;" in sv                     # typedef'd var, no net kw
    assert "wire state_t" not in sv


def test_cases_have_default_arms():
    sv = _sv(_mem_reader_fsm())
    # both case statements are complete (Verilator CASEINCOMPLETE-clean)
    assert sv.count("default:") == 2


def test_next_state_case_and_transitions():
    sv = _sv(_mem_reader_fsm())
    assert "case (state)" in sv
    assert "next_state = state;" in sv        # comb default
    assert "next_state = MEM_READ_WORD_REQ;" in sv   # IDLE -> REQ (unconditional)
    assert "if (mem_read_word_ack)" in sv     # REQ -> IDLE (conditional)
    assert "next_state = IDLE;" in sv


def test_output_datapath_registered():
    sv = _sv(_mem_reader_fsm())
    assert "always @(posedge clk)" in sv
    assert "mem_read_word_valid <= 1;" in sv
    assert "data <= mem_read_word_rdata;" in sv


def test_reset_in_state_register():
    sv = _sv(_mem_reader_fsm())
    assert "if (!rst_n)" in sv
    assert "state <= IDLE;" in sv
    assert "state <= next_state;" in sv


def test_output_ff_has_reset_block():
    # The output/datapath always_ff zeroes outputs + registers on reset,
    # matching the docs/baseline_sv golden shape.
    sv = _sv(_mem_reader_fsm())
    assert "mem_read_word_valid <= 0;" in sv   # output reset
    assert "mem_read_word_arg0 <= 0;" in sv    # output reset
    assert "data <= 0;" in sv                  # register reset
    # Two reset conditions now: state register + output ff.
    assert sv.count("if (!rst_n)") == 2


# --------------------------------------------------------------------------- #
# FSMPortCall / FSMPortOutput — handshake port inference + emission
# --------------------------------------------------------------------------- #

def _portcall_fsm() -> FSMModule:
    """A mem_reader whose read is expressed as an awaited FSMPortCall.

    The ``mem_read_word_{valid,arg0,ack,rdata}`` ports and the ``rdata`` result
    register are NOT pre-declared — they must be inferred by build_fsm_sv.
    """
    idle = FSMState(id=0, name="IDLE", transitions=[FSMTransition(target_state=1)])
    req = FSMState(
        id=1, name="MEM_READ_WORD_REQ",
        transitions=[FSMTransition(target_state=0, condition="mem_read_word_ack")],
        operations=[
            FSMPortCall(port_name="mem", method_name="read_word",
                        arg_exprs=[0], result_var="rdata"),
        ],
    )
    return FSMModule(name="MemReaderPC", ports=[], registers=[],
                     states=[idle, req], initial_state=0)


def test_portcall_ports_inferred():
    sv = _sv(_portcall_fsm())
    assert "output logic mem_read_word_valid" in sv
    assert "output logic [31:0] mem_read_word_arg0" in sv
    assert "input  wire mem_read_word_ack" in sv
    assert "input  wire [31:0] mem_read_word_rdata" in sv


def test_portcall_result_register_inferred():
    sv = _sv(_portcall_fsm())
    assert "logic [31:0] rdata;" in sv          # result register declared
    assert "rdata <= 0;" in sv                  # reset


def test_portcall_handshake_emitted():
    sv = _sv(_portcall_fsm())
    assert "mem_read_word_valid <= 1'b1;" in sv   # 1-bit strobe
    assert "mem_read_word_arg0 <= 0;" in sv
    assert "if (mem_read_word_ack)" in sv
    assert "rdata <= mem_read_word_rdata;" in sv


def test_portoutput_no_ack_or_result():
    fsm = FSMModule(
        name="MonWriter", ports=[], registers=[],
        states=[FSMState(id=0, name="IDLE", operations=[
            FSMPortOutput(port_name="monitor", method_name="on_event",
                          arg_exprs=[42])])],
        initial_state=0, single_state=False)
    sv = _sv(fsm)
    assert "output logic monitor_on_event_valid" in sv
    assert "output logic [31:0] monitor_on_event_arg0" in sv
    assert "monitor_on_event_ack" not in sv     # non-awaited: no ack port
    assert "monitor_on_event_valid <= 1'b1;" in sv   # 1-bit strobe


def test_unsupported_shape_raises():
    fsm = _mem_reader_fsm()
    fsm.user_structs = [object()]   # a struct-bearing FSM is out of core envelope
    with pytest.raises(FsmSVUnsupported):
        build_fsm_sv(fsm)


def test_wait_cycles_state_falls_back():
    # A WAIT_CYCLES state (wait_cycles>1) needs cycle-counter logic the core
    # builder does not emit → must raise so the caller uses the legacy generator
    # (else the SV would be functionally incomplete: missing the counter).
    from zuspec.synth.sprtl.fsm_ir import FSMStateKind
    fsm = _mem_reader_fsm()
    fsm.states[1].kind = FSMStateKind.WAIT_CYCLES
    fsm.states[1].wait_cycles = 8
    with pytest.raises(FsmSVUnsupported):
        build_fsm_sv(fsm)

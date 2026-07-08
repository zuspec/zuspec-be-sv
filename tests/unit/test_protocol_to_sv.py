"""build_fifo_sv structural migration (protocol emit → be.sv, phase U-3).

Checks that ``build_fifo_sv`` maps a ``QueueIR`` to a be.sv ``RTLModule`` whose
``SVEmitter`` output is a structurally-faithful synchronous FIFO — i.e. the FIFO
SV is produced by be.sv, not by ``sprtl/protocol_sv.generate_fifo_sv``.

Functional equivalence to the legacy generator was verified out-of-band with a
2000-cycle random-stimulus Verilator co-simulation (0 mismatches on
full/empty/count/rd_data); this unit test guards the structure + key invariants.
"""
import pytest

from zuspec.synth.ir.protocol_ir import (
    QueueIR, SelectIR, SelectBranchIR,
    IfProtocolPortIR, IfProtocolScenario, ProtocolField,
)
from zuspec.be.sv.passes.protocol_to_sv import (
    build_fifo_sv, build_priority_arbiter_sv, build_rr_arbiter_sv,
    build_ifprotocol_port_decls, build_ifprotocol_port_instantiation,
    ProtocolSVUnsupported,
)
from zuspec.be.sv.ir.sv_emit import SVEmitter
from zuspec.be.sv.ir.rtl import RTLModule, RTLMemory


def _sv(q):
    return SVEmitter().emit_all(build_fifo_sv(q))


def test_module_and_ports():
    sv = _sv(QueueIR(name="requests", elem_width=32, depth=16))
    assert "module requests_fifo" in sv
    for p in ("wr_en", "wr_data", "full", "rd_en", "rd_data", "empty", "count"):
        assert p in sv
    assert "output logic [31:0] rd_data" in sv   # registered output
    assert "output logic full" in sv


def test_memory_and_pointers_scale_with_depth():
    sv = _sv(QueueIR(name="q", elem_width=8, depth=16))
    assert "logic [7:0] mem [0:15];" in sv        # depth-16, width-8 memory
    assert "logic [3:0] wr_ptr;" in sv            # addr_bits = 4
    assert "logic [3:0] rd_ptr;" in sv
    assert "logic [4:0] count_r;" in sv           # count_bits = 5


def test_status_assigns():
    sv = _sv(QueueIR(name="q", elem_width=8, depth=16))
    assert "assign full = (count_r == 16);" in sv
    assert "assign empty = (count_r == 0);" in sv
    assert "assign count = count_r;" in sv


def test_registered_read_and_write():
    sv = _sv(QueueIR(name="q", elem_width=8, depth=8))
    assert "rd_data <= mem[rd_ptr];" in sv
    assert "mem[wr_ptr] <= wr_data;" in sv
    assert "if (rst) begin" in sv                 # sync reset


def test_count_update_ifelse_equivalent_to_case():
    # The legacy `case ({wr,rd})` count update becomes an if/else-if chain
    # (10 -> +1, 01 -> -1, else hold) — no core concat node exists.
    sv = _sv(QueueIR(name="q", elem_width=8, depth=8))
    assert "count_r <= (count_r + 1);" in sv
    assert "count_r <= (count_r - 1);" in sv


def test_returns_single_rtl_module():
    nodes = build_fifo_sv(QueueIR(name="q", elem_width=8, depth=8))
    assert len(nodes) == 1 and isinstance(nodes[0], RTLModule)
    assert any(isinstance(m, RTLMemory) for m in nodes[0].memories)


def test_module_prefix():
    sv = _sv_prefixed = SVEmitter().emit_all(
        build_fifo_sv(QueueIR(name="q", elem_width=8, depth=8), module_prefix="p_"))
    assert "module p_q_fifo" in sv


# --------------------------------------------------------------------------- #
# fixed-priority arbiter (build_priority_arbiter_sv)
# --------------------------------------------------------------------------- #

def _arb_sv(n):
    sel = SelectIR(name="s", branches=[SelectBranchIR(f"q{i}", i) for i in range(n)])
    return SVEmitter().emit_all(build_priority_arbiter_sv(sel))


def test_arbiter_module_and_ports():
    sv = _arb_sv(4)
    assert "module s_arb" in sv
    assert "input  wire [3:0] req_i" in sv
    assert "output logic [3:0] gnt_o" in sv
    assert "output logic [1:0] sel_o" in sv        # idx_bits = 2
    assert "output logic gnt_valid_o" in sv


def test_arbiter_priority_chain():
    # branch 0 highest priority; each later branch guarded by !req_i[<earlier>]
    sv = _arb_sv(4)
    assert "always @(*)" in sv                      # combinational
    assert "if (req_i[0])" in sv
    assert "gnt_o[0] = 1'b1;" in sv
    assert "req_i[1] && !(req_i[0])" in sv
    assert "req_i[3] && !(req_i[0]) && !(req_i[1]) && !(req_i[2])" in sv


def test_arbiter_defaults_prevent_latch():
    sv = _arb_sv(3)
    # every output defaulted at top of the comb block (no inferred latch)
    assert "gnt_o = 0;" in sv
    assert "sel_o = 0;" in sv
    assert "gnt_valid_o = 1'b0;" in sv


def test_arbiter_round_robin_falls_back():
    sel = SelectIR(name="s", branches=[SelectBranchIR("q0", 0)], round_robin=True)
    with pytest.raises(ProtocolSVUnsupported):
        build_priority_arbiter_sv(sel)


def test_arbiter_empty_select_falls_back():
    with pytest.raises(ProtocolSVUnsupported):
        build_priority_arbiter_sv(SelectIR(name="s", branches=[]))


# --------------------------------------------------------------------------- #
# round-robin arbiter (build_rr_arbiter_sv)
# --------------------------------------------------------------------------- #

def _rr_sv(n):
    sel = SelectIR(name="s", branches=[SelectBranchIR(f"q{i}", i) for i in range(n)],
                   round_robin=True)
    return SVEmitter().emit_all(build_rr_arbiter_sv(sel))


def test_rr_module_and_ports():
    sv = _rr_sv(4)
    assert "module s_rr_arb" in sv           # distinct name from priority _arb
    assert "input  wire clk" in sv           # clocked (stateful mask)
    assert "input  wire rst" in sv
    assert "input  wire [3:0] req_i" in sv
    assert "output logic [3:0] gnt_o" in sv
    assert "output logic [1:0] sel_o" in sv  # idx_bits = 2
    assert "output logic gnt_valid_o" in sv


def test_rr_mask_and_encoders():
    sv = _rr_sv(4)
    assert "logic [3:0] mask_r;" in sv                     # rotating mask register
    assert "assign masked_req = (req_i & mask_r);" in sv   # req masked by mask
    # fall-through: masked grant when non-zero, else unmasked grant
    assert "(masked_gnt != 0) ? masked_gnt : unmasked_gnt" in sv
    assert "assign gnt_valid_o = (req_i != 0);" in sv
    # both priority encoders are structural for-loops
    assert sv.count("for (int i = 0; i < 4; i++)") >= 3


def test_rr_sel_is_truncating_partselect():
    # sel_o = idx_bits'(i) emitted as the equivalent part-select i[idx_bits-1:0]
    sv = _rr_sv(4)
    assert "sel_o = i[1:0];" in sv


def test_rr_mask_reset_and_rotation():
    sv = _rr_sv(4)
    assert "mask_r <= 15;" in sv                            # '1 == (2**4)-1
    assert "mask_r <= ((1 << ((i + 1) % 4)) - 1);" in sv    # rotate past winner


def test_rr_priority_select_falls_back():
    sel = SelectIR(name="s", branches=[SelectBranchIR("q0", 0)], round_robin=False)
    with pytest.raises(ProtocolSVUnsupported):
        build_rr_arbiter_sv(sel)


def test_rr_empty_select_falls_back():
    with pytest.raises(ProtocolSVUnsupported):
        build_rr_arbiter_sv(SelectIR(name="s", branches=[], round_robin=True))


# --------------------------------------------------------------------------- #
# IfProtocol port declarations / instantiation
# --------------------------------------------------------------------------- #

def _mem_port():
    return IfProtocolPortIR(name="mem", scenario=IfProtocolScenario.B,
                            req_fields=[ProtocolField("addr", 32),
                                        ProtocolField("wdata", 32)],
                            resp_fields=[ProtocolField("rdata", 32)])


def test_port_decls_fragment():
    frag = build_ifprotocol_port_decls(_mem_port())
    # default handshake: req_valid (out), req_ready (in), fields, resp_valid (in)
    assert "output logic mem_req_valid," in frag
    assert "input logic mem_req_ready," in frag
    assert "output logic [31:0] mem_req_addr," in frag
    assert "output logic [31:0] mem_req_wdata," in frag
    assert "input logic mem_resp_valid," in frag
    assert "input logic [31:0] mem_resp_rdata," in frag
    # every line ends with a comma (fragment spliced into a module header)
    assert all(l.rstrip().endswith(",") for l in frag.splitlines() if l.strip())
    # width-1 signals carry no bit-range spec
    assert "[0:0]" not in frag


def test_port_decls_export_flips_direction():
    p = _mem_port()
    p.is_export = True
    frag = build_ifprotocol_port_decls(p)
    assert "input logic mem_req_valid," in frag       # flipped vs port
    assert "output logic mem_resp_valid," in frag


def test_port_instantiation_fragment():
    frag = build_ifprotocol_port_instantiation(_mem_port())
    assert ".mem_req_valid(mem_req_valid)," in frag
    assert ".mem_req_addr(mem_req_addr)," in frag
    assert ".mem_resp_rdata(mem_resp_rdata)," in frag
    # connection count matches the port count
    assert frag.count("(") == len(_mem_port().all_sv_ports())

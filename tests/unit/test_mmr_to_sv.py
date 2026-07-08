"""build_mmr_regfile_sv — MMR register-file structural migration (U-5, slice 1).

Checks that ``build_mmr_regfile_sv`` / ``build_mmr_regfile_package`` map a
``@zdc.regfile`` class to be.sv IR whose emitted SV is a structurally-faithful,
plain-CSR register file — produced by be.sv, not the legacy
``mmr_regfile_emit.MmrRegFileRtlEmitter``.

Functional equivalence to the legacy emitter was verified out-of-band with a
600-transaction APB read/write Verilator co-simulation (0 mismatches on prdata and
every hwif_out field value); this unit test guards the structure + slice-1 envelope.
"""
import pytest

import zuspec.dataclasses as zdc
from zuspec.dataclasses.mmr.base import RegisterFile

from zuspec.be.sv.passes.mmr_to_sv import (
    build_mmr_regfile_sv, build_mmr_regfile_package, MmrSVUnsupported,
)
from zuspec.be.sv.ir.sv_emit import SVEmitter
from zuspec.be.sv.ir.rtl import RTLModule


@zdc.regfile
class PlainRegs(RegisterFile):
    @zdc.reg(offset=0x00, width=32)
    class CTRL:
        EN: zdc.u1 = zdc.reg_field(default=0)
        MODE: zdc.u4 = zdc.reg_field(default=3)

    @zdc.reg(offset=0x04, width=32)
    class DATA:
        VAL: zdc.u32 = zdc.reg_field(default=0)


@zdc.regfile
class PulseRegs(RegisterFile):
    @zdc.reg(offset=0x00, width=32)
    class CTRL:
        START: zdc.u1 = zdc.FieldAttr.Pulse
        MODE: zdc.u4 = zdc.reg_field(default=5)


@zdc.regfile
class StickyRegs(RegisterFile):
    @zdc.reg(offset=0x00, width=32)
    class STATUS:
        DONE:  zdc.u1 = zdc.FieldAttr.StickyBit
        ERROR: zdc.u1 = zdc.FieldAttr.StickyBit


@zdc.regfile
class WoclrRegs(RegisterFile):
    @zdc.reg(offset=0x00, width=32)
    class MASK:
        VAL: zdc.u8 = zdc.reg_field(sw=zdc.SW.RW, hw=zdc.HW.R, onwrite='woclr', default=0xFF)


@zdc.regfile
class RoHwWriteRegs(RegisterFile):
    # sw=RO + hw=W → the legacy dangling-`else` shape; still falls back.
    @zdc.reg(offset=0x00, width=32)
    class STATUS:
        BUSY: zdc.u1 = zdc.reg_field(sw=zdc.SW.RO, hw=zdc.HW.W, default=0)


def _mod(rf, **kw):
    return SVEmitter().emit_all(build_mmr_regfile_sv(rf, **kw))


def _pkg(rf, **kw):
    return SVEmitter().emit_all(build_mmr_regfile_package(rf, **kw))


# --------------------------------------------------------------------------- #
# module
# --------------------------------------------------------------------------- #

def test_module_and_apb_ports():
    sv = _mod(PlainRegs, module_name="plain")
    assert "module plain" in sv
    for p in ("clk", "rst", "psel", "penable", "pwrite", "paddr",
              "pwdata", "prdata", "pready", "pslverr"):
        assert p in sv
    assert "input  plain__in_t hwif_in" in sv       # typedef-typed port (RTLPort.dtype)
    assert "output plain__out_t hwif_out" in sv
    assert "assign pready = 1'd1;" in sv             # APB zero-wait tie-offs
    assert "assign pslverr = 1'd0;" in sv
    assert "assign cpuif_req = (psel & penable);" in sv


def test_field_storage_and_decode():
    sv = _mod(PlainRegs, module_name="plain")
    assert "logic field_storage_CTRL_EN;" in sv
    assert "logic [3:0] field_storage_CTRL_MODE;" in sv       # 4-bit field
    assert "logic [31:0] field_storage_DATA_VAL;" in sv
    # address decode strobes, one per register
    assert "decoded_reg_strb_CTRL = 1'b1;" in sv
    assert "decoded_reg_strb_DATA = 1'b1;" in sv


def test_field_comb_and_ff():
    sv = _mod(PlainRegs, module_name="plain")
    # SW write path: strobe & write → next = wr_data slice, load
    assert "if (decoded_reg_strb_CTRL && cpuif_req_is_wr)" in sv
    assert "field_combo_next_CTRL_EN = cpuif_wr_data[0];" in sv
    assert "field_combo_next_CTRL_MODE = cpuif_wr_data[4:1];" in sv  # MODE packed at lsb 1
    # ff with reset to default and load-enable
    assert "field_storage_CTRL_EN <= 1'b0;" in sv             # default 0
    assert "field_storage_CTRL_MODE <= 3;" in sv              # default 3
    assert "if (field_combo_load_CTRL_EN) begin" in sv


def test_read_mux_and_hwif_out():
    sv = _mod(PlainRegs, module_name="plain")
    assert "prdata = 0;" in sv
    assert "prdata[0] = field_storage_CTRL_EN;" in sv
    assert "prdata[4:1] = field_storage_CTRL_MODE;" in sv
    assert "assign hwif_out.CTRL.EN_value = field_storage_CTRL_EN;" in sv


def test_returns_single_rtl_module():
    nodes = build_mmr_regfile_sv(PlainRegs, module_name="plain")
    assert len(nodes) == 1 and isinstance(nodes[0], RTLModule)


# --------------------------------------------------------------------------- #
# package (nested anonymous packed structs)
# --------------------------------------------------------------------------- #

def test_package_nested_structs():
    sv = _pkg(PlainRegs, module_name="plain")
    assert "package plain_pkg;" in sv
    assert "typedef struct packed {" in sv
    # out_t: nested per-register struct with *_value members
    assert "logic EN_value;" in sv
    assert "logic [3:0] MODE_value;" in sv
    assert "} CTRL;" in sv
    assert "} plain__out_t;" in sv


def test_package_empty_in_t_gets_reserved_placeholder():
    # slice-1 regfiles have no hwif_in signals; an empty packed struct is illegal
    # SV, so a reserved placeholder is emitted instead.
    sv = _pkg(PlainRegs, module_name="plain")
    assert "logic _reserved;" in sv
    assert "} plain__in_t;" in sv


# --------------------------------------------------------------------------- #
# slice 2: singlepulse (Pulse) fields
# --------------------------------------------------------------------------- #

def test_singlepulse_auto_clear():
    sv = _mod(PulseRegs, module_name="pr")
    # SW write path still present, plus the auto-clear: field set & not being
    # written this cycle → drive back to zero.
    assert "field_combo_next_CTRL_START = cpuif_wr_data[0];" in sv
    assert ("(field_storage_CTRL_START != 1'b0) && "
            "!(decoded_reg_strb_CTRL && cpuif_req_is_wr)") in sv


def test_singlepulse_swmod_output():
    sv = _mod(PulseRegs, module_name="pr")
    assert ("assign hwif_out.CTRL.START_swmod = "
            "(decoded_reg_strb_CTRL & cpuif_req_is_wr);") in sv


def test_singlepulse_package_has_value_and_swmod():
    sv = _pkg(PulseRegs, module_name="pr")
    assert "logic START_value;" in sv
    assert "logic START_swmod;" in sv


# --------------------------------------------------------------------------- #
# slice 3: stickybit + edge-detect + onwrite + interrupt aggregation
# --------------------------------------------------------------------------- #

def test_stickybit_woclr_and_posedge():
    sv = _mod(StickyRegs, module_name="st")
    # SW write clears (woclr): next = sv & ~wr_data_bit
    assert "field_combo_next_STATUS_DONE = (field_storage_STATUS_DONE & ~(cpuif_wr_data[0]));" in sv
    # HW posedge sticky set, chained as the SW block's else
    assert "if ((~(field_q_STATUS_DONE) & hwif_in.STATUS.DONE_hwset)) begin" in sv
    assert "field_combo_next_STATUS_DONE = (field_storage_STATUS_DONE | hwif_in.STATUS.DONE_hwset);" in sv


def test_stickybit_field_q_register():
    sv = _mod(StickyRegs, module_name="st")
    assert "logic field_q_STATUS_DONE;" in sv
    assert "field_q_STATUS_DONE <= hwif_in.STATUS.DONE_hwset;" in sv


def test_interrupt_aggregation():
    sv = _mod(StickyRegs, module_name="st")
    assert ("assign hwif_out.STATUS.intr = "
            "(field_storage_STATUS_DONE | field_storage_STATUS_ERROR);") in sv


def test_stickybit_package_hwset_and_intr():
    sv = _pkg(StickyRegs, module_name="st")
    assert "logic DONE_hwset;" in sv          # hwif_in gets the hwset input
    assert "logic ERROR_hwset;" in sv
    assert "logic intr;" in sv                # hwif_out gets the aggregated intr


def test_onwrite_woclr_isolated():
    sv = _mod(WoclrRegs, module_name="wc")
    assert "field_combo_next_MASK_VAL = (field_storage_MASK_VAL & ~(cpuif_wr_data[7:0]));" in sv
    assert "assign hwif_out.MASK.VAL_value = field_storage_MASK_VAL;" in sv  # hw=R value


# --------------------------------------------------------------------------- #
# envelope: fall back for still-unmapped field semantics
# --------------------------------------------------------------------------- #

def test_ro_hw_write_falls_back():
    # sw=RO + hw=W is the legacy dangling-`else` shape (never compiled) → fallback.
    with pytest.raises(MmrSVUnsupported):
        build_mmr_regfile_sv(RoHwWriteRegs)

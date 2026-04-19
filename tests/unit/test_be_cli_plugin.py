"""Unit tests for the zuspec-be-sv CLI plugin (RTLSVBackend, _wrap_module)."""
from __future__ import annotations

import argparse
import io
import sys
import pytest

import zuspec.dataclasses as zdc
from zuspec.be.sv.cli_plugin import RTLSVBackend, _wrap_module, SVBackendPlugin
from zuspec.cli.ir import IR
from zuspec.cli.registry import Registry


# ---------------------------------------------------------------------------
# Minimal action for testing
# ---------------------------------------------------------------------------

@zdc.dataclass
class _TwoBit(zdc.Action):
    instr: zdc.u4 = zdc.input()
    out_a: zdc.u1 = zdc.rand()
    out_b: zdc.u1 = zdc.rand()

    @zdc.constraint
    def c(self):
        self.out_a == self.instr[0]
        self.out_b == self.instr[1]


def _ready_cc():
    """Return a minimized ConstraintCompiler for _TwoBit."""
    from zuspec.synth.sprtl.constraint_compiler import ConstraintCompiler
    cc = ConstraintCompiler(_TwoBit)
    cc.extract()
    cc.compute_support()
    cc.build_cubes()
    cc.build_odc_cubes()
    cc.minimize()
    return cc


# ---------------------------------------------------------------------------
# Tests for SVBackendPlugin
# ---------------------------------------------------------------------------

def test_backend_plugin_registers():
    reg = Registry()
    reg.reset()
    SVBackendPlugin().register(reg)
    be = reg.get_backend("rtl-sv")
    assert be is not None
    assert be.requires_ir_kind == "zuspec.constraint.compiler"


# ---------------------------------------------------------------------------
# Tests for _wrap_module
# ---------------------------------------------------------------------------

def test_wrap_module_contains_module_header():
    cc = _ready_cc()
    body = cc.emit_sv()
    text = _wrap_module(cc, body, "my_mod", "d")
    assert "module my_mod (" in text
    assert "endmodule" in text


def test_wrap_module_input_port_declared():
    cc = _ready_cc()
    body = cc.emit_sv()
    text = _wrap_module(cc, body, "my_mod", "d")
    assert "input  logic" in text
    assert "instr" in text


def test_wrap_module_output_ports_declared():
    cc = _ready_cc()
    body = cc.emit_sv()
    text = _wrap_module(cc, body, "my_mod", "d")
    assert "output logic" in text
    assert "out_a" in text
    assert "out_b" in text


def test_wrap_module_prefix_alias():
    cc = _ready_cc()
    body = cc.emit_sv()
    text = _wrap_module(cc, body, "my_mod", "d")
    # Should contain a wire alias bridging the port to the prefixed name
    assert "wire" in text
    assert "d_instr" in text


def test_wrap_module_output_assigns():
    cc = _ready_cc()
    body = cc.emit_sv()
    text = _wrap_module(cc, body, "my_mod", "d")
    assert "assign out_a = d_out_a" in text
    assert "assign out_b = d_out_b" in text


# ---------------------------------------------------------------------------
# Test RTLSVBackend.emit()
# ---------------------------------------------------------------------------

def test_rtlsv_backend_emit_stdout(capsys):
    cc = _ready_cc()
    ir = IR(payload=cc, kind="zuspec.constraint.compiler")
    be = RTLSVBackend()
    args = argparse.Namespace(top="_TwoBit", output="-", be_prefix="d", module_name=None)
    be.emit(ir, args)
    captured = capsys.readouterr()
    assert "module _TwoBit" in captured.out
    assert "endmodule" in captured.out


def test_rtlsv_backend_emit_file(tmp_path):
    cc = _ready_cc()
    ir = IR(payload=cc, kind="zuspec.constraint.compiler")
    be = RTLSVBackend()
    out_file = str(tmp_path / "out.sv")
    args = argparse.Namespace(top="_TwoBit", output=out_file, be_prefix="d", module_name=None)
    be.emit(ir, args)
    text = (tmp_path / "out.sv").read_text()
    assert "module _TwoBit" in text

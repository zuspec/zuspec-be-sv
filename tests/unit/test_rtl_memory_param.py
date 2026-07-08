"""RTLMemory + RTLParameter IR nodes (process→FSM unification, phase U-1).

Migrated structural capability be.sv previously lacked: memory/RAM arrays (for
FIFO/ROB/register-file structures) and parameterised modules (FIFO depth, ID
bits). These tests pin the node emission.
"""
from zuspec.be.sv.ir.rtl import (
    RTLModule, RTLPort, RTLMemory, RTLParameter, PortDirection,
)
from zuspec.be.sv.ir.rtl_emit import RTLEmitter


def _sv(mod):
    return RTLEmitter().emit_module(mod)


def test_memory_declaration():
    mod = RTLModule(
        name="ram",
        memories=[RTLMemory(name="mem", width=32, depth=16)],
    )
    sv = _sv(mod)
    assert "logic [31:0] mem [0:15];" in sv


def test_memory_1bit_and_typed():
    mod = RTLModule(
        name="ram",
        memories=[
            RTLMemory(name="flags", width=1, depth=8),
            RTLMemory(name="rob", dtype="rob_entry_t", depth=4),
        ],
    )
    sv = _sv(mod)
    assert "logic flags [0:7];" in sv
    assert "rob_entry_t rob [0:3];" in sv


def test_header_parameter():
    mod = RTLModule(
        name="fifo",
        params=[RTLParameter(name="DEPTH", value=16)],
        ports=[RTLPort(name="clk", direction=PortDirection.INPUT)],
    )
    sv = _sv(mod)
    assert "module fifo #(" in sv
    assert "parameter DEPTH = 16" in sv
    assert ") (" in sv


def test_localparam_in_body():
    mod = RTLModule(
        name="m",
        params=[RTLParameter(name="AW", value=4, local=True)],
    )
    sv = _sv(mod)
    assert "module m (" in sv           # no header param list
    assert "localparam AW = 4;" in sv


def test_no_params_keeps_plain_header():
    mod = RTLModule(name="m", ports=[RTLPort(name="clk")])
    sv = _sv(mod)
    assert "module m (" in sv
    assert "#(" not in sv

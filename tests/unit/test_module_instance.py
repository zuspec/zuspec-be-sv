"""Structural ModuleInstance emission, with and without parameter overrides."""
import zuspec.ir.core as ir
from zuspec.be.sv import SVGenerator


def _emit(tmp_path, comp):
    ctxt = ir.Context()
    ctxt.type_m[comp.name] = comp
    files = SVGenerator(tmp_path).generate(ctxt)
    return "\n".join(f.read_text() for f in files)


def test_module_instance_with_parameters(tmp_path):
    comp = ir.DataTypeComponent(
        name="top", super=None,
        fields=[ir.FieldInOut(name="clk", datatype=ir.DataTypeInt(bits=1, signed=False),
                              is_out=False)],
        module_instances=[
            ir.ModuleInstance(module="fw_rv_fifo", name="u_c1",
                              parameters={"WIDTH": "9", "DEPTH": "2"},
                              connections=[ir.PortConnection(port="clk", signal="clk")]),
            ir.ModuleInstance(module="blk", name="u_b",
                              connections=[ir.PortConnection(port="clk", signal="clk")])])
    sv = _emit(tmp_path, comp)
    assert "fw_rv_fifo #(.WIDTH(9), .DEPTH(2)) u_c1 (" in sv
    assert "  blk u_b (" in sv

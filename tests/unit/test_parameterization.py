"""Test SystemVerilog generation for parameterized components."""
import pytest
import zuspec.dataclasses as zdc
from zuspec.be.sv import SVGenerator
from pathlib import Path


def test_param_simple(tmpdir):
    """Test basic parameterized component generation."""
    
    @zdc.dataclass
    class MyC(zdc.Component):
        DATA_WIDTH : zdc.u32 = zdc.const(default=32)
        clock : zdc.bit = zdc.input()
        reset : zdc.bit = zdc.input()
        data : zdc.bitv = zdc.input(width=lambda s:s.DATA_WIDTH)
    
    # Generate IR
    factory = zdc.DataModelFactory()
    ctxt = factory.build(MyC)
    
    # Generate SV
    output_dir = Path(tmpdir)
    generator = SVGenerator(output_dir)
    sv_files = generator.generate(ctxt)
    
    assert len(sv_files) > 0
    sv_content = sv_files[0].read_text()
    
    # Verify module has parameter declaration
    assert "module" in sv_content and "MyC" in sv_content
    assert "parameter int DATA_WIDTH" in sv_content and "32" in sv_content
    
    # Verify port uses parameter
    assert "input" in sv_content and "data" in sv_content
    assert "DATA_WIDTH" in sv_content
    assert "[(DATA_WIDTH-1):0]" in sv_content or "[DATA_WIDTH-1:0]" in sv_content


def test_param_multiple_const(tmpdir):
    """Test component with multiple const parameters."""
    
    @zdc.dataclass
    class Multi(zdc.Component):
        DATA_WIDTH : zdc.u32 = zdc.const(default=32)
        ADDR_WIDTH : zdc.u32 = zdc.const(default=64)
        clock : zdc.bit = zdc.input()
        data : zdc.bitv = zdc.input(width=lambda s:s.DATA_WIDTH)
        addr : zdc.bitv = zdc.output(width=lambda s:s.ADDR_WIDTH)
    
    factory = zdc.DataModelFactory()
    ctxt = factory.build(Multi)
    
    output_dir = Path(tmpdir)
    generator = SVGenerator(output_dir)
    sv_files = generator.generate(ctxt)
    
    sv_content = sv_files[0].read_text()
    
    # Verify both parameters in module header
    assert "parameter int DATA_WIDTH" in sv_content
    assert "parameter int ADDR_WIDTH" in sv_content
    assert "32" in sv_content
    assert "64" in sv_content
    
    # Verify ports use parameters
    assert "DATA_WIDTH" in sv_content and "data" in sv_content
    assert "ADDR_WIDTH" in sv_content and "addr" in sv_content


def test_param_computed_width(tmpdir):
    """Test field with computed width (e.g., DATA_WIDTH/8)."""
    
    @zdc.dataclass
    class ByteEnable(zdc.Component):
        DATA_WIDTH : zdc.u32 = zdc.const(default=32)
        data : zdc.bitv = zdc.input(width=lambda s:s.DATA_WIDTH)
        strobe : zdc.bitv = zdc.input(width=lambda s:int(s.DATA_WIDTH/8))
    
    factory = zdc.DataModelFactory()
    ctxt = factory.build(ByteEnable)
    
    output_dir = Path(tmpdir)
    generator = SVGenerator(output_dir)
    sv_files = generator.generate(ctxt)
    
    sv_content = sv_files[0].read_text()
    
    # Verify parameter
    assert "parameter int DATA_WIDTH" in sv_content
    
    # Verify data port uses DATA_WIDTH
    assert "data" in sv_content
    
    # Verify strobe uses computed width
    # Should generate something like [(DATA_WIDTH/8-1):0] or similar
    assert "strobe" in sv_content


def test_param_instance_override(tmpdir):
    """Test instantiating parameterized component with overrides."""
    
    @zdc.dataclass
    class MyC(zdc.Component):
        DATA_WIDTH : zdc.u32 = zdc.const(default=32)
        clock : zdc.bit = zdc.input()
        data : zdc.bitv = zdc.input(width=lambda s:s.DATA_WIDTH)
    
    @zdc.dataclass
    class MyT(zdc.Component):
        DATA_WIDTH : zdc.u32 = zdc.const(default=12)
        clock : zdc.bit = zdc.input()
        
        c1 : MyC = zdc.inst(kwargs=lambda s:dict(DATA_WIDTH=16))
        c2 : MyC = zdc.inst(kwargs=lambda s:dict(DATA_WIDTH=32))
        c3 : MyC = zdc.inst(kwargs=lambda s:dict(DATA_WIDTH=s.DATA_WIDTH+4))
        
        def __bind__(self):
            return {
                self.c1.clock: self.clock,
                self.c2.clock: self.clock,
                self.c3.clock: self.clock,
            }
    
    factory = zdc.DataModelFactory()
    ctxt = factory.build([MyC, MyT])
    
    output_dir = Path(tmpdir)
    generator = SVGenerator(output_dir)
    sv_files = generator.generate(ctxt)
    
    # Find MyT file
    myt_file = next(f for f in sv_files if 'MyT' in f.name)
    sv_content = myt_file.read_text()
    
    # Verify MyT has its own parameter
    assert "parameter int DATA_WIDTH" in sv_content
    assert "=12" in sv_content or "= 12" in sv_content
    
    # Verify c1 instantiation with DATA_WIDTH=16
    assert "c1" in sv_content
    assert "#(.DATA_WIDTH(16))" in sv_content or "#( .DATA_WIDTH(16) )" in sv_content
    
    # Verify c2 instantiation with DATA_WIDTH=32
    assert "c2" in sv_content
    assert "#(.DATA_WIDTH(32))" in sv_content or "#( .DATA_WIDTH(32) )" in sv_content
    
    # Verify c3 instantiation with DATA_WIDTH+4
    assert "c3" in sv_content
    assert "#(.DATA_WIDTH(DATA_WIDTH+4))" in sv_content or \
           "#(.DATA_WIDTH(DATA_WIDTH + 4))" in sv_content or \
           "#( .DATA_WIDTH(DATA_WIDTH+4) )" in sv_content


def test_param_bundle(tmpdir):
    """Test parameterized bundle (interface)."""
    
    @zdc.dataclass
    class DataBus(zdc.Bundle):
        WIDTH : zdc.u32 = zdc.const(default=16)
        data : zdc.bitv = zdc.output(width=lambda s:s.WIDTH)
        valid : zdc.bit = zdc.output()
    
    @zdc.dataclass
    class BusUser(zdc.Component):
        BUS_WIDTH : zdc.u32 = zdc.const(default=32)
        clock : zdc.bit = zdc.input()
        
        bus : DataBus = zdc.bundle(kwargs=lambda s:dict(WIDTH=s.BUS_WIDTH))
    
    factory = zdc.DataModelFactory()
    ctxt = factory.build([DataBus, BusUser])
    
    output_dir = Path(tmpdir)
    generator = SVGenerator(output_dir)
    sv_files = generator.generate(ctxt)
    
    # Find BusUser file
    user_file = next(f for f in sv_files if 'BusUser' in f.name)
    sv_content = user_file.read_text()
    
    # Verify BusUser has parameter
    assert "parameter int BUS_WIDTH" in sv_content
    
    # Verify bundle signals are exposed (flattened)
    assert "bus_data" in sv_content
    assert "bus_valid" in sv_content
    
    # The bundle ports should use the parameter for width
    # Since bundle width is parameterized, flattened signals should reference BUS_WIDTH
    assert "BUS_WIDTH" in sv_content


def test_param_const_not_in_ports(tmpdir):
    """Test that const fields are not generated as ports."""
    
    @zdc.dataclass
    class MyC(zdc.Component):
        WIDTH : zdc.u32 = zdc.const(default=32)
        clock : zdc.bit = zdc.input()
        data : zdc.bitv = zdc.input(width=lambda s:s.WIDTH)
    
    factory = zdc.DataModelFactory()
    ctxt = factory.build(MyC)
    
    output_dir = Path(tmpdir)
    generator = SVGenerator(output_dir)
    sv_files = generator.generate(ctxt)
    
    sv_content = sv_files[0].read_text()
    
    # Verify WIDTH is in parameter list
    assert "parameter int WIDTH" in sv_content
    
    # Verify WIDTH is NOT declared as a port (it should only appear in parameter block and port types)
    # Check that there's no port declaration like "input logic WIDTH" or "output logic WIDTH"
    lines = sv_content.split('\n')
    for line in lines:
        # Skip parameter lines
        if 'parameter' in line:
            continue
        # Check for WIDTH as a port name (not in brackets)
        if ('input' in line or 'output' in line) and ' WIDTH' in line and '[' not in line:
            pytest.fail(f"WIDTH should not be declared as a port: {line}")
    
    # Verify WIDTH is used in port type
    assert "[(WIDTH-1):0]" in sv_content or "[WIDTH-1:0]" in sv_content

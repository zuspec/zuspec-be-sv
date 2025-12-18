import pytest
import zuspec.dataclasses as zdc
from zuspec.be.sv import SVGenerator
from pathlib import Path

def test_smoke(tmpdir):
    """Test basic SystemVerilog generation from Zuspec Component."""

    @zdc.dataclass
    class Counter(zdc.Component):
        clock : zdc.bit = zdc.input()
        reset : zdc.bit = zdc.input()
        count : zdc.bit32 = zdc.output()

        @zdc.sync(clock=lambda s:s.clock, reset=lambda s:s.reset)
        def _count(self):
            if self.reset:
                self.count = 0
            else:
                self.count += 1

    # Generate Zuspec to SV
    factory = zdc.DataModelFactory()
    ctxt = factory.build(Counter)
    
    output_dir = Path(tmpdir)
    generator = SVGenerator(output_dir)
    sv_files = generator.generate(ctxt)
    
    # Verify files were created
    assert len(sv_files) > 0
    assert sv_files[0].exists()
    
    # Check generated SystemVerilog
    sv_content = sv_files[0].read_text()
    
    # Verify module declaration (name will be sanitized with dots and angle brackets replaced)
    assert "module test_smoke" in sv_content and "Counter" in sv_content
    
    # Verify ports
    assert "input  clock" in sv_content or "input clock" in sv_content
    assert "input  reset" in sv_content or "input reset" in sv_content
    assert "output reg[31:0] count" in sv_content
    
    # Verify always block
    assert "always @(posedge clock or posedge reset)" in sv_content
    
    # Verify reset logic
    assert "if (reset)" in sv_content
    assert "count <= 0" in sv_content
    
    # Verify counter logic
    assert "count <= count + 1" in sv_content
    
    # Verify endmodule
    assert "endmodule" in sv_content
"""Test SystemVerilog translation correctness for various constructs"""
import pytest
import zuspec.dataclasses as zdc
from zuspec.be.sv import SVGenerator
from pathlib import Path
import re


def test_basic_counter():
    """Test basic counter with sync process"""
    
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
    
    factory = zdc.DataModelFactory()
    ctxt = factory.build(Counter)
    
    generator = SVGenerator(Path("/tmp"))
    sv_code = generator._generate_component(list(ctxt.type_m.values())[0])
    
    # Verify module structure
    assert "module" in sv_code
    assert "input  clock" in sv_code or "input clock" in sv_code
    assert "input  reset" in sv_code or "input reset" in sv_code
    assert "output reg[31:0] count" in sv_code
    
    # Verify always block
    assert "always @(posedge clock or posedge reset)" in sv_code
    
    # Verify reset logic
    assert "if (reset)" in sv_code
    assert "count <= 0" in sv_code
    
    # Verify increment logic
    assert "else" in sv_code
    assert "count <= count + 1" in sv_code
    
    # Verify module end
    assert "endmodule" in sv_code


def test_bit_widths():
    """Test various bit width declarations"""
    
    @zdc.dataclass
    class BitWidths(zdc.Component):
        b1 : zdc.bit = zdc.input()
        b8 : zdc.bit8 = zdc.input()
        b16 : zdc.bit16 = zdc.input()
        b32 : zdc.bit32 = zdc.input()
        b64 : zdc.bit64 = zdc.output()
    
    factory = zdc.DataModelFactory()
    ctxt = factory.build(BitWidths)
    
    generator = SVGenerator(Path("/tmp"))
    sv_code = generator._generate_component(list(ctxt.type_m.values())[0])
    
    # Check single bit (no width specifier)
    assert re.search(r'input\s+b1', sv_code)
    
    # Check 8-bit
    assert "input reg[7:0] b8" in sv_code or "input  reg[7:0] b8" in sv_code
    
    # Check 16-bit
    assert "input reg[15:0] b16" in sv_code or "input  reg[15:0] b16" in sv_code
    
    # Check 32-bit
    assert "input reg[31:0] b32" in sv_code or "input  reg[31:0] b32" in sv_code
    
    # Check 64-bit output
    assert "output reg[63:0] b64" in sv_code


def test_combinational_logic():
    """Test combinational (comb) process translation"""
    
    @zdc.dataclass
    class XorGate(zdc.Component):
        a : zdc.bit16 = zdc.input()
        b : zdc.bit16 = zdc.input()
        out : zdc.bit16 = zdc.output()
        
        @zdc.comb
        def _xor_calc(self):
            self.out = self.a ^ self.b
    
    factory = zdc.DataModelFactory()
    ctxt = factory.build(XorGate)
    
    generator = SVGenerator(Path("/tmp"))
    comp = list(ctxt.type_m.values())[0]
    
    # For now, comb processes might not be fully implemented
    # This test verifies the component structure at least
    sv_code = generator._generate_component(comp)
    assert "module" in sv_code
    assert "input reg[15:0] a" in sv_code or "input  reg[15:0] a" in sv_code


def test_arithmetic_operations():
    """Test various arithmetic operations"""
    
    @zdc.dataclass
    class ALU(zdc.Component):
        clock : zdc.bit = zdc.input()
        a : zdc.bit32 = zdc.input()
        b : zdc.bit32 = zdc.input()
        sum_out : zdc.bit32 = zdc.output()
        diff_out : zdc.bit32 = zdc.output()
        prod_out : zdc.bit32 = zdc.output()
        
        @zdc.sync(clock=lambda s:s.clock)
        def _compute(self):
            self.sum_out = self.a + self.b
            self.diff_out = self.a - self.b
            self.prod_out = self.a * self.b
    
    factory = zdc.DataModelFactory()
    ctxt = factory.build(ALU)
    
    generator = SVGenerator(Path("/tmp"))
    sv_code = generator._generate_component(list(ctxt.type_m.values())[0])
    
    # Verify arithmetic operators are translated
    assert "sum_out <= a + b" in sv_code
    assert "diff_out <= a - b" in sv_code
    assert "prod_out <= a * b" in sv_code


def test_internal_signals():
    """Test internal (non-port) signals"""
    
    @zdc.dataclass
    class Pipeline(zdc.Component):
        clock : zdc.bit = zdc.input()
        data_in : zdc.bit16 = zdc.input()
        data_out : zdc.bit16 = zdc.output()
        
        _stage1 : zdc.bit16 = zdc.field()
        _stage2 : zdc.bit16 = zdc.field()
        
        @zdc.sync(clock=lambda s:s.clock)
        def _pipeline(self):
            self._stage1 = self.data_in
            self._stage2 = self._stage1
            self.data_out = self._stage2
    
    factory = zdc.DataModelFactory()
    ctxt = factory.build(Pipeline)
    
    generator = SVGenerator(Path("/tmp"))
    comp = list(ctxt.type_m.values())[0]
    sv_code = generator._generate_component(comp)
    
    # Internal signals should still be accessible in the always block
    assert "_stage1 <= data_in" in sv_code
    assert "_stage2 <= _stage1" in sv_code
    assert "data_out <= _stage2" in sv_code


def test_nested_if_else():
    """Test nested if-else statements"""
    
    @zdc.dataclass
    class StateMachine(zdc.Component):
        clock : zdc.bit = zdc.input()
        reset : zdc.bit = zdc.input()
        state : zdc.bit8 = zdc.output()
        
        @zdc.sync(clock=lambda s:s.clock, reset=lambda s:s.reset)
        def _state_logic(self):
            if self.reset:
                self.state = 0
            else:
                if self.state == 0:
                    self.state = 1
                else:
                    if self.state == 1:
                        self.state = 2
                    else:
                        self.state = 0
    
    factory = zdc.DataModelFactory()
    ctxt = factory.build(StateMachine)
    
    generator = SVGenerator(Path("/tmp"))
    sv_code = generator._generate_component(list(ctxt.type_m.values())[0])
    
    # Verify nested structure exists
    assert "if (reset)" in sv_code
    assert "else" in sv_code
    # The inner structure should have multiple levels
    assert sv_code.count("if") >= 3  # At least 3 if statements


def test_compound_expressions():
    """Test compound expressions with multiple operators"""
    
    @zdc.dataclass
    class Calculator(zdc.Component):
        clock : zdc.bit = zdc.input()
        a : zdc.bit32 = zdc.input()
        b : zdc.bit32 = zdc.input()
        c : zdc.bit32 = zdc.input()
        result : zdc.bit32 = zdc.output()
        
        @zdc.sync(clock=lambda s:s.clock)
        def _calc(self):
            self.result = (self.a + self.b) * self.c
    
    factory = zdc.DataModelFactory()
    ctxt = factory.build(Calculator)
    
    generator = SVGenerator(Path("/tmp"))
    sv_code = generator._generate_component(list(ctxt.type_m.values())[0])
    
    # Should contain both + and * operators
    assert "+" in sv_code
    assert "*" in sv_code


def test_augmented_assignments():
    """Test augmented assignments (+=, -=, etc.)"""
    
    @zdc.dataclass
    class Accumulator(zdc.Component):
        clock : zdc.bit = zdc.input()
        add_val : zdc.bit32 = zdc.input()
        sub_val : zdc.bit32 = zdc.input()
        sum : zdc.bit32 = zdc.output()
        diff : zdc.bit32 = zdc.output()
        
        @zdc.sync(clock=lambda s:s.clock)
        def _accumulate(self):
            self.sum += self.add_val
            self.diff -= self.sub_val
    
    factory = zdc.DataModelFactory()
    ctxt = factory.build(Accumulator)
    
    generator = SVGenerator(Path("/tmp"))
    sv_code = generator._generate_component(list(ctxt.type_m.values())[0])
    
    # Augmented assignments should be expanded
    assert "sum <= sum + add_val" in sv_code
    assert "diff <= diff - sub_val" in sv_code


def test_name_sanitization():
    """Test that invalid SV names are sanitized"""
    
    @zdc.dataclass
    class TestComp(zdc.Component):
        clock : zdc.bit = zdc.input()
    
    factory = zdc.DataModelFactory()
    ctxt = factory.build(TestComp)
    
    generator = SVGenerator(Path("/tmp"))
    
    # Test sanitization function directly
    assert "__" in generator._sanitize_sv_name("test.module.name")
    assert "__" in generator._sanitize_sv_name("test<locals>")
    assert generator._sanitize_sv_name("123test")[0] == '_'
    
    # Verify no dots in generated code
    sv_code = generator._generate_component(list(ctxt.type_m.values())[0])
    module_match = re.search(r'module\s+(\w+)', sv_code)
    if module_match:
        module_name = module_match.group(1)
        assert '.' not in module_name
        assert '<' not in module_name
        assert '>' not in module_name


def test_multiple_outputs():
    """Test component with multiple outputs"""
    
    @zdc.dataclass
    class MultiOut(zdc.Component):
        clock : zdc.bit = zdc.input()
        data : zdc.bit32 = zdc.input()
        out1 : zdc.bit32 = zdc.output()
        out2 : zdc.bit32 = zdc.output()
        out3 : zdc.bit32 = zdc.output()
        
        @zdc.sync(clock=lambda s:s.clock)
        def _split(self):
            self.out1 = self.data
            self.out2 = self.data + 1
            self.out3 = self.data + 2
    
    factory = zdc.DataModelFactory()
    ctxt = factory.build(MultiOut)
    
    generator = SVGenerator(Path("/tmp"))
    sv_code = generator._generate_component(list(ctxt.type_m.values())[0])
    
    # Verify all outputs are declared
    assert "output reg[31:0] out1" in sv_code
    assert "output reg[31:0] out2" in sv_code
    assert "output reg[31:0] out3" in sv_code
    
    # Verify all are assigned
    assert "out1 <= data" in sv_code
    assert "out2 <= data + 1" in sv_code
    assert "out3 <= data + 2" in sv_code


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

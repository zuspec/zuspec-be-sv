"""Verify that C is actually doing real work and not optimizing it away"""
import os
import subprocess
import zuspec.dataclasses as zdc
from pathlib import Path


def test_verify_c_work(tmpdir):
    """Check that C is actually computing the counter value correctly."""
    
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
                self.count = self.count + 1
    
    from zuspec.be.sw import CGenerator
    
    factory = zdc.DataModelFactory()
    ctxt = factory.build(Counter)
    
    output_dir = Path(tmpdir) / "c"
    output_dir.mkdir(exist_ok=True)
    
    generator = CGenerator(output_dir)
    sources = generator.generate(ctxt, py_classes=[Counter])
    
    import zuspec.be.sw
    sw_pkg_path = Path(zuspec.be.sw.__file__).parent
    runtime_include = sw_pkg_path / "share" / "include"
    runtime_src = sw_pkg_path / "share" / "rt"
    
    # Create test harness that prints the result
    test_harness = output_dir / "test_harness.c"
    
    # Test multiple cycle counts
    for cycles in [100, 1000, 10000, 100000, 1000000]:
        test_harness.write_text(f"""
#include <stdio.h>
#include <stdlib.h>
#include "zsp_alloc.h"
#include "zsp_init_ctxt.h"
#include "counter.h"

int main(int argc, char **argv) {{
    zsp_alloc_t alloc;
    zsp_alloc_malloc_init(&alloc);

    zsp_init_ctxt_t ctxt;
    ctxt.alloc = &alloc;

    Counter comp;
    Counter_init(&ctxt, &comp, "counter", NULL);
    
    // Reset the counter
    comp.reset = 1;
    comp.clock = 1;
    Counter__count(&comp);  // Call sync process on clock edge
    comp.clock = 0;
    comp.reset = 0;
    
    // Run cycles
    for (int i = 0; i < {cycles}; i++) {{
        comp.clock = 1;
        Counter__count(&comp);  // Call sync process on positive clock edge
        comp.clock = 0;
    }}
    
    // Print result - this forces compiler to not optimize away
    printf("%d\\n", comp.count);
    
    // Also verify correctness
    if (comp.count != {cycles}) {{
        fprintf(stderr, "ERROR: Expected %d, got %d\\n", {cycles}, comp.count);
        return 1;
    }}
    
    return 0;
}}
""")
        
        # Compile
        executable = output_dir / f"test_counter_{cycles}"
        
        c_files = [f for f in output_dir.glob("*.c") if f.name != "main.c"]
        runtime_c_files = list(runtime_src.glob("*.c"))
        
        compile_cmd = [
            "gcc", "-O2", 
            f"-I{runtime_include}",
            f"-I{output_dir}",
            "-o", str(executable)
        ] + [str(f) for f in c_files] + [str(f) for f in runtime_c_files]
        
        result = subprocess.run(compile_cmd, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, f"Compilation failed: {result.stderr}"
        
        # Run and check output
        result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, f"Execution failed for {cycles} cycles: {result.stderr}"
        
        # Verify the output is correct
        output = result.stdout.strip()
        assert output == str(cycles), f"Expected {cycles}, got {output}"
        
        print(f"✓ Verified {cycles} cycles: count = {output}")
    
    print("\n✅ All tests passed - C is doing real work!")


def test_check_assembly(tmpdir):
    """Check the generated assembly to see if loop is optimized away."""
    
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
                self.count = self.count + 1
    
    from zuspec.be.sw import CGenerator
    
    factory = zdc.DataModelFactory()
    ctxt = factory.build(Counter)
    
    output_dir = Path(tmpdir) / "c"
    output_dir.mkdir(exist_ok=True)
    
    generator = CGenerator(output_dir)
    sources = generator.generate(ctxt, py_classes=[Counter])
    
    import zuspec.be.sw
    sw_pkg_path = Path(zuspec.be.sw.__file__).parent
    runtime_include = sw_pkg_path / "share" / "include"
    runtime_src = sw_pkg_path / "share" / "rt"
    
    # Create test harness
    test_harness = output_dir / "test_harness.c"
    test_harness.write_text(f"""
#include <stdio.h>
#include <stdlib.h>
#include "zsp_alloc.h"
#include "zsp_init_ctxt.h"
#include "counter.h"

int main(int argc, char **argv) {{
    zsp_alloc_t alloc;
    zsp_alloc_malloc_init(&alloc);

    zsp_init_ctxt_t ctxt;
    ctxt.alloc = &alloc;

    Counter comp;
    Counter_init(&ctxt, &comp, "counter", NULL);
    
    // Reset the counter
    comp.reset = 1;
    comp.clock = 1;
    Counter__count(&comp);  // Call sync process on clock edge
    comp.clock = 0;
    comp.reset = 0;
    
    // Run cycles
    for (int i = 0; i < 1000000; i++) {{
        comp.clock = 1;
        Counter__count(&comp);  // Call sync process on positive clock edge
        comp.clock = 0;
    }}
    
    // Print result
    printf("%d\\n", comp.count);
    
    return 0;
}}
""")
    
    # Compile to assembly
    asm_file = output_dir / "test_harness.s"
    
    c_files = [f for f in output_dir.glob("*.c") if f.name != "main.c"]
    
    compile_cmd = [
        "gcc", "-O2", "-S",
        f"-I{runtime_include}",
        f"-I{output_dir}",
        "-o", str(asm_file),
        str(test_harness)
    ]
    
    result = subprocess.run(compile_cmd, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"Assembly generation failed: {result.stderr}"
    
    # Read and check assembly
    asm_content = asm_file.read_text()
    
    print("\n" + "="*80)
    print("ASSEMBLY CODE ANALYSIS")
    print("="*80)
    print("\nSearching for loop-related instructions...")
    
    # Look for loop indicators
    has_loop = False
    loop_lines = []
    
    for line in asm_content.split('\n'):
        line_lower = line.lower()
        if any(keyword in line_lower for keyword in ['loop', 'jmp', 'cmp', 'jle', 'jge', 'jl', 'jg']):
            loop_lines.append(line)
            has_loop = True
    
    if loop_lines:
        print("\n✓ Found loop-related instructions:")
        for line in loop_lines[:20]:  # Show first 20 lines
            print(f"  {line}")
        if len(loop_lines) > 20:
            print(f"  ... and {len(loop_lines) - 20} more")
    else:
        print("\n⚠ WARNING: No obvious loop instructions found!")
        print("The compiler may have optimized away the loop completely.")
    
    # Check for constant assignment (sign of optimization)
    if "1000000" in asm_content or "$1000000" in asm_content:
        print("\n⚠ WARNING: Found constant 1000000 in assembly - might be optimized!")
    
    print("\nFull assembly saved to:", asm_file)
    print("="*80 + "\n")


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "-s"])

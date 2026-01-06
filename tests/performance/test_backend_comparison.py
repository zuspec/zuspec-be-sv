"""Performance comparison: Python vs SystemVerilog vs C backends"""
import os
import pytest
import shutil
import asyncio
import time
import subprocess
import zuspec.dataclasses as zdc
from zuspec.be.sv import SVGenerator
from pathlib import Path
from dv_flow.mgr import TaskListenerLog, TaskSetRunner, PackageLoader
from dv_flow.mgr.task_graph_builder import TaskGraphBuilder


def benchmark_python_native(component_class, cycles):
    """Benchmark native Python execution."""
    
    # Instantiate and run
    start = time.perf_counter()
    
    comp = component_class()
    comp.reset = 1
    comp.clock = 0
    
    # Reset cycle
    comp.clock = 1
    comp.clock = 0
    comp.reset = 0
    
    # Run cycles
    for _ in range(cycles):
        comp.clock = 1
        comp.clock = 0
    
    end = time.perf_counter()
    elapsed = end - start
    
    return {
        'backend': 'Python (Native)',
        'cycles': cycles,
        'total_time': elapsed,
        'throughput': cycles / elapsed,
        'time_per_cycle_us': elapsed / cycles * 1_000_000
    }


def benchmark_systemverilog_verilator(component_class, cycles, tmpdir):
    """Benchmark SystemVerilog with Verilator."""
    
    if not shutil.which('verilator'):
        return None
    
    # Generate SystemVerilog
    start = time.perf_counter()
    
    factory = zdc.DataModelFactory()
    ctxt = factory.build(component_class)
    
    output_dir = Path(tmpdir) / "sv"
    output_dir.mkdir(exist_ok=True)
    generator = SVGenerator(output_dir)
    sv_files = generator.generate(ctxt)
    
    # Get module name
    sv_content = sv_files[0].read_text()
    import re
    module_match = re.search(r'module\s+(\w+)', sv_content)
    module_name = module_match.group(1) if module_match else "Counter"
    
    # Create testbench
    testbench = f"""
module tb;
    reg clock;
    reg reset;
    wire [31:0] count;
    
    {module_name} dut(
        .clock(clock),
        .reset(reset),
        .count(count)
    );
    
    initial begin
        clock = 0;
        forever #5 clock = ~clock;
    end
    
    initial begin
        reset = 1;
        repeat(10) @(posedge clock);
        reset = 0;
        repeat({cycles}) @(posedge clock);
        $finish;
    end
endmodule
"""
    
    tb_file = output_dir / "tb.sv"
    tb_file.write_text(testbench)
    
    gen_time = time.perf_counter() - start
    
    # Compile and run with Verilator
    compile_start = time.perf_counter()
    
    runner = TaskSetRunner(str(Path(tmpdir) / 'rundir'))
    
    def marker_listener(marker):
        from dv_flow.mgr.task_data import SeverityE
        if marker.severity == SeverityE.Error:
            raise Exception(f"Marker error: {marker.msg}")
    
    builder = TaskGraphBuilder(
        PackageLoader(marker_listeners=[marker_listener]).load_rgy(['std', 'hdlsim.vlt']),
        str(Path(tmpdir) / 'rundir'))
    
    sv_fileset = builder.mkTaskNode(
        'std.FileSet',
        name="sv_files",
        type="systemVerilogSource",
        base=str(output_dir),
        include="*.sv",
        needs=[])
    
    sim_img = builder.mkTaskNode(
        "hdlsim.vlt.SimImage",
        name="sim_img",
        top=['tb'],
        needs=[sv_fileset])
    
    sim_run = builder.mkTaskNode(
        "hdlsim.vlt.SimRun",
        name="sim_run",
        needs=[sim_img])
    
    runner.add_listener(TaskListenerLog().event)
    out = asyncio.run(runner.run(sim_run))
    
    end = time.perf_counter()
    total_time = end - start
    compile_time = end - compile_start
    
    # Extract actual simulation time from log
    rundir_fs = None
    for fs in out.output:
        if fs.type == 'std.FileSet' and fs.filetype == "simRunDir":
            rundir_fs = fs
    
    sim_time = 0
    if rundir_fs:
        sim_log_path = os.path.join(rundir_fs.basedir, "sim.log")
        if os.path.isfile(sim_log_path):
            with open(sim_log_path, "r") as f:
                sim_log = f.read()
                # Look for walltime in Verilator output
                time_match = re.search(r'walltime ([\d.]+) s', sim_log)
                if time_match:
                    sim_time = float(time_match.group(1))
    
    return {
        'backend': 'SystemVerilog (Verilator)',
        'cycles': cycles,
        'gen_time': gen_time,
        'compile_time': compile_time,
        'sim_time': sim_time,
        'total_time': total_time,
        'throughput': cycles / total_time,
        'throughput_sim_only': cycles / sim_time if sim_time > 0 else 0,
        'time_per_cycle_us': total_time / cycles * 1_000_000,
        'time_per_cycle_us_sim': sim_time / cycles * 1_000_000 if sim_time > 0 else 0
    }


def benchmark_c_backend(component_class, cycles, tmpdir):
    """Benchmark C backend."""
    
    try:
        from zuspec.be.sw import CGenerator
    except ImportError:
        return None
    
    start = time.perf_counter()
    
    # Generate C code
    factory = zdc.DataModelFactory()
    ctxt = factory.build(component_class)
    
    output_dir = Path(tmpdir) / "c"
    output_dir.mkdir(exist_ok=True)
    
    generator = CGenerator(output_dir)
    sources = generator.generate(ctxt, py_classes=[component_class])
    
    gen_time = time.perf_counter() - start
    
    # Get runtime paths
    import zuspec.be.sw
    sw_pkg_path = Path(zuspec.be.sw.__file__).parent
    runtime_include = sw_pkg_path / "share" / "include"
    runtime_src = sw_pkg_path / "share" / "rt"
    
    # Create test harness - use the generated main.c as a base
    test_harness = output_dir / "test_harness.c"
    comp_name = component_class.__name__
    test_harness.write_text(f"""
#include <stdio.h>
#include <stdlib.h>
#include "zsp_alloc.h"
#include "zsp_init_ctxt.h"
#include "{comp_name.lower()}.h"

int main(int argc, char **argv) {{
    zsp_alloc_t alloc;
    zsp_alloc_malloc_init(&alloc);

    zsp_init_ctxt_t ctxt;
    ctxt.alloc = &alloc;

    {comp_name} comp;
    {comp_name}_init(&ctxt, &comp, "{comp_name.lower()}", NULL);
    
    // Reset
    comp.reset = 1;
    comp.clock = 1;
    comp.clock = 0;
    comp.reset = 0;
    
    // Run cycles
    for (int i = 0; i < {cycles}; i++) {{
        comp.clock = 1;
        comp.clock = 0;
    }}
    
    printf("Final count: %d\\n", comp.count);
    return 0;
}}
""")
    
    # Compile
    compile_start = time.perf_counter()
    executable = output_dir / "test_counter"
    
    # Find all generated C source files (exclude main.c since we have test_harness.c)
    c_files = [f for f in output_dir.glob("*.c") if f.name != "main.c"]
    
    # Find all runtime C source files
    runtime_c_files = list(runtime_src.glob("*.c"))
    
    compile_cmd = [
        "gcc", "-O2", 
        f"-I{runtime_include}",
        f"-I{output_dir}",
        "-o", str(executable)
    ] + [str(f) for f in c_files] + [str(f) for f in runtime_c_files]
    
    try:
        result = subprocess.run(compile_cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            print(f"C compilation failed: {result.stderr}")
            return None
    except Exception as e:
        print(f"C compilation error: {e}")
        return None
    
    compile_time = time.perf_counter() - compile_start
    
    # Run
    run_start = time.perf_counter()
    try:
        result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            print(f"C execution failed: {result.stderr}")
            return None
    except Exception as e:
        print(f"C execution error: {e}")
        return None
    
    run_time = time.perf_counter() - run_start
    
    end = time.perf_counter()
    total_time = end - start
    
    return {
        'backend': 'C (GCC -O2)',
        'cycles': cycles,
        'gen_time': gen_time,
        'compile_time': compile_time,
        'run_time': run_time,
        'total_time': total_time,
        'throughput': cycles / total_time,
        'throughput_run_only': cycles / run_time,
        'time_per_cycle_us': total_time / cycles * 1_000_000,
        'time_per_cycle_us_run': run_time / cycles * 1_000_000
    }


@pytest.mark.parametrize("cycles", [1000, 10000, 100000])
def test_backend_comparison(tmpdir, cycles):
    """Compare performance across all backends."""
    
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
    
    print(f"\n{'='*80}")
    print(f"BACKEND PERFORMANCE COMPARISON - {cycles} CYCLES")
    print(f"{'='*80}\n")
    
    results = []
    
    # Python Native
    print("Running Python Native...", end=" ", flush=True)
    py_result = benchmark_python_native(Counter, cycles)
    results.append(py_result)
    print(f"✓ {py_result['throughput']:.0f} cycles/sec")
    
    # SystemVerilog + Verilator
    print("Running SystemVerilog + Verilator...", end=" ", flush=True)
    sv_result = benchmark_systemverilog_verilator(Counter, cycles, tmpdir)
    if sv_result:
        results.append(sv_result)
        print(f"✓ {sv_result['throughput']:.0f} cycles/sec (total), {sv_result['throughput_sim_only']:.0f} cycles/sec (sim only)")
    else:
        print("✗ Skipped (Verilator not available)")
    
    # C Backend
    print("Running C (GCC -O2)...", end=" ", flush=True)
    c_result = benchmark_c_backend(Counter, cycles, tmpdir)
    if c_result:
        results.append(c_result)
        print(f"✓ {c_result['throughput']:.0f} cycles/sec (total), {c_result['throughput_run_only']:.0f} cycles/sec (run only)")
    else:
        print("✗ Skipped (C backend not available)")
    
    # Print detailed comparison table
    print("\n" + "="*80)
    print("DETAILED PERFORMANCE COMPARISON")
    print("="*80)
    print()
    
    # Header
    print(f"{'Backend':<30} {'Total Time':<15} {'Throughput':<20} {'Time/Cycle':<15}")
    print(f"{'':30} {'(seconds)':<15} {'(cycles/sec)':<20} {'(μs)':<15}")
    print("-" * 80)
    
    for r in results:
        backend = r['backend']
        total = r['total_time']
        throughput = r['throughput']
        per_cycle = r['time_per_cycle_us']
        
        print(f"{backend:<30} {total:>12.3f}    {throughput:>15.0f}     {per_cycle:>12.3f}")
    
    print()
    
    # Breakdown table
    print("="*80)
    print("TIME BREAKDOWN (seconds)")
    print("="*80)
    print()
    
    print(f"{'Backend':<30} {'Generation':<12} {'Compilation':<12} {'Execution':<12} {'Total':<12}")
    print("-" * 80)
    
    for r in results:
        backend = r['backend']
        gen = r.get('gen_time', 0)
        comp = r.get('compile_time', 0)
        run = r.get('run_time') or r.get('sim_time', 0)
        total = r['total_time']
        
        print(f"{backend:<30} {gen:>10.3f}   {comp:>10.3f}   {run:>10.3f}   {total:>10.3f}")
    
    print()
    
    # Speedup table (relative to Python)
    if len(results) > 1:
        print("="*80)
        print("SPEEDUP RELATIVE TO PYTHON NATIVE")
        print("="*80)
        print()
        
        py_throughput = py_result['throughput']
        
        print(f"{'Backend':<30} {'Total Speedup':<20} {'Execution Speedup':<20}")
        print("-" * 80)
        
        for r in results:
            backend = r['backend']
            
            if 'Python' in backend:
                print(f"{backend:<30} {'1.00x (baseline)':<20} {'1.00x (baseline)':<20}")
            else:
                total_speedup = r['throughput'] / py_throughput
                
                # Get execution-only speedup
                if 'throughput_sim_only' in r:
                    exec_speedup = r['throughput_sim_only'] / py_throughput
                elif 'throughput_run_only' in r:
                    exec_speedup = r['throughput_run_only'] / py_throughput
                else:
                    exec_speedup = total_speedup
                
                print(f"{backend:<30} {total_speedup:>18.2f}x  {exec_speedup:>18.2f}x")
        
        print()
    
    print("="*80)
    print()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])

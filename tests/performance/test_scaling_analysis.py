"""Detailed scaling analysis for C vs SystemVerilog backends"""
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


def benchmark_c(cycles, tmpdir):
    """Benchmark C backend."""
    from zuspec.be.sw import CGenerator
    
    start = time.perf_counter()
    
    # Generate C code
    factory = zdc.DataModelFactory()
    ctxt = factory.build(Counter)
    
    output_dir = Path(tmpdir) / "c"
    output_dir.mkdir(exist_ok=True)
    
    generator = CGenerator(output_dir)
    sources = generator.generate(ctxt, py_classes=[Counter])
    
    gen_time = time.perf_counter() - start
    
    # Get runtime paths
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
    
    c_files = [f for f in output_dir.glob("*.c") if f.name != "main.c"]
    runtime_c_files = list(runtime_src.glob("*.c"))
    
    compile_cmd = [
        "gcc", "-O2", 
        f"-I{runtime_include}",
        f"-I{output_dir}",
        "-o", str(executable)
    ] + [str(f) for f in c_files] + [str(f) for f in runtime_c_files]
    
    result = subprocess.run(compile_cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        return None
    
    compile_time = time.perf_counter() - compile_start
    
    # Run
    run_start = time.perf_counter()
    result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        return None
    
    run_time = time.perf_counter() - run_start
    
    total_time = time.perf_counter() - start
    
    return {
        'gen_time': gen_time,
        'compile_time': compile_time,
        'run_time': run_time,
        'total_time': total_time,
        'throughput': cycles / total_time,
        'exec_throughput': cycles / run_time
    }


def benchmark_verilator(cycles, tmpdir):
    """Benchmark SystemVerilog with Verilator."""
    
    if not shutil.which('verilator'):
        return None
    
    start = time.perf_counter()
    
    # Generate SystemVerilog
    factory = zdc.DataModelFactory()
    ctxt = factory.build(Counter)
    
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
    
    # Compile and run
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
    
    total_time = time.perf_counter() - start
    compile_time = time.perf_counter() - compile_start
    
    # Extract sim time from log
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
                time_match = re.search(r'walltime ([\d.]+) s', sim_log)
                if time_match:
                    sim_time = float(time_match.group(1))
    
    return {
        'gen_time': gen_time,
        'compile_time': compile_time,
        'run_time': sim_time,
        'total_time': total_time,
        'throughput': cycles / total_time,
        'exec_throughput': cycles / sim_time if sim_time > 0 else 0
    }


@pytest.mark.parametrize("cycles", [1000, 10000, 100000, 500000, 1000000])
def test_scaling_comparison(tmpdir, cycles):
    """Compare C vs Verilator scaling."""
    
    print(f"\n{'='*80}")
    print(f"SCALING TEST: {cycles:,} CYCLES")
    print(f"{'='*80}\n")
    
    # Run C
    print("Running C (GCC -O2)...", end=" ", flush=True)
    c_result = benchmark_c(cycles, tmpdir)
    if c_result:
        print(f"✓ {c_result['total_time']:.3f}s total")
    else:
        print("✗ Failed")
        c_result = None
    
    # Run Verilator
    print("Running SystemVerilog (Verilator)...", end=" ", flush=True)
    sv_result = benchmark_verilator(cycles, tmpdir)
    if sv_result:
        print(f"✓ {sv_result['total_time']:.3f}s total")
    else:
        print("✗ Failed")
        sv_result = None
    
    if c_result and sv_result:
        print(f"\n{'='*80}")
        print(f"COMPARISON FOR {cycles:,} CYCLES")
        print(f"{'='*80}\n")
        
        print(f"{'Metric':<30} {'C (GCC -O2)':<20} {'Verilator':<20} {'Winner':<15}")
        print("-" * 85)
        
        # Total time
        c_faster = c_result['total_time'] < sv_result['total_time']
        print(f"{'Total Time':<30} {c_result['total_time']:>18.3f}s {sv_result['total_time']:>18.3f}s {'C' if c_faster else 'Verilator':>15}")
        
        # Compilation time
        c_faster = c_result['compile_time'] < sv_result['compile_time']
        print(f"{'Compilation Time':<30} {c_result['compile_time']:>18.3f}s {sv_result['compile_time']:>18.3f}s {'C' if c_faster else 'Verilator':>15}")
        
        # Execution time
        c_faster = c_result['run_time'] < sv_result['run_time']
        print(f"{'Execution Time':<30} {c_result['run_time']:>18.3f}s {sv_result['run_time']:>18.3f}s {'C' if c_faster else 'Verilator':>15}")
        
        # Throughput (total)
        c_faster = c_result['throughput'] > sv_result['throughput']
        print(f"{'Throughput (cycles/sec)':<30} {c_result['throughput']:>18.0f} {sv_result['throughput']:>18.0f} {'C' if c_faster else 'Verilator':>15}")
        
        # Exec throughput
        c_faster = c_result['exec_throughput'] > sv_result['exec_throughput']
        print(f"{'Exec Throughput (cycles/sec)':<30} {c_result['exec_throughput']:>18.0f} {sv_result['exec_throughput']:>18.0f} {'C' if c_faster else 'Verilator':>15}")
        
        print("\n" + "="*80 + "\n")
        
        # Calculate speedup
        if c_result['total_time'] < sv_result['total_time']:
            speedup = sv_result['total_time'] / c_result['total_time']
            print(f"Result: C is {speedup:.2f}x faster in total time")
        else:
            speedup = c_result['total_time'] / sv_result['total_time']
            print(f"Result: Verilator is {speedup:.2f}x faster in total time")
        
        print()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])

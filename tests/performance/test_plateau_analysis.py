"""Find the plateau point where cycles/sec stabilizes"""
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
    
    factory = zdc.DataModelFactory()
    ctxt = factory.build(Counter)
    
    output_dir = Path(tmpdir) / "c"
    output_dir.mkdir(exist_ok=True)
    
    generator = CGenerator(output_dir)
    sources = generator.generate(ctxt, py_classes=[Counter])
    
    gen_time = time.perf_counter() - start
    
    import zuspec.be.sw
    sw_pkg_path = Path(zuspec.be.sw.__file__).parent
    runtime_include = sw_pkg_path / "share" / "include"
    runtime_src = sw_pkg_path / "share" / "rt"
    
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
    
    comp.reset = 1;
    comp.clock = 1;
    comp.clock = 0;
    comp.reset = 0;
    
    for (int i = 0; i < {cycles}; i++) {{
        comp.clock = 1;
        comp.clock = 0;
    }}
    
    return 0;
}}
""")
    
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
    
    # Run multiple times to get stable measurement
    run_times = []
    for _ in range(3):
        run_start = time.perf_counter()
        result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            return None
        run_time = time.perf_counter() - run_start
        run_times.append(run_time)
    
    # Use median of 3 runs
    run_time = sorted(run_times)[1]
    
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
    
    factory = zdc.DataModelFactory()
    ctxt = factory.build(Counter)
    
    output_dir = Path(tmpdir) / "sv"
    output_dir.mkdir(exist_ok=True)
    generator = SVGenerator(output_dir)
    sv_files = generator.generate(ctxt)
    
    sv_content = sv_files[0].read_text()
    import re
    module_match = re.search(r'module\s+(\w+)', sv_content)
    module_name = module_match.group(1) if module_match else "Counter"
    
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


def test_find_plateau(tmpdir):
    """Find the plateau point where throughput stabilizes."""
    
    # Test at various cycle counts to find plateau
    test_points = [
        1000, 2000, 5000,
        10000, 20000, 50000,
        100000, 200000, 500000,
        1000000, 2000000, 5000000,
        10000000
    ]
    
    print(f"\n{'='*100}")
    print(f"PLATEAU ANALYSIS: Finding where execution throughput stabilizes")
    print(f"{'='*100}\n")
    
    c_results = []
    sv_results = []
    
    print(f"{'Cycles':<12} {'C Exec Time':<15} {'C Throughput':<20} {'SV Exec Time':<15} {'SV Throughput':<20}")
    print("-" * 100)
    
    for cycles in test_points:
        print(f"{cycles:>10,}  ", end="", flush=True)
        
        # C benchmark
        c_result = benchmark_c(cycles, tmpdir)
        if c_result:
            c_results.append({'cycles': cycles, **c_result})
            print(f"{c_result['run_time']:>13.6f}s  {c_result['exec_throughput']:>18,.0f}  ", end="", flush=True)
        else:
            print(f"{'FAILED':>13}  {'':>18}  ", end="", flush=True)
        
        # Verilator benchmark
        sv_result = benchmark_verilator(cycles, tmpdir)
        if sv_result:
            sv_results.append({'cycles': cycles, **sv_result})
            print(f"{sv_result['run_time']:>13.6f}s  {sv_result['exec_throughput']:>18,.0f}")
        else:
            print(f"{'FAILED':>13}  {'':>18}")
    
    # Analyze plateau
    print(f"\n{'='*100}")
    print("PLATEAU ANALYSIS")
    print(f"{'='*100}\n")
    
    # Find plateau for C
    if len(c_results) >= 3:
        print("C (GCC -O2) Plateau Analysis:")
        print(f"{'Cycles':<12} {'Exec Throughput':<25} {'Change from Previous':<25}")
        print("-" * 62)
        
        prev_throughput = 0
        for i, r in enumerate(c_results):
            if prev_throughput > 0:
                change = ((r['exec_throughput'] - prev_throughput) / prev_throughput) * 100
                print(f"{r['cycles']:>10,}  {r['exec_throughput']:>20,.0f}    {change:>20.2f}%")
            else:
                print(f"{r['cycles']:>10,}  {r['exec_throughput']:>20,.0f}    {'N/A':>20}")
            prev_throughput = r['exec_throughput']
        
        # Find where change is < 5%
        plateau_point = None
        for i in range(1, len(c_results)):
            prev = c_results[i-1]['exec_throughput']
            curr = c_results[i]['exec_throughput']
            change = abs((curr - prev) / prev) * 100
            if change < 5 and i >= 2:  # Need at least 3 points
                plateau_point = c_results[i-1]['cycles']
                break
        
        if plateau_point:
            print(f"\n✓ C plateaus around {plateau_point:,} cycles (< 5% change after this)")
        else:
            print(f"\n⚠ C has not plateaued yet at {c_results[-1]['cycles']:,} cycles")
    
    print("\n")
    
    # Find plateau for Verilator
    if len(sv_results) >= 3:
        print("Verilator Plateau Analysis:")
        print(f"{'Cycles':<12} {'Exec Throughput':<25} {'Change from Previous':<25}")
        print("-" * 62)
        
        prev_throughput = 0
        for i, r in enumerate(sv_results):
            if prev_throughput > 0:
                change = ((r['exec_throughput'] - prev_throughput) / prev_throughput) * 100
                print(f"{r['cycles']:>10,}  {r['exec_throughput']:>20,.0f}    {change:>20.2f}%")
            else:
                print(f"{r['cycles']:>10,}  {r['exec_throughput']:>20,.0f}    {'N/A':>20}")
            prev_throughput = r['exec_throughput']
        
        # Find where change is < 5%
        plateau_point = None
        for i in range(1, len(sv_results)):
            prev = sv_results[i-1]['exec_throughput']
            curr = sv_results[i]['exec_throughput']
            change = abs((curr - prev) / prev) * 100
            if change < 5 and i >= 2:
                plateau_point = sv_results[i-1]['cycles']
                break
        
        if plateau_point:
            print(f"\n✓ Verilator plateaus around {plateau_point:,} cycles (< 5% change after this)")
        else:
            print(f"\n⚠ Verilator has not plateaued yet at {sv_results[-1]['cycles']:,} cycles")
    
    print(f"\n{'='*100}\n")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])

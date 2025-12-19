"""Performance benchmarks for SystemVerilog simulation"""
import os
import pytest
import shutil
import asyncio
import time
import zuspec.dataclasses as zdc
from zuspec.be.sv import SVGenerator
from pathlib import Path
from dv_flow.mgr import TaskListenerLog, TaskSetRunner, PackageLoader
from dv_flow.mgr.task_graph_builder import TaskGraphBuilder


def get_available_sims():
    """Get list of available simulators."""
    sims = []
    for sim_exe, sim in {
        "verilator": "vlt",
    }.items():
        if shutil.which(sim_exe) is not None:
            sims.append(sim)
    return sims


def benchmark_simulation(name, component_class, testbench, cycles, tmpdir, sim):
    """Run a simulation benchmark and return timing results."""
    
    # Generate SystemVerilog
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
    module_name = module_match.group(1) if module_match else "test"
    
    # Write testbench
    tb_file = output_dir / "tb.sv"
    tb_content = testbench.format(module_name=module_name, cycles=cycles)
    tb_file.write_text(tb_content)
    
    # Setup and run simulation
    start_time = time.perf_counter()
    
    runner = TaskSetRunner(str(Path(tmpdir) / 'rundir'))
    
    def marker_listener(marker):
        from dv_flow.mgr.task_data import SeverityE
        if marker.severity == SeverityE.Error:
            raise Exception(f"Marker error: {marker.msg}")
    
    builder = TaskGraphBuilder(
        PackageLoader(marker_listeners=[marker_listener]).load_rgy(['std', f'hdlsim.{sim}']),
        str(Path(tmpdir) / 'rundir'))
    
    sv_fileset = builder.mkTaskNode(
        'std.FileSet',
        name="sv_files",
        type="systemVerilogSource",
        base=str(output_dir),
        include="*.sv",
        needs=[])
    
    sim_img = builder.mkTaskNode(
        f"hdlsim.{sim}.SimImage",
        name="sim_img",
        top=['tb'],
        needs=[sv_fileset])
    
    sim_run = builder.mkTaskNode(
        f"hdlsim.{sim}.SimRun",
        name="sim_run",
        needs=[sim_img])
    
    runner.add_listener(TaskListenerLog().event)
    out = asyncio.run(runner.run(sim_run))
    
    end_time = time.perf_counter()
    elapsed = end_time - start_time
    
    # Check if simulation succeeded
    if out is None or not hasattr(out, 'output') or out.output is None:
        return {
            'name': name,
            'cycles': cycles,
            'total_time': elapsed,
            'sim_time_ns': 0,
            'throughput': 0,
            'success': False
        }
    
    # Extract simulation time from log
    rundir_fs = None
    for fs in out.output:
        if fs.type == 'std.FileSet' and fs.filetype == "simRunDir":
            rundir_fs = fs
    
    sim_time_ns = 0
    if rundir_fs:
        sim_log_path = os.path.join(rundir_fs.basedir, "sim.log")
        if os.path.isfile(sim_log_path):
            with open(sim_log_path, "r") as f:
                sim_log = f.read()
                # Extract simulation time from Verilator output
                import re
                time_match = re.search(r'at (\d+)ps', sim_log)
                if time_match:
                    sim_time_ns = int(time_match.group(1)) / 1000.0
    
    return {
        'name': name,
        'cycles': cycles,
        'total_time': elapsed,
        'sim_time_ns': sim_time_ns,
        'throughput': cycles / elapsed if elapsed > 0 else 0,
        'ns_per_cycle': sim_time_ns / cycles if cycles > 0 else 0,
        'success': runner.status == 0
    }


@pytest.mark.parametrize("sim", get_available_sims())
def test_simple_counter_perf(tmpdir, sim):
    """Benchmark: Simple counter simulation performance"""
    
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
    
    testbench = """
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
        $display("Final count = %0d", count);
        $finish;
    end
endmodule
"""
    
    result = benchmark_simulation("Simple Counter", Counter, testbench, 1000, tmpdir, sim)
    
    assert result['success']
    print(f"\n{result['name']}:")
    print(f"  Cycles: {result['cycles']}")
    print(f"  Total time: {result['total_time']:.3f}s")
    print(f"  Throughput: {result['throughput']:.0f} cycles/sec")
    print(f"  Sim time per cycle: {result['ns_per_cycle']:.3f} ns")


@pytest.mark.parametrize("sim", get_available_sims())
def test_alu_perf(tmpdir, sim):
    """Benchmark: ALU with arithmetic operations"""
    
    @zdc.dataclass
    class ALU(zdc.Component):
        clock : zdc.bit = zdc.input()
        a : zdc.bit32 = zdc.input()
        b : zdc.bit32 = zdc.input()
        op : zdc.bit8 = zdc.input()
        result : zdc.bit32 = zdc.output()
        
        @zdc.sync(clock=lambda s:s.clock)
        def _compute(self):
            if self.op == 0:
                self.result = self.a + self.b
            else:
                if self.op == 1:
                    self.result = self.a - self.b
                else:
                    if self.op == 2:
                        self.result = self.a * self.b
                    else:
                        self.result = self.a
    
    testbench = """
module tb;
    reg clock;
    reg [31:0] a, b;
    reg [7:0] op;
    wire [31:0] result;
    
    {module_name} dut(
        .clock(clock),
        .a(a),
        .b(b),
        .op(op),
        .result(result)
    );
    
    initial begin
        clock = 0;
        forever #5 clock = ~clock;
    end
    
    integer i;
    initial begin
        a = 0;
        b = 0;
        op = 0;
        
        for (i = 0; i < {cycles}; i = i + 1) begin
            @(posedge clock);
            a = i;
            b = i * 2;
            op = i % 4;
        end
        
        $display("Final result = %0d", result);
        $finish;
    end
endmodule
"""
    
    result = benchmark_simulation("ALU", ALU, testbench, 1000, tmpdir, sim)
    
    assert result['success']
    print(f"\n{result['name']}:")
    print(f"  Cycles: {result['cycles']}")
    print(f"  Total time: {result['total_time']:.3f}s")
    print(f"  Throughput: {result['throughput']:.0f} cycles/sec")


@pytest.mark.parametrize("sim", get_available_sims())
def test_pipeline_perf(tmpdir, sim):
    """Benchmark: Multi-stage pipeline"""
    
    @zdc.dataclass
    class Pipeline(zdc.Component):
        clock : zdc.bit = zdc.input()
        data_in : zdc.bit32 = zdc.input()
        data_out : zdc.bit32 = zdc.output()
        
        _stage1 : zdc.bit32 = zdc.field()
        _stage2 : zdc.bit32 = zdc.field()
        _stage3 : zdc.bit32 = zdc.field()
        
        @zdc.sync(clock=lambda s:s.clock)
        def _pipeline(self):
            self._stage1 = self.data_in + 1
            self._stage2 = self._stage1 + 2
            self._stage3 = self._stage2 + 3
            self.data_out = self._stage3
    
    testbench = """
module tb;
    reg clock;
    reg [31:0] data_in;
    wire [31:0] data_out;
    
    {module_name} dut(
        .clock(clock),
        .data_in(data_in),
        .data_out(data_out)
    );
    
    initial begin
        clock = 0;
        forever #5 clock = ~clock;
    end
    
    integer i;
    initial begin
        data_in = 0;
        
        for (i = 0; i < {cycles}; i = i + 1) begin
            @(posedge clock);
            data_in = i;
        end
        
        $display("Final output = %0d", data_out);
        $finish;
    end
endmodule
"""
    
    result = benchmark_simulation("Pipeline", Pipeline, testbench, 1000, tmpdir, sim)
    
    assert result['success']
    print(f"\n{result['name']}:")
    print(f"  Cycles: {result['cycles']}")
    print(f"  Total time: {result['total_time']:.3f}s")
    print(f"  Throughput: {result['throughput']:.0f} cycles/sec")


@pytest.mark.parametrize("sim", get_available_sims())
@pytest.mark.parametrize("cycles", [100, 1000, 10000])
def test_scaling_performance(tmpdir, sim, cycles):
    """Benchmark: Test how performance scales with cycle count"""
    
    @zdc.dataclass
    class ScaleTest(zdc.Component):
        clock : zdc.bit = zdc.input()
        data : zdc.bit32 = zdc.output()
        
        @zdc.sync(clock=lambda s:s.clock)
        def _increment(self):
            self.data += 1
    
    testbench = """
module tb;
    reg clock;
    wire [31:0] data;
    
    {module_name} dut(
        .clock(clock),
        .data(data)
    );
    
    initial begin
        clock = 0;
        forever #5 clock = ~clock;
    end
    
    initial begin
        repeat({cycles}) @(posedge clock);
        $display("Data = %0d", data);
        $finish;
    end
endmodule
"""
    
    result = benchmark_simulation(f"Scaling Test ({cycles} cycles)", ScaleTest, testbench, cycles, tmpdir, sim)
    
    assert result['success']
    print(f"\n{result['name']}:")
    print(f"  Cycles: {result['cycles']}")
    print(f"  Total time: {result['total_time']:.3f}s")
    print(f"  Throughput: {result['throughput']:.0f} cycles/sec")
    print(f"  Time/cycle: {result['total_time']/cycles*1000:.3f} ms")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])

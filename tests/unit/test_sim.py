import os
import pytest
import shutil
import asyncio
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

@pytest.mark.parametrize("sim", get_available_sims())
def test_counter_sim(tmpdir, sim):
    """Test Counter component with Verilator simulation."""

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
    
    output_dir = Path(tmpdir) / "sv"
    output_dir.mkdir(exist_ok=True)
    generator = SVGenerator(output_dir)
    sv_files = generator.generate(ctxt)
    
    # Get the generated module name
    sv_content = sv_files[0].read_text()
    import re
    module_match = re.search(r'module\s+(\w+)', sv_content)
    module_name = module_match.group(1) if module_match else "Counter"
    
    # Create testbench
    sv_tb = f"""
module tb;
    reg clock;
    reg reset;
    wire [31:0] count;
    
    {module_name} counter_inst(
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
        #20;
        reset = 0;
        #100;
        $display("Count = %0d", count);
        if (count == 10) begin
            $display("PASS");
        end else begin
            $display("FAIL: Expected count=10, got %0d", count);
        end
        $finish;
    end
endmodule
"""
    
    # Write testbench
    tb_file = output_dir / "tb.sv"
    tb_file.write_text(sv_tb)
    
    # Setup DFM task graph and run simulation
    runner = TaskSetRunner(str(Path(tmpdir) / 'rundir'))
    
    def marker_listener(marker):
        # Only raise on errors, not warnings
        from dv_flow.mgr.task_data import SeverityE
        if marker.severity == SeverityE.Error:
            print(f"ERROR: {marker.msg}")
            if marker.loc:
                print(f"  at {marker.loc.filename}:{marker.loc.line}")
            raise Exception(f"Marker error: {marker.msg}")
    
    builder = TaskGraphBuilder(
        PackageLoader(marker_listeners=[marker_listener]).load_rgy(['std', f'hdlsim.{sim}']),
        str(Path(tmpdir) / 'rundir'))
    
    # Create FileSet for all SV files
    sv_fileset = builder.mkTaskNode(
        'std.FileSet',
        name="sv_files",
        type="systemVerilogSource",
        base=str(output_dir),
        include="*.sv",
        needs=[])
    
    # Create SimImage
    sim_img = builder.mkTaskNode(
        f"hdlsim.{sim}.SimImage",
        name="sim_img",
        top=['tb'],
        needs=[sv_fileset])
    
    # Create SimRun
    sim_run = builder.mkTaskNode(
        f"hdlsim.{sim}.SimRun",
        name="sim_run",
        needs=[sim_img])
    
    # Run simulation
    runner.add_listener(TaskListenerLog().event)
    out = asyncio.run(runner.run(sim_run))
    
    assert runner.status == 0
    
    # Find simulation log
    rundir_fs = None
    for fs in out.output:
        if fs.type == 'std.FileSet' and fs.filetype == "simRunDir":
            rundir_fs = fs
    
    assert rundir_fs is not None
    assert rundir_fs.src == "sim_run"
    
    # Check simulation log
    sim_log_path = os.path.join(rundir_fs.basedir, "sim.log")
    assert os.path.isfile(sim_log_path)
    
    with open(sim_log_path, "r") as f:
        sim_log = f.read()
    
    print(f"\n=== Simulation Log ===\n{sim_log}\n======================\n")
    
    # Verify test passed
    assert "PASS" in sim_log or "Count = 10" in sim_log

@pytest.mark.parametrize("sim", get_available_sims())
def test_extern_full_flow_sim(tmpdir, sim):
    """End-to-end test using an extern SV module bound into a generated top."""

    @zdc.dataclass
    class CounterImpl(zdc.Extern[zdc.Component]):
        clock : zdc.bit = zdc.input()
        reset : zdc.bit = zdc.input()
        count : zdc.bit32 = zdc.output()

        attributes = {
            "name": "ext_counter"
        }

    ext_counter_sv = """
module ext_counter(
  input logic clock,
  input logic reset,
  output logic [31:0] count);

  always_ff @(posedge clock or posedge reset) begin
    if (reset) begin
      count <= '0;
    end else begin
      count <= count + 1;
    end
  end
endmodule
"""

    @zdc.dataclass
    class CounterTop(zdc.Component):
        clock : zdc.bit = zdc.input()
        reset : zdc.bit = zdc.input()
        count : zdc.bit32 = zdc.output()

        impl : CounterImpl = zdc.field()

        def __bind__(self):
            return {
                self.impl.clock : self.clock,
                self.impl.reset : self.reset,
                self.impl.count : self.count
            }

    # Generate Zuspec to SV
    factory = zdc.DataModelFactory()
    ctxt = factory.build([CounterTop, CounterImpl])

    output_dir = Path(tmpdir) / "sv"
    output_dir.mkdir(exist_ok=True)
    generator = SVGenerator(output_dir)
    sv_files = generator.generate(ctxt)

    # Provide the external module source alongside generated SV
    (output_dir / "ext_counter.sv").write_text(ext_counter_sv)

    # Get the generated module name
    sv_content = sv_files[0].read_text()
    import re
    module_match = re.search(r"module\s+(\w+)", sv_content)
    module_name = module_match.group(1) if module_match else "CounterTop"

    # Create testbench
    sv_tb = f"""
module tb;
    logic clock;
    logic reset;
    logic [31:0] count;

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
        #20;
        reset = 0;
        #100;
        $display(\"Count = %0d\", count);
        if (count == 10) begin
            $display(\"PASS\");
        end else begin
            $display(\"FAIL: Expected count=10, got %0d\", count);
        end
        $finish;
    end
endmodule
"""

    tb_file = output_dir / "tb.sv"
    tb_file.write_text(sv_tb)

    # Setup DFM task graph and run simulation
    runner = TaskSetRunner(str(Path(tmpdir) / 'rundir'))

    def marker_listener(marker):
        from dv_flow.mgr.task_data import SeverityE
        if marker.severity == SeverityE.Error:
            print(f"ERROR: {marker.msg}")
            if marker.loc:
                print(f"  at {marker.loc.filename}:{marker.loc.line}")
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

    assert runner.status == 0

    # Find simulation log
    rundir_fs = None
    for fs in out.output:
        if fs.type == 'std.FileSet' and fs.filetype == "simRunDir":
            rundir_fs = fs

    assert rundir_fs is not None

    sim_log_path = os.path.join(rundir_fs.basedir, "sim.log")
    assert os.path.isfile(sim_log_path)

    sim_log = Path(sim_log_path).read_text()
    assert "PASS" in sim_log or "Count = 10" in sim_log


import pytest
import os
import shutil
import asyncio
import zuspec.dataclasses as zdc
from zuspec.dataclasses import ir
from typing import Protocol
from zuspec.be.sv import SVGenerator
from pathlib import Path
from dv_flow.mgr import TaskListenerLog, TaskSetRunner, PackageLoader
from dv_flow.mgr.task_graph_builder import TaskGraphBuilder


def get_available_sims():
    """Get list of available simulators."""
    sims = []
    for sim_exe, sim in {
        "verilator": "vlt",
        "vsim": "mti",
        "xsim": "xsm",
    }.items():
        if shutil.which(sim_exe) is not None:
            sims.append(sim)
    return sims


def test_core_smoke(tmpdir):
    """Test Verilog generation for XtorCore component."""

    @zdc.dataclass
    class XtorCore(zdc.Component):
        clock : zdc.bit = zdc.input()
        reset : zdc.bit = zdc.input()
        ready : zdc.bit = zdc.output()
        valid : zdc.bit = zdc.input()
        data_i : zdc.u32 = zdc.input()
        data_o : zdc.u32 = zdc.output()

        @zdc.sync(clock=lambda s:s.clock, reset=lambda s:s.reset)
        def _behavior(self):
            if self.reset:
                self.data_o = 0
                self.ready = 0
            else:
                # ready mirrors valid (delayed by one clock)
                self.ready = self.valid
                if self.valid:
                    self.data_o = self.data_i + 1

    # Generate Verilog from Zuspec Component
    factory = zdc.DataModelFactory()
    ctxt = factory.build(XtorCore)
    
    output_dir = Path(tmpdir)
    generator = SVGenerator(output_dir)
    sv_files = generator.generate(ctxt)
    
    # Verify files were created
    assert len(sv_files) > 0
    assert sv_files[0].exists()
    
    # Check generated SystemVerilog
    sv_content = sv_files[0].read_text()
    print("\n=== Generated SystemVerilog ===")
    print(sv_content)
    print("=== End Generated SystemVerilog ===\n")
    
    # Verify module declaration
    assert "module test_core_smoke" in sv_content and "XtorCore" in sv_content
    
    # Verify ports
    assert "input logic clock" in sv_content
    assert "input logic reset" in sv_content
    assert "output logic ready" in sv_content
    assert "input logic valid" in sv_content
    assert "input logic [31:0] data_i" in sv_content
    assert "output logic [31:0] data_o" in sv_content
    
    # Verify always block
    assert "always @(posedge clock or posedge reset)" in sv_content
    
    # Verify reset logic
    assert "if (reset)" in sv_content
    assert "data_o <= 0" in sv_content
    assert "ready <= 0" in sv_content
    
    # Verify ready assignment in else block (mirrors valid)
    assert "ready <= valid" in sv_content
    
    # Verify data logic (no longer checks ready, just valid)
    assert "if (valid)" in sv_content
    assert "data_o <= data_i + 1" in sv_content
    
    # Verify endmodule
    assert "endmodule" in sv_content



@pytest.mark.parametrize("sim", get_available_sims())
def test_xtor_smoke_sim(tmpdir, sim):
    """Test Xtor transactor with simulation - calls xtor_if.send() task."""

    @zdc.dataclass
    class XtorCore(zdc.Component):
        clock : zdc.bit = zdc.input()
        reset : zdc.bit = zdc.input()
        ready : zdc.bit = zdc.output()
        valid : zdc.bit = zdc.input()
        data_i : zdc.u32 = zdc.input()
        data_o : zdc.u32 = zdc.output()

        @zdc.sync(clock=lambda s:s.clock, reset=lambda s:s.reset)
        def _behavior(self):
            if self.reset:
                self.data_o = 0
                self.ready = 0
            else:
                # ready mirrors valid (delayed by one clock)
                self.ready = self.valid
                if self.valid:
                    self.data_o = self.data_i + 1

    class IXtor(Protocol):
        async def send(self, data : zdc.u32) -> zdc.u32: ...

    @zdc.dataclass
    class Xtor(zdc.XtorComponent[IXtor]):
        clock : zdc.bit = zdc.input()
        reset : zdc.bit = zdc.input()
        ready : zdc.bit = zdc.field()
        valid : zdc.bit = zdc.field()
        data_i : zdc.u32 = zdc.field()
        data_o : zdc.u32 = zdc.field()

        core : XtorCore = zdc.inst()

        xtor_if : IXtor = zdc.export()

        def __bind__(self):
            return {
                self.core.clock: self.clock,
                self.core.reset: self.reset,
                self.ready: self.core.ready,
                self.valid: self.core.valid,
                self.data_i: self.core.data_i,
                self.data_o: self.core.data_o,
                self.xtor_if.send: self.send
            }

        async def send(self, data : zdc.u32) -> zdc.u32:
            await zdc.posedge(self.clock)
            
            while self.reset:
                await zdc.posedge(self.clock)
            
            self.data_i = data
            self.valid = 1
            
            while not self.ready:
                await zdc.posedge(self.clock)
            
            # At this point, ready=1 means data has been processed
            # and data_o is valid
            result = self.data_o
            # Clear valid after transaction completes
            self.valid = 0
            await zdc.posedge(self.clock)
            
            return result

    # Generate Verilog
    factory = zdc.DataModelFactory()
    ctxt = factory.build([Xtor, XtorCore])
    
    output_dir = Path(tmpdir) / "sv"
    output_dir.mkdir(exist_ok=True)
    generator = SVGenerator(output_dir)
    sv_files = generator.generate(ctxt)
    
    # Find generated Xtor module name
    xtor_module = None
    for f in sv_files:
        content = f.read_text()
        if "interface" in content and "xtor_if" in content:
            import re
            match = re.search(r'module\s+(\S+)\s*\(', content)
            if match:
                xtor_module = match.group(1)
                break
    
    assert xtor_module is not None, "Could not find Xtor module name"
    
    # Load testbench template from data file
    tb_template_path = Path(__file__).parent / "data" / "test_xtor_smoke" / "tb_xtor_smoke_sim.sv"
    sv_tb = tb_template_path.read_text().replace("{module_name}", xtor_module)
    
    # Write testbench
    tb_file = output_dir / "tb.sv"
    tb_file.write_text(sv_tb)
    
    # Setup DFM and run simulation
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
    
    assert runner.status == 0, f"Simulation failed with status {runner.status}"
    
    # Find simulation run directory
    rundir_fs = None
    for fs in out.output:
        if fs.type == 'std.FileSet' and fs.filetype == "simRunDir":
            rundir_fs = fs
    
    assert rundir_fs is not None, "Could not find simulation run directory"
    
    # Check simulation log
    sim_log_path = os.path.join(rundir_fs.basedir, "sim.log")
    assert os.path.isfile(sim_log_path), f"Simulation log not found at {sim_log_path}"
    
    with open(sim_log_path, "r") as f:
        sim_log = f.read()
    
    print(f"\n=== Simulation Log ({sim}) ===\n{sim_log}\n======================\n")
    
    # Verify test passed
    assert "TEST PASSED" in sim_log, f"Test did not pass for simulator {sim}"

def test_xtor_smoke(tmpdir):
    """Test Verilog generation for Xtor transactor component."""

    @zdc.dataclass
    class XtorCore(zdc.Component):
        clock : zdc.bit = zdc.input()
        reset : zdc.bit = zdc.input()
        ready : zdc.bit = zdc.output()
        valid : zdc.bit = zdc.input()
        data_i : zdc.u32 = zdc.input()
        data_o : zdc.u32 = zdc.output()

        @zdc.sync(clock=lambda s:s.clock, reset=lambda s:s.reset)
        def _behavior(self):
            if self.reset:
                self.data_o = 0
                self.ready = 0
            else:
                # ready mirrors valid (delayed by one clock)
                self.ready = self.valid
                if self.valid:
                    self.data_o = self.data_i + 1

    class IXtor(Protocol):
        async def send(self, data : zdc.u32) -> zdc.u32: ...

    @zdc.dataclass
    class Xtor(zdc.XtorComponent[IXtor]):
        clock : zdc.bit = zdc.input()
        reset : zdc.bit = zdc.input()
        ready : zdc.bit = zdc.field()
        valid : zdc.bit = zdc.field()
        data_i : zdc.u32 = zdc.field()
        data_o : zdc.u32 = zdc.field()

        core : XtorCore = zdc.inst()

        def __bind__(self):
            return {
                self.core.clock: self.clock,
                self.core.reset: self.reset,
                self.ready: self.core.ready,
                self.valid: self.core.valid,
                self.data_i: self.core.data_i,
                self.data_o: self.core.data_o,
                self.xtor_if.send: self.send
            }

        async def send(self, data : zdc.u32) -> zdc.u32:
            # Wait one clock cycle
            await zdc.posedge(self.clock)
            
            # Wait for out of reset
            while self.reset:
                await zdc.posedge(self.clock)
            
            # Send data
            self.data_i = data
            self.valid = 1
            
            # Wait for ready
            while not self.ready:
                await zdc.posedge(self.clock)
            
            # Capture result
            result = self.data_o
            
            # Clear valid after transaction completes
            self.valid = 0
            await zdc.posedge(self.clock)
            
            return result

    # Build the IR
    factory = zdc.DataModelFactory()
    ctxt = factory.build([Xtor, XtorCore])
    
    # Find the Xtor component
    xtor_comp = None
    for name, dtype in ctxt.type_m.items():
        if isinstance(dtype, ir.DataTypeComponent) and 'Xtor' in name and 'Core' not in name:
            xtor_comp = dtype
            break
    
    assert xtor_comp is not None, "Could not find Xtor component in IR"
    
    # Check that xtor_if export field exists
    export_field = None
    for field in xtor_comp.fields:
        if field.kind == ir.FieldKind.Export:
            export_field = field
            break
    
    assert export_field is not None, "Could not find export field in Xtor component"
    assert export_field.name == "xtor_if", f"Expected export field named 'xtor_if', got '{export_field.name}'"
    
    # Check that send method is in functions list
    send_method = None
    for method in xtor_comp.functions:
        if method.name == "send":
            send_method = method
            break
    
    assert send_method is not None, "Could not find 'send' method in Xtor component"
    
    # Generate SystemVerilog
    output_dir = Path(tmpdir)
    generator = SVGenerator(output_dir)
    sv_files = generator.generate(ctxt)
    
    assert len(sv_files) > 0, "No SV files generated"
    
    # Find the Xtor SV file (not XtorCore)
    xtor_sv_file = None
    for f in sv_files:
        if 'Xtor' in f.name and 'Core' not in f.name:
            xtor_sv_file = f
            break
    
    assert xtor_sv_file is not None, "Could not find Xtor SV file"
    
    sv_content = xtor_sv_file.read_text()
    print("\n=== Generated SystemVerilog ===")
    print(sv_content)
    print("=== End Generated SystemVerilog ===\n")
    
    # Verify interface was generated
    assert "interface" in sv_content, "No interface generated"
    assert "xtor_if" in sv_content, "xtor_if not found in generated SV"
    assert "task send" in sv_content, "send task not found in generated SV"
    assert "endinterface" in sv_content, "endinterface not found"
    assert "@(posedge" in sv_content, "No posedge timing control found"
    assert "__ret" in sv_content, "Return parameter __ret not found"
    assert "input logic [31:0] data" in sv_content, "Input parameter data not found"
    
    # Verify while loops are generated
    assert "while (reset)" in sv_content, "while (reset) loop not found"
    assert "while (!ready)" in sv_content, "while (!ready) loop not found"
    
    # Verify module structure
    assert "module test_xtor_smoke" in sv_content, "Module declaration not found"
    assert "input logic clock" in sv_content, "clock input not found"
    assert "input logic reset" in sv_content, "reset input not found"
    assert "endmodule" in sv_content, "endmodule not found"
    
    # Verify internal signals
    assert "logic ready" in sv_content, "ready signal not declared"
    assert "logic valid" in sv_content, "valid signal not declared"
    assert "logic [31:0] data_i" in sv_content, "data_i signal not declared"
    assert "logic [31:0] data_o" in sv_content, "data_o signal not declared"
    
    # Verify core instance
    assert "XtorCore core" in sv_content, "core instance not found"
    assert ".clock(clock)" in sv_content, "core clock connection not found"
    assert ".reset(reset)" in sv_content, "core reset connection not found"
    assert ".ready(ready)" in sv_content, "core ready connection not found"
    assert ".valid(valid)" in sv_content, "core valid connection not found"
    assert ".data_i(data_i)" in sv_content, "core data_i connection not found"
    assert ".data_o(data_o)" in sv_content, "core data_o connection not found"
    
    # Verify task body
    assert "data_i = data;" in sv_content, "data_i assignment not found in task"
    assert "valid = 1;" in sv_content, "valid assignment not found in task"
    assert "result = data_o;" in sv_content, "result assignment not found in task"
    assert "__ret = result;" in sv_content, "return assignment not found in task"
    
    print("✓ All SystemVerilog structure checks passed!")


@pytest.mark.parametrize("sim", get_available_sims())
def test_xtor_interface_sim(tmpdir, sim):
    """Test XtorComponent interface with simulation - calls xtor_if.send()."""

    @zdc.dataclass
    class XtorCore(zdc.Component):
        clock : zdc.bit = zdc.input()
        reset : zdc.bit = zdc.input()
        ready : zdc.bit = zdc.output()
        valid : zdc.bit = zdc.input()
        data_i : zdc.u32 = zdc.input()
        data_o : zdc.u32 = zdc.output()

        @zdc.sync(clock=lambda s:s.clock, reset=lambda s:s.reset)
        def _behavior(self):
            if self.reset:
                self.data_o = 0
                self.ready = 0
            else:
                # ready mirrors valid (delayed by one clock)
                self.ready = self.valid
                if self.valid:
                    self.data_o = self.data_i + 1

    class IXtor(Protocol):
        async def send(self, data : zdc.u32) -> zdc.u32: ...

    @zdc.dataclass
    class Xtor(zdc.XtorComponent[IXtor]):
        clock : zdc.bit = zdc.input()
        reset : zdc.bit = zdc.input()
        ready : zdc.bit = zdc.field()
        valid : zdc.bit = zdc.field()
        data_i : zdc.u32 = zdc.field()  # Changed from input() to field()
        data_o : zdc.u32 = zdc.field()

        core : XtorCore = zdc.inst()

        def __bind__(self):
            return {
                self.core.clock: self.clock,
                self.core.reset: self.reset,
                self.ready: self.core.ready,
                self.valid: self.core.valid,
                self.data_i: self.core.data_i,
                self.data_o: self.core.data_o,
                self.xtor_if.send: self.send
            }

        async def send(self, data : zdc.u32) -> zdc.u32:
            await zdc.posedge(self.clock)
            
            while self.reset:
                await zdc.posedge(self.clock)
            
            self.data_i = data
            self.valid = 1
            
            while not self.ready:
                await zdc.posedge(self.clock)
            
            # At this point, ready=1 means data has been processed
            # and data_o is valid
            result = self.data_o
            # Clear valid after transaction completes
            self.valid = 0
            await zdc.posedge(self.clock)
            
            return result

    # Generate Verilog
    factory = zdc.DataModelFactory()
    ctxt = factory.build([Xtor, XtorCore])
    
    output_dir = Path(tmpdir) / "sv"
    output_dir.mkdir(exist_ok=True)
    generator = SVGenerator(output_dir)
    sv_files = generator.generate(ctxt)
    
    # Find generated module names
    xtor_module = None
    for f in sv_files:
        content = f.read_text()
        if "interface" in content and "xtor_if" in content:
            import re
            match = re.search(r'module\s+(\S+)\s*\(', content)
            if match:
                xtor_module = match.group(1)
                break
    
    assert xtor_module is not None, "Could not find Xtor module name"
    
    # Load testbench template from data file
    tb_template_path = Path(__file__).parent / "data" / "test_xtor_smoke" / "tb_xtor_interface_sim.sv"
    sv_tb = tb_template_path.read_text().replace("{module_name}", xtor_module)
    
    # Write testbench
    tb_file = output_dir / "tb.sv"
    tb_file.write_text(sv_tb)
    
    # Setup DFM and run simulation
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
    
    assert runner.status == 0, f"Simulation failed with status {runner.status}"
    
    # Find simulation log
    rundir_fs = None
    for fs in out.output:
        if fs.type == 'std.FileSet' and fs.filetype == "simRunDir":
            rundir_fs = fs
    
    assert rundir_fs is not None, "Could not find simulation run directory"
    
    sim_log_path = os.path.join(rundir_fs.basedir, "sim.log")
    assert os.path.isfile(sim_log_path), f"Simulation log not found at {sim_log_path}"
    
    with open(sim_log_path, "r") as f:
        sim_log = f.read()
    
    print(f"\n=== Simulation Log ({sim}) ===\n{sim_log}\n======================\n")
    
    # Verify test passed
    assert "TEST PASSED" in sim_log, f"Test did not pass for simulator {sim}"

@pytest.mark.parametrize("sim", get_available_sims())
def test_xtor_interface_bundle_sim(tmpdir, sim):
    """Test XtorComponent interface with simulation - uses Bundle to group signals."""

    @zdc.dataclass
    class RV(zdc.Struct):
        ready : zdc.bit = zdc.output()
        valid : zdc.bit = zdc.input()
        data_i : zdc.u32 = zdc.input()
        data_o : zdc.u32 = zdc.output()


    @zdc.dataclass
    class XtorCore(zdc.Component):
        clock : zdc.bit = zdc.input()
        reset : zdc.bit = zdc.input()
        io : RV = zdc.bundle()

        @zdc.sync(clock=lambda s:s.clock, reset=lambda s:s.reset)
        def _behavior(self):
            if self.reset:
                self.io.data_o = 0
                self.io.ready = 0
            else:
                # ready mirrors valid (delayed by one clock)
                self.io.ready = self.io.valid
                if self.io.valid:
                    self.io.data_o = self.io.data_i + 1

    class IXtor(Protocol):
        async def send(self, data : zdc.u32) -> zdc.u32: ...

    @zdc.dataclass
    class Xtor(zdc.XtorComponent[IXtor]):
        clock : zdc.bit = zdc.input()
        reset : zdc.bit = zdc.input()
        io : RV = zdc.field() # Making this a field makes it data

        core : XtorCore = zdc.inst()

        def __bind__(self):
            return {
                self.core.clock: self.clock,
                self.core.reset: self.reset,
                self.io: self.core.io,
                self.xtor_if.send: self.send
            }

        async def send(self, data : zdc.u32) -> zdc.u32:
            await zdc.posedge(self.clock)
            
            while self.reset:
                await zdc.posedge(self.clock)
            
            self.io.data_i = data
            self.io.valid = 1
            
            while not self.io.ready:
                await zdc.posedge(self.clock)
            
            # At this point, ready=1 means data has been processed
            # and data_o is valid
            result = self.io.data_o
            # Clear valid after transaction completes
            self.io.valid = 0
            await zdc.posedge(self.clock)
            
            return result

    # Generate Verilog
    factory = zdc.DataModelFactory()
    ctxt = factory.build([Xtor, XtorCore, RV])
    
    output_dir = Path(tmpdir) / "sv"
    output_dir.mkdir(exist_ok=True)
    generator = SVGenerator(output_dir)
    sv_files = generator.generate(ctxt)
    
    # Find generated module names
    xtor_module = None
    for f in sv_files:
        content = f.read_text()
        if "interface" in content and "xtor_if" in content:
            import re
            match = re.search(r'module\s+(\S+)\s*\(', content)
            if match:
                xtor_module = match.group(1)
                break
    
    assert xtor_module is not None, "Could not find Xtor module name"
    
    # Load testbench template from data file (bundle-specific testbench)
    tb_template_path = Path(__file__).parent / "data" / "test_xtor_smoke" / "tb_xtor_interface_bundle_sim.sv"
    sv_tb = tb_template_path.read_text().replace("{module_name}", xtor_module)
    
    # Write testbench
    tb_file = output_dir / "tb.sv"
    tb_file.write_text(sv_tb)
    
    # Setup DFM and run simulation
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
    
    assert runner.status == 0, f"Simulation failed with status {runner.status}"
    
    # Find simulation log
    rundir_fs = None
    for fs in out.output:
        if fs.type == 'std.FileSet' and fs.filetype == "simRunDir":
            rundir_fs = fs
    
    assert rundir_fs is not None, "Could not find simulation run directory"
    
    sim_log_path = os.path.join(rundir_fs.basedir, "sim.log")
    assert os.path.isfile(sim_log_path), f"Simulation log not found at {sim_log_path}"
    
    with open(sim_log_path, "r") as f:
        sim_log = f.read()
    
    print(f"\n=== Simulation Log ({sim}) ===\n{sim_log}\n======================\n")
    
    # Verify test passed
    assert "TEST PASSED" in sim_log, f"Test did not pass for simulator {sim}"

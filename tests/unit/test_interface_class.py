"""Unit tests for SV `interface class` and `implements` emission (milestone A).

Foundational support for the OO interface-class projection: the be-sv IR can
represent SV interface classes (pure-virtual prototype containers) and concrete
classes that `implements` them.
"""
import shutil
import subprocess

import pytest

from zuspec.be.sv.ir.sv import (
    SVArg,
    SVClass,
    SVFunctionDecl,
    SVInterfaceClass,
    SVTaskDecl,
)
from zuspec.be.sv.ir.sv_emit import SVEmitter


def _emit(node) -> str:
    return SVEmitter().emit_one(node)


def test_interface_class_basic():
    """interface class with a pure-virtual task prototype."""
    ic = SVInterfaceClass(name="export_api_if", tasks=[SVTaskDecl(name="Entry")])
    sv = _emit(ic)
    assert "interface class export_api_if;" in sv
    assert "pure virtual task Entry();" in sv
    assert sv.strip().endswith("endclass")


def test_interface_class_forces_pure():
    """Method flags are coerced to pure virtual inside an interface class."""
    ic = SVInterfaceClass(
        name="api_if",
        tasks=[SVTaskDecl(name="run", is_virtual=True, is_pure=False,
                          body_lines=["$display();"])],
        functions=[SVFunctionDecl(name="get", return_type="int")],
    )
    sv = _emit(ic)
    assert "pure virtual task run();" in sv
    assert "pure virtual function int get();" in sv
    # no body leaks in
    assert "$display" not in sv
    assert "endtask" not in sv


def test_interface_class_extends():
    """interface class extending other interface classes."""
    ic = SVInterfaceClass(name="c_if", extends=["a_if", "b_if"],
                          functions=[SVFunctionDecl(name="f", return_type="void")])
    sv = _emit(ic)
    assert "interface class c_if extends a_if, b_if;" in sv


def test_class_implements_single():
    """class implementing one interface class."""
    cls = SVClass(name="impl", implements=["export_api_if"],
                  tasks=[SVTaskDecl(name="Entry", body_lines=['$write("hi");'])])
    sv = _emit(cls)
    assert "class impl implements export_api_if;" in sv
    assert "task Entry();" in sv  # concrete body, not pure
    assert "endtask" in sv


def test_class_implements_multiple_with_extends():
    """class with both extends and implements."""
    cls = SVClass(name="impl", extends_name="base_c",
                  implements=["a_if", "b_if"])
    sv = _emit(cls)
    assert "class impl extends base_c implements a_if, b_if;" in sv


def test_class_no_implements_unchanged():
    """Absent implements list -> no `implements` clause (regression guard)."""
    sv = _emit(SVClass(name="plain", extends_name="base_c"))
    assert "class plain extends base_c;" in sv
    assert "implements" not in sv


@pytest.mark.skipif(shutil.which("verilator") is None,
                    reason="verilator not on PATH")
def test_emitted_interface_class_elaborates(tmp_path):
    """The emitted interface-class + impl + caller elaborates under Verilator."""
    em = SVEmitter()
    api = em.emit_one(SVInterfaceClass(name="export_api_if",
                                       tasks=[SVTaskDecl(name="Entry")]))
    impl = em.emit_one(SVClass(
        name="export_api_impl", implements=["export_api_if"],
        tasks=[SVTaskDecl(name="Entry", body_lines=['$write("ok");'])]))
    src = tmp_path / "t.sv"
    src.write_text(
        "package p;\n"
        + "\n".join("  " + l for l in api.splitlines()) + "\n"
        + "\n".join("  " + l for l in impl.splitlines()) + "\n"
        + "endpackage\n"
        + "module top; import p::*;\n"
        "  initial begin export_api_if h = export_api_impl'(null); $finish; end\n"
        "endmodule\n"
    )
    r = subprocess.run(
        ["verilator", "--binary", "-Wno-fatal", "-sv", str(src), "--top-module", "top"],
        cwd=str(tmp_path), capture_output=True, text=True)
    assert r.returncode == 0, f"verilator failed:\n{r.stderr}"

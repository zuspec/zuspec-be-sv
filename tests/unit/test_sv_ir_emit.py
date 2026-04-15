"""Unit tests for SV IR node construction and SVEmitter serialization.

Every new IR node type introduced in Phase 1 is exercised here.
Tests are pure IR construction + SVEmitter + string comparison -- no
PSS input, no simulator.
"""

import textwrap

import pytest

from zuspec.be.sv.ir.sv import (
    SVArg,
    SVClass,
    SVClassField,
    SVConstraintBlock,
    SVField,
    SVForwardDecl,
    SVFunctionDecl,
    SVImportDPI,
    SVInterface,
    SVLineDirective,
    SVModuleDecl,
    SVPackage,
    SVRawItem,
    SVTaskDecl,
    SVTypedefEnum,
    SVTypedefStruct,
)
from zuspec.be.sv.ir.sv_emit import SVEmitter


@pytest.fixture
def emitter():
    return SVEmitter()


# ------------------------------------------------------------------ #
# SVClassField
# ------------------------------------------------------------------ #

class TestSVClassField:
    def test_plain_field(self, emitter):
        cf = SVClassField(name="data", dtype="int unsigned")
        text = emitter.emit_one(cf)
        assert text == "int unsigned data;"

    def test_rand_field(self, emitter):
        cf = SVClassField(name="addr", dtype="bit [31:0]", is_rand=True)
        text = emitter.emit_one(cf)
        assert text == "rand bit [31:0] addr;"

    def test_randc_field(self, emitter):
        cf = SVClassField(name="mode", dtype="bit [1:0]", is_randc=True)
        text = emitter.emit_one(cf)
        assert text == "randc bit [1:0] mode;"

    def test_randc_overrides_rand(self, emitter):
        """randc takes precedence when both flags are set."""
        cf = SVClassField(name="x", dtype="int", is_rand=True, is_randc=True)
        text = emitter.emit_one(cf)
        assert text.startswith("randc ")

    def test_initial_value(self, emitter):
        cf = SVClassField(name="count", dtype="int", initial_value="0")
        text = emitter.emit_one(cf)
        assert text == "int count = 0;"


# ------------------------------------------------------------------ #
# SVConstraintBlock
# ------------------------------------------------------------------ #

class TestSVConstraintBlock:
    def test_simple_constraint(self, emitter):
        cb = SVConstraintBlock(name="addr_align", exprs=["addr[1:0] == 2'b0"])
        text = emitter.emit_one(cb)
        assert "constraint addr_align {" in text
        assert "addr[1:0] == 2'b0;" in text
        assert text.strip().endswith("}")

    def test_multiple_exprs(self, emitter):
        cb = SVConstraintBlock(name="bounds", exprs=[
            "addr >= 32'h1000",
            "addr <= 32'hFFFF",
        ])
        text = emitter.emit_one(cb)
        assert "addr >= 32'h1000;" in text
        assert "addr <= 32'hFFFF;" in text

    def test_implication(self, emitter):
        cb = SVConstraintBlock(name="impl_c", exprs=[
            "mode == 1 -> addr < 32'h100",
        ])
        text = emitter.emit_one(cb)
        assert "mode == 1 -> addr < 32'h100;" in text

    def test_if_else(self, emitter):
        cb = SVConstraintBlock(name="cond_c", exprs=[
            "if (mode == 0) { addr == 0; } else { addr > 0; }",
        ])
        text = emitter.emit_one(cb)
        assert "if (mode == 0)" in text

    def test_foreach(self, emitter):
        cb = SVConstraintBlock(name="arr_c", exprs=[
            "foreach (data[i]) { data[i] inside {[0:255]}; }",
        ])
        text = emitter.emit_one(cb)
        assert "foreach (data[i])" in text

    def test_unique(self, emitter):
        cb = SVConstraintBlock(name="uniq_c", exprs=[
            "unique {a, b, c}",
        ])
        text = emitter.emit_one(cb)
        assert "unique {a, b, c};" in text


# ------------------------------------------------------------------ #
# SVTaskDecl
# ------------------------------------------------------------------ #

class TestSVTaskDecl:
    def test_simple_task(self, emitter):
        td = SVTaskDecl(name="run", body_lines=[
            "$display(\"running\");",
        ])
        text = emitter.emit_one(td)
        assert "task run();" in text
        assert "$display(\"running\");" in text
        assert "endtask" in text

    def test_task_with_args(self, emitter):
        td = SVTaskDecl(name="send", args=[
            SVArg(name="addr", dtype="int"),
            SVArg(name="data", dtype="int"),
        ], body_lines=["// body"])
        text = emitter.emit_one(td)
        assert "task send(input int addr, input int data);" in text

    def test_virtual_task(self, emitter):
        td = SVTaskDecl(name="body", is_virtual=True, body_lines=[
            "// default impl",
        ])
        text = emitter.emit_one(td)
        assert "virtual task body();" in text

    def test_pure_virtual_task(self, emitter):
        td = SVTaskDecl(name="execute", is_pure=True)
        text = emitter.emit_one(td)
        assert "pure virtual task execute();" in text
        # pure virtual should not have endtask
        assert "endtask" not in text

    def test_task_output_arg(self, emitter):
        td = SVTaskDecl(name="read", args=[
            SVArg(name="addr", dtype="int"),
            SVArg(name="data", dtype="int", direction="output"),
        ], body_lines=["data = mem[addr];"])
        text = emitter.emit_one(td)
        assert "output int data" in text


# ------------------------------------------------------------------ #
# SVFunctionDecl
# ------------------------------------------------------------------ #

class TestSVFunctionDecl:
    def test_void_function(self, emitter):
        fd = SVFunctionDecl(name="init", body_lines=["count = 0;"])
        text = emitter.emit_one(fd)
        assert "function void init();" in text
        assert "count = 0;" in text
        assert "endfunction" in text

    def test_function_with_return(self, emitter):
        fd = SVFunctionDecl(name="get_count", return_type="int",
                            body_lines=["return count;"])
        text = emitter.emit_one(fd)
        assert "function int get_count();" in text

    def test_pure_virtual_function(self, emitter):
        fd = SVFunctionDecl(name="get_name", return_type="string", is_pure=True)
        text = emitter.emit_one(fd)
        assert "pure virtual function string get_name();" in text
        assert "endfunction" not in text

    def test_virtual_function(self, emitter):
        fd = SVFunctionDecl(name="calc", return_type="int",
                            is_virtual=True, body_lines=["return 0;"])
        text = emitter.emit_one(fd)
        assert "virtual function int calc();" in text

    def test_constructor(self, emitter):
        fd = SVFunctionDecl(name="new",
                            args=[SVArg(name="name", dtype="string")],
                            return_type="void",
                            body_lines=["super.new(name);"])
        text = emitter.emit_one(fd)
        assert "function void new(input string name);" in text
        assert "super.new(name);" in text


# ------------------------------------------------------------------ #
# SVClass (full)
# ------------------------------------------------------------------ #

class TestSVClass:
    def test_simple_class_with_rand_fields(self, emitter):
        cls = SVClass(
            name="Packet",
            fields=[
                SVClassField(name="addr", dtype="bit [31:0]", is_rand=True),
                SVClassField(name="data", dtype="bit [63:0]", is_rand=True),
                SVClassField(name="tag", dtype="int"),
            ],
        )
        text = emitter.emit_one(cls)
        assert "class Packet;" in text
        assert "rand bit [31:0] addr;" in text
        assert "rand bit [63:0] data;" in text
        assert "int tag;" in text
        assert "endclass" in text

    def test_class_with_constraints(self, emitter):
        cls = SVClass(
            name="AlignedPacket",
            fields=[
                SVClassField(name="addr", dtype="bit [31:0]", is_rand=True),
            ],
            constraints=[
                SVConstraintBlock(name="align_c", exprs=["addr[1:0] == 2'b0"]),
                SVConstraintBlock(name="range_c", exprs=[
                    "addr >= 32'h1000",
                    "addr <= 32'hFFFF",
                ]),
            ],
        )
        text = emitter.emit_one(cls)
        assert "constraint align_c {" in text
        assert "constraint range_c {" in text

    def test_virtual_class_with_pure_virtual_task(self, emitter):
        cls = SVClass(
            name="zsp_import_if",
            is_virtual=True,
            tasks=[
                SVTaskDecl(name="dma_transfer", is_pure=True, args=[
                    SVArg(name="src", dtype="int"),
                    SVArg(name="dst", dtype="int"),
                    SVArg(name="len", dtype="int"),
                ]),
            ],
        )
        text = emitter.emit_one(cls)
        assert "virtual class zsp_import_if;" in text
        assert "pure virtual task dma_transfer" in text

    def test_class_extends(self, emitter):
        cls = SVClass(
            name="my_action_c",
            extends_name="zsp_action",
            fields=[
                SVClassField(name="comp", dtype="my_comp_c"),
            ],
        )
        text = emitter.emit_one(cls)
        assert "class my_action_c extends zsp_action;" in text

    def test_class_with_functions_and_tasks(self, emitter):
        cls = SVClass(
            name="DmaAction",
            extends_name="zsp_action",
            fields=[
                SVClassField(name="src_addr", dtype="bit [31:0]", is_rand=True),
            ],
            functions=[
                SVFunctionDecl(name="new", return_type="void",
                               body_lines=["super.new();"]),
            ],
            tasks=[
                SVTaskDecl(name="body", is_virtual=True,
                           body_lines=["comp.do_dma(src_addr);"]),
            ],
        )
        text = emitter.emit_one(cls)
        assert "function void new();" in text
        assert "virtual task body();" in text
        assert "endclass" in text

    def test_class_with_forward_decls(self, emitter):
        cls = SVClass(
            name="Producer",
            forward_decls=["Consumer"],
            fields=[
                SVClassField(name="consumer_ref", dtype="Consumer"),
            ],
        )
        text = emitter.emit_one(cls)
        lines = text.split("\n")
        # Forward decl should come before class definition
        fwd_idx = next(i for i, l in enumerate(lines) if "typedef class Consumer;" in l)
        cls_idx = next(i for i, l in enumerate(lines) if "class Producer;" in l)
        assert fwd_idx < cls_idx

    def test_class_with_raw_items(self, emitter):
        cls = SVClass(
            name="Mixed",
            items=[SVRawItem(lines=["// custom code"])],
        )
        text = emitter.emit_one(cls)
        assert "// custom code" in text


# ------------------------------------------------------------------ #
# SVImportDPI
# ------------------------------------------------------------------ #

class TestSVImportDPI:
    def test_dpi_function(self, emitter):
        dpi = SVImportDPI(
            name="zsp_dpi_solve",
            return_type="int",
            args=[SVArg(name="problem_id", dtype="int")],
        )
        text = emitter.emit_one(dpi)
        assert text == 'import "DPI-C" function int zsp_dpi_solve(input int problem_id);'

    def test_dpi_void_function(self, emitter):
        dpi = SVImportDPI(
            name="zsp_dpi_init",
            return_type="void",
        )
        text = emitter.emit_one(dpi)
        assert text == 'import "DPI-C" function void zsp_dpi_init();'

    def test_dpi_task(self, emitter):
        dpi = SVImportDPI(
            func_or_task="task",
            name="zsp_dpi_wait",
            args=[SVArg(name="cycles", dtype="int")],
        )
        text = emitter.emit_one(dpi)
        assert text == 'import "DPI-C" task zsp_dpi_wait(input int cycles);'

    def test_dpi_multiple_args(self, emitter):
        dpi = SVImportDPI(
            name="zsp_dpi_create",
            return_type="int",
            args=[
                SVArg(name="type_id", dtype="int"),
                SVArg(name="n_vars", dtype="int"),
                SVArg(name="handle", dtype="int", direction="output"),
            ],
        )
        text = emitter.emit_one(dpi)
        assert "input int type_id" in text
        assert "output int handle" in text


# ------------------------------------------------------------------ #
# SVModuleDecl
# ------------------------------------------------------------------ #

class TestSVModuleDecl:
    def test_simple_module(self, emitter):
        mod = SVModuleDecl(name="zsp_test_top", body_lines=[
            "initial begin",
            "  $display(\"test\");",
            "  $finish;",
            "end",
        ])
        text = emitter.emit_one(mod)
        assert "module zsp_test_top;" in text
        assert "endmodule" in text
        assert "$display(\"test\");" in text

    def test_empty_module(self, emitter):
        mod = SVModuleDecl(name="empty_mod")
        text = emitter.emit_one(mod)
        assert text == "module empty_mod;\nendmodule"


# ------------------------------------------------------------------ #
# SVLineDirective
# ------------------------------------------------------------------ #

class TestSVLineDirective:
    def test_line_directive(self, emitter):
        ld = SVLineDirective(filename="test.pss", lineno=42)
        text = emitter.emit_one(ld)
        assert text == '`line 42 "test.pss" 0'

    def test_line_directive_with_path(self, emitter):
        ld = SVLineDirective(filename="/path/to/model.pss", lineno=1)
        text = emitter.emit_one(ld)
        assert '"/path/to/model.pss"' in text


# ------------------------------------------------------------------ #
# SVForwardDecl
# ------------------------------------------------------------------ #

class TestSVForwardDecl:
    def test_forward_decl(self, emitter):
        fd = SVForwardDecl(class_name="my_class")
        text = emitter.emit_one(fd)
        assert text == "typedef class my_class;"


# ------------------------------------------------------------------ #
# SVField extensions (is_rand/is_randc on struct field)
# ------------------------------------------------------------------ #

class TestSVFieldExtensions:
    def test_svfield_rand_ignored_in_struct(self, emitter):
        """SVField.is_rand does not affect struct emission."""
        td = SVTypedefStruct(
            name="my_struct_t",
            fields=[SVField(name="x", width=8, is_rand=True)],
        )
        text = emitter.emit_one(td)
        # Struct fields don't get rand qualifier
        assert "rand" not in text
        assert "logic [7:0] x;" in text


# ------------------------------------------------------------------ #
# Package containing new node types
# ------------------------------------------------------------------ #

class TestSVPackageWithNewNodes:
    def test_package_with_class_and_dpi(self, emitter):
        pkg = SVPackage(
            name="zsp_pkg",
            items=[
                SVForwardDecl(class_name="action_a"),
                SVImportDPI(name="dpi_func", return_type="int"),
                SVClass(
                    name="action_a",
                    extends_name="zsp_action",
                    fields=[
                        SVClassField(name="val", dtype="int", is_rand=True),
                    ],
                ),
            ],
        )
        text = emitter.emit_one(pkg)
        assert "package zsp_pkg;" in text
        assert "typedef class action_a;" in text
        assert 'import "DPI-C"' in text
        assert "class action_a extends zsp_action;" in text
        assert "endpackage : zsp_pkg" in text

    def test_package_with_module(self, emitter):
        pkg = SVPackage(
            name="test_pkg",
            items=[
                SVTypedefEnum(name="mode_e", members=[("READ", 0), ("WRITE", 1)]),
            ],
        )
        text = emitter.emit_one(pkg)
        assert "typedef enum" in text
        assert "READ" in text


# ------------------------------------------------------------------ #
# Line directives interspersed with class body
# ------------------------------------------------------------------ #

class TestLineDirectivesInContext:
    def test_line_directives_in_emit_all(self, emitter):
        constructs = [
            SVLineDirective(filename="model.pss", lineno=10),
            SVClass(
                name="action_a",
                fields=[SVClassField(name="x", dtype="int", is_rand=True)],
            ),
            SVLineDirective(filename="model.pss", lineno=20),
            SVClass(
                name="action_b",
                fields=[SVClassField(name="y", dtype="int", is_rand=True)],
            ),
        ]
        text = emitter.emit_all(constructs)
        lines = text.split("\n")
        # Verify ordering: line directive before each class
        line_10_idx = next(i for i, l in enumerate(lines) if '`line 10' in l)
        class_a_idx = next(i for i, l in enumerate(lines) if 'class action_a' in l)
        line_20_idx = next(i for i, l in enumerate(lines) if '`line 20' in l)
        class_b_idx = next(i for i, l in enumerate(lines) if 'class action_b' in l)
        assert line_10_idx < class_a_idx
        assert line_20_idx < class_b_idx


# ------------------------------------------------------------------ #
# emit_all integration
# ------------------------------------------------------------------ #

class TestEmitAll:
    def test_mixed_constructs(self, emitter):
        """emit_all handles a mix of old and new IR node types."""
        constructs = [
            SVTypedefEnum(name="cmd_e", members=[("RD", 0), ("WR", 1)]),
            SVTypedefStruct(name="header_t", fields=[
                SVField(name="cmd", dtype="cmd_e"),
                SVField(name="len", width=8),
            ]),
            SVForwardDecl(class_name="my_class"),
            SVClass(
                name="my_class",
                fields=[SVClassField(name="hdr", dtype="header_t")],
            ),
            SVModuleDecl(name="top", body_lines=["initial $finish;"]),
        ]
        text = emitter.emit_all(constructs)
        assert "typedef enum" in text
        assert "typedef struct packed" in text
        assert "typedef class my_class;" in text
        assert "class my_class;" in text
        assert "module top;" in text

#****************************************************************************
# Copyright 2019-2025 Matthew Ballance and contributors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#****************************************************************************
"""SystemVerilog code generator for transforming datamodel to SystemVerilog."""
from pathlib import Path
from typing import List, Dict, Any
from zuspec.dataclasses import dm


class SVGenerator:
    """Main SystemVerilog code generator from datamodel."""

    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _sanitize_sv_name(self, name: str) -> str:
        """Sanitize a name to be a valid SystemVerilog identifier.
        
        Replaces dots, angle brackets, and other invalid characters with underscores.
        E.g., 'test_smoke.<locals>.Counter' -> 'test_smoke__locals__Counter'
        """
        import re
        # Replace dots and angle brackets with double underscores
        name = name.replace('.', '__')
        name = name.replace('<', '__')
        name = name.replace('>', '__')
        # Replace any other invalid characters with single underscore
        name = re.sub(r'[^a-zA-Z0-9_]', '_', name)
        # Ensure it doesn't start with a digit
        if name and name[0].isdigit():
            name = '_' + name
        return name

    def generate(self, ctxt: dm.Context) -> List[Path]:
        """Generate SystemVerilog code for all components in context."""
        files = []
        
        for name, dtype in ctxt.type_m.items():
            if isinstance(dtype, dm.DataTypeComponent):
                sv_code = self._generate_component(dtype)
                # Sanitize name for file (replace invalid chars with underscores)
                sv_name = self._sanitize_sv_name(name)
                output_file = self.output_dir / f"{sv_name}.sv"
                output_file.write_text(sv_code)
                files.append(output_file)
        
        return files

    def _generate_component(self, comp: dm.DataTypeComponent) -> str:
        """Generate SystemVerilog code for a component."""
        lines = []
        
        # Sanitize module name for SystemVerilog
        module_name = self._sanitize_sv_name(comp.name)
        
        # Module declaration
        lines.append(f"module {module_name}(")
        
        # Generate port list
        ports = []
        for field in comp.fields:
            if isinstance(field, dm.FieldInOut):
                port_dir = "output" if field.is_out else "input"
                port_type = self._get_sv_type(field.datatype)
                ports.append(f"  {port_dir} {port_type} {field.name}")
        
        lines.append(",\n".join(ports))
        lines.append(");")
        lines.append("")
        
        # Generate internal register declarations for non-InOut fields
        internal_fields = [f for f in comp.fields if not isinstance(f, dm.FieldInOut)]
        for field in internal_fields:
            if isinstance(field.datatype, dm.DataTypeInt):
                if field.datatype.bits == 1 or field.datatype.bits == -1:
                    lines.append(f"  reg {field.name};")
                else:
                    lines.append(f"  reg [{field.datatype.bits-1}:0] {field.name};")
            else:
                lines.append(f"  reg {field.name};")
        
        if internal_fields:
            lines.append("")
        
        # Generate sync processes (always blocks)
        for func in comp.sync_processes:
            lines.extend(self._generate_sync_process(func, comp))
        
        lines.append("endmodule")
        lines.append("")
        
        return "\n".join(lines)

    def _get_sv_type(self, dtype: dm.DataType) -> str:
        """Convert datamodel type to SystemVerilog type."""
        if isinstance(dtype, dm.DataTypeInt):
            if dtype.bits == 1 or dtype.bits == -1:
                return ""
            else:
                return f"reg[{dtype.bits-1}:0]"
        return ""

    def _generate_sync_process(self, func: dm.Function, comp: dm.DataTypeComponent) -> List[str]:
        """Generate SystemVerilog always block from sync process."""
        lines = []
        
        # Extract clock and reset from metadata
        clock_name = None
        reset_name = None
        if 'clock' in func.metadata:
            clock_expr = func.metadata['clock']
            if isinstance(clock_expr, dm.ExprRefField):
                clock_name = comp.fields[clock_expr.index].name
        if 'reset' in func.metadata:
            reset_expr = func.metadata['reset']
            if isinstance(reset_expr, dm.ExprRefField):
                reset_name = comp.fields[reset_expr.index].name
        
        # Generate always block sensitivity list
        sensitivity = []
        if clock_name:
            sensitivity.append(f"posedge {clock_name}")
        if reset_name:
            sensitivity.append(f"posedge {reset_name}")
        
        lines.append(f"  always @({' or '.join(sensitivity)}) begin")
        
        # Generate body
        for stmt in func.body:
            lines.extend(self._generate_stmt(stmt, comp, indent=2))
        
        lines.append("  end")
        lines.append("")
        
        return lines

    def _generate_stmt(self, stmt: dm.Stmt, comp: dm.DataTypeComponent, indent: int = 0) -> List[str]:
        """Generate SystemVerilog statement."""
        ind = "  " * indent
        lines = []
        
        if isinstance(stmt, dm.StmtIf):
            test_expr = self._generate_expr(stmt.test, comp)
            lines.append(f"{ind}if ({test_expr}) begin")
            for s in stmt.body:
                lines.extend(self._generate_stmt(s, comp, indent + 1))
            if stmt.orelse:
                lines.append(f"{ind}end else begin")
                for s in stmt.orelse:
                    lines.extend(self._generate_stmt(s, comp, indent + 1))
            lines.append(f"{ind}end")
        
        elif isinstance(stmt, dm.StmtAssign):
            for target in stmt.targets:
                target_expr = self._generate_expr(target, comp)
                value_expr = self._generate_expr(stmt.value, comp)
                lines.append(f"{ind}{target_expr} <= {value_expr};")
        
        elif isinstance(stmt, dm.StmtAugAssign):
            target_expr = self._generate_expr(stmt.target, comp)
            value_expr = self._generate_expr(stmt.value, comp)
            op = self._get_sv_op(stmt.op)
            lines.append(f"{ind}{target_expr} <= {target_expr} {op} {value_expr};")
        
        return lines

    def _generate_expr(self, expr: dm.Expr, comp: dm.DataTypeComponent) -> str:
        """Generate SystemVerilog expression."""
        if isinstance(expr, dm.ExprRefField):
            # Look up field by index
            if expr.index < len(comp.fields):
                return comp.fields[expr.index].name
            return f"field_{expr.index}"
        
        elif isinstance(expr, dm.ExprConstant):
            value = expr.value
            if isinstance(value, int):
                # Determine bit width if possible
                return str(value)
            return str(value)
        
        elif isinstance(expr, dm.ExprAttribute):
            obj = self._generate_expr(expr.value, comp)
            return expr.attr
        
        elif isinstance(expr, dm.ExprBin):
            left = self._generate_expr(expr.lhs, comp)
            right = self._generate_expr(expr.rhs, comp)
            op = self._get_sv_binop(expr.op)
            return f"{left} {op} {right}"
        
        elif isinstance(expr, dm.ExprCompare):
            # Handle comparison expressions
            result = self._generate_expr(expr.left, comp)
            for i, (op, comparator) in enumerate(zip(expr.ops, expr.comparators)):
                cmp_op = self._get_sv_cmpop(op)
                cmp_expr = self._generate_expr(comparator, comp)
                result = f"{result} {cmp_op} {cmp_expr}"
            return result
        
        return "/* unknown expr */"

    def _get_sv_binop(self, op: dm.BinOp) -> str:
        """Convert binary operator to SystemVerilog."""
        op_map = {
            dm.BinOp.Add: "+",
            dm.BinOp.Sub: "-",
            dm.BinOp.Mult: "*",
            dm.BinOp.Div: "/",
            dm.BinOp.Mod: "%",
            dm.BinOp.LShift: "<<",
            dm.BinOp.RShift: ">>",
            dm.BinOp.BitOr: "|",
            dm.BinOp.BitXor: "^",
            dm.BinOp.BitAnd: "&",
        }
        return op_map.get(op, "?")

    def _get_sv_op(self, op: dm.AugOp) -> str:
        """Convert augmented assignment operator to SystemVerilog."""
        op_map = {
            dm.AugOp.Add: "+",
            dm.AugOp.Sub: "-",
            dm.AugOp.Mult: "*",
            dm.AugOp.Div: "/",
        }
        return op_map.get(op, "?")

    def _get_sv_cmpop(self, op: dm.CmpOp) -> str:
        """Convert comparison operator to SystemVerilog."""
        op_map = {
            dm.CmpOp.Eq: "==",
            dm.CmpOp.NotEq: "!=",
            dm.CmpOp.Lt: "<",
            dm.CmpOp.LtE: "<=",
            dm.CmpOp.Gt: ">",
            dm.CmpOp.GtE: ">=",
        }
        return op_map.get(op, "?")

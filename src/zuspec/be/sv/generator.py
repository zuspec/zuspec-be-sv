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
from zuspec.dataclasses import ir


class SVGenerator:
    """Main SystemVerilog code generator from datamodel."""

    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._ctxt: ir.Context = None

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

    def _get_flattened_bundle_fields(self, bundle_field_name: str, bundle_type: ir.DataType) -> List[tuple]:
        """Get flattened field declarations for a bundle.
        
        Returns list of (flattened_name, datatype) tuples.
        E.g., for bundle 'io' of type RV with fields 'ready', 'valid', returns:
        [('io_ready', DataTypeInt), ('io_valid', DataTypeInt), ...]
        """
        if not isinstance(bundle_type, ir.DataTypeRef):
            return []
        
        # Look up the referenced struct type
        struct_type = self._ctxt.type_m.get(bundle_type.ref_name)
        if not isinstance(struct_type, ir.DataTypeStruct):
            return []
        
        flattened = []
        for field in struct_type.fields:
            if isinstance(field.datatype, ir.DataTypeInt):
                flattened_name = f"{bundle_field_name}_{field.name}"
                flattened.append((flattened_name, field.datatype))
        
        return flattened

    def generate(self, ctxt: ir.Context) -> List[Path]:
        """Generate SystemVerilog code for all components in context."""
        self._ctxt = ctxt
        files = []
        
        for name, dtype in ctxt.type_m.items():
            if isinstance(dtype, ir.DataTypeExtern):
                continue
            if isinstance(dtype, ir.DataTypeComponent):
                sv_code = self._generate_component(dtype)
                # Sanitize name for file (replace invalid chars with underscores)
                sv_name = self._sanitize_sv_name(name)
                output_file = self.output_dir / f"{sv_name}.sv"
                output_file.write_text(sv_code)
                files.append(output_file)
        
        return files

    def _generate_component(self, comp: ir.DataTypeComponent) -> str:
        """Generate SystemVerilog code for a component."""
        all_code = []
        
        # First, generate interfaces for any export fields
        export_interfaces = self._generate_export_interfaces(comp)
        if export_interfaces:
            all_code.append(export_interfaces)
            all_code.append("")
        
        lines = []
        
        # Sanitize module name for SystemVerilog
        module_name = self._sanitize_sv_name(comp.name)
        
        # Module declaration
        lines.append(f"module {module_name}(")
        
        # Generate port list
        ports = []
        
        # Check if this component has exports (is a top-level XtorComponent)
        has_exports = any(f.kind == ir.FieldKind.Export for f in comp.fields)
        
        for field in comp.fields:
            if isinstance(field, ir.FieldInOut):
                port_dir = "output" if field.is_out else "input"
                port_type = self._get_sv_type(field.datatype)
                ports.append(f"  {port_dir} {port_type} {field.name}")
            elif not has_exports and isinstance(field.datatype, ir.DataTypeRef) and field.kind != ir.FieldKind.Export:
                # Only expose bundles as ports for non-XtorComponent (subcomponents)
                # XtorComponents keep bundles internal for interface access
                ref_type = self._ctxt.type_m.get(field.datatype.ref_name)
                if isinstance(ref_type, ir.DataTypeStruct):
                    # This is a bundle - expose as flattened ports with proper directions
                    # Get the struct type to determine field directions
                    for struct_field in ref_type.fields:
                        if isinstance(struct_field, ir.FieldInOut):
                            # Use the direction from the struct definition
                            port_dir = "output" if struct_field.is_out else "input"
                            flat_name = f"{field.name}_{struct_field.name}"
                            if struct_field.datatype.bits == 1 or struct_field.datatype.bits == -1:
                                type_str = "logic"
                            else:
                                type_str = f"logic [{struct_field.datatype.bits-1}:0]"
                            ports.append(f"  {port_dir} {type_str} {flat_name}")
        
        lines.append(",\n".join(ports))
        lines.append(");")
        lines.append("")
        
        # Internal signal declarations
        for field in comp.fields:
            if isinstance(field, ir.FieldInOut):
                continue
            # Skip export fields - they become interface instances
            if field.kind == ir.FieldKind.Export:
                continue
            # For bundles: declare as internal if component has exports, skip otherwise (they're ports)
            if isinstance(field.datatype, ir.DataTypeRef):
                ref_type = self._ctxt.type_m.get(field.datatype.ref_name)
                if isinstance(ref_type, ir.DataTypeStruct):
                    # This is a bundle
                    if has_exports:
                        # XtorComponent: bundles are internal
                        flattened = self._get_flattened_bundle_fields(field.name, field.datatype)
                        for flat_name, flat_type in flattened:
                            if flat_type.bits == 1 or flat_type.bits == -1:
                                type_str = "logic"
                            else:
                                type_str = f"logic [{flat_type.bits-1}:0]"
                            lines.append(f"  {type_str} {flat_name};")
                    # else: bundles are ports, not internal
                # Non-bundle DataTypeRef (component instances) - skip
                continue
            if isinstance(field.datatype, ir.DataTypeInt):
                # Get the data type (logic, logic [N:0])
                if field.datatype.bits == 1 or field.datatype.bits == -1:
                    type_str = "logic"
                else:
                    type_str = f"logic [{field.datatype.bits-1}:0]"
                
                # In SystemVerilog, logic can be used directly without reg/wire keywords
                lines.append(f"  {type_str} {field.name};")
        if any((not isinstance(f, ir.FieldInOut) and isinstance(f.datatype, ir.DataTypeInt) and f.kind != ir.FieldKind.Export) for f in comp.fields):
            lines.append("")

        # Instantiate components (inst() fields)
        lines.extend(self._generate_component_instances(comp))
        if len(lines) and lines[-1] != "":
            lines.append("")

        # Instantiate extern components using bind map connections
        lines.extend(self._generate_extern_instances(comp))
        if len(lines) and lines[-1] != "":
            lines.append("")

        # Generate sync processes (always blocks)
        for func in comp.sync_processes:
            lines.extend(self._generate_sync_process(func, comp))
        
        # Add initial block to initialize internal signals written by tasks (for simulation)
        # Skip signals that will be driven by interface continuous assigns
        internal_signals = ['valid', 'data_i']  # Signals typically written by transactor task
        actual_signals = []
        
        # Collect signals driven by interface (to skip them)
        interface_driven_signals = set()
        for field in comp.fields:
            if field.kind == ir.FieldKind.Export:
                bound_methods = self._find_bound_methods(comp, field)
                if bound_methods:
                    for method in bound_methods:
                        for stmt in method.body:
                            self._collect_signal_refs(stmt, interface_driven_signals, comp)
                    # Check which signals are written by interface
                    for sig in list(interface_driven_signals):
                        if self._is_signal_written_in_methods(sig, bound_methods, comp):
                            # This signal is driven by interface assign, don't initialize
                            continue
        
        for field in comp.fields:
            if (not isinstance(field, ir.FieldInOut) and 
                isinstance(field.datatype, ir.DataTypeInt) and 
                field.kind != ir.FieldKind.Export and
                not isinstance(field.datatype, ir.DataTypeRef) and
                field.name in internal_signals and
                field.name not in interface_driven_signals):
                actual_signals.append(field.name)
        
        if actual_signals:
            lines.append("")
            lines.append("  // Initialize internal signals for simulation")
            lines.append("  initial begin")
            for sig_name in actual_signals:
                lines.append(f"    {sig_name} = 0;")
            lines.append("  end")
        
        # Instantiate export interfaces
        for field in comp.fields:
            if field.kind == ir.FieldKind.Export:
                interface_name = f"{module_name}_{field.name}"
                bound_methods = self._find_bound_methods(comp, field)
                
                if bound_methods:
                    lines.append(f"  // Instantiate interface for {field.name}")
                    lines.append(f"  {interface_name} {field.name}();")
                    lines.append("")
                    
                    # Collect signals needed by interface
                    needed_signals = set()
                    for method in bound_methods:
                        for stmt in method.body:
                            self._collect_signal_refs(stmt, needed_signals, comp)
                    
                    # Generate assign statements to connect module signals to interface signals
                    lines.append(f"  // Connect module signals to interface")
                    for signal_name in sorted(needed_signals):
                        # Determine if this signal is written by the interface task
                        signal_written_by_interface = self._is_signal_written_in_methods(signal_name, bound_methods, comp)
                        
                        if signal_written_by_interface:
                            # Interface writes, module reads
                            lines.append(f"  assign {signal_name} = {field.name}.{signal_name};")
                        else:
                            # Module writes, interface reads
                            lines.append(f"  assign {field.name}.{signal_name} = {signal_name};")
                    
                    lines.append("")
        
        lines.append("endmodule")
        lines.append("")
        
        all_code.append("\n".join(lines))
        return "\n".join(all_code)

    def _get_sv_type(self, dtype: ir.DataType) -> str:
        """Convert datamodel type to SystemVerilog type."""
        if isinstance(dtype, ir.DataTypeInt):
            if dtype.bits == 1 or dtype.bits == -1:
                return "logic"
            else:
                return f"logic [{dtype.bits-1}:0]"
        return ""

    def _get_sv_decl_type(self, dtype: ir.DataType) -> str:
        if isinstance(dtype, ir.DataTypeInt):
            if dtype.bits == 1 or dtype.bits == -1:
                return "logic"
            return f"logic [{dtype.bits-1}:0]"
        return "logic"

    def _generate_extern_instances(self, comp: ir.DataTypeComponent) -> List[str]:
        if self._ctxt is None:
            return []

        lines: List[str] = []

        for field in comp.fields:
            if isinstance(field, ir.FieldInOut):
                continue
            if not isinstance(field.datatype, ir.DataTypeRef):
                continue

            ref_t = self._ctxt.type_m.get(field.datatype.ref_name)
            if not isinstance(ref_t, ir.DataTypeExtern):
                continue

            inst_name = field.name
            mod_name = ref_t.extern_name if ref_t.extern_name else (ref_t.py_type.__name__ if ref_t.py_type is not None else inst_name)

            port_conn: Dict[str, str] = {}

            def match_subport(expr) -> Any:
                if not isinstance(expr, ir.ExprRefField):
                    return None
                if not isinstance(expr.base, ir.ExprRefField):
                    return None
                if not isinstance(expr.base.base, ir.TypeExprRefSelf):
                    return None
                inst_idx = expr.base.index
                port_idx = expr.index
                if inst_idx >= len(comp.fields):
                    return None
                if comp.fields[inst_idx].name != inst_name:
                    return None
                if port_idx >= len(ref_t.fields):
                    return None
                return ref_t.fields[port_idx].name

            for b in comp.bind_map:
                lhs_p = match_subport(b.lhs)
                rhs_p = match_subport(b.rhs)

                if lhs_p is not None and rhs_p is None:
                    port_conn[lhs_p] = self._generate_expr(b.rhs, comp)
                elif rhs_p is not None and lhs_p is None:
                    port_conn[rhs_p] = self._generate_expr(b.lhs, comp)

            if not port_conn:
                continue

            lines.append(f"  {mod_name} {inst_name} (")
            conns = []
            for pname, pexpr in port_conn.items():
                conns.append(f"    .{pname}({pexpr})")
            lines.append(",\n".join(conns))
            lines.append("  );")
            lines.append("")

        return lines
    
    def _generate_component_instances(self, comp: ir.DataTypeComponent) -> List[str]:
        """Generate component instances for inst() fields."""
        if self._ctxt is None:
            return []

        lines: List[str] = []

        for field in comp.fields:
            if isinstance(field, ir.FieldInOut):
                continue
            if not isinstance(field.datatype, ir.DataTypeRef):
                continue

            ref_t = self._ctxt.type_m.get(field.datatype.ref_name)
            if not isinstance(ref_t, ir.DataTypeComponent):
                continue
            
            # Don't instantiate extern types here (handled separately)
            if isinstance(ref_t, ir.DataTypeExtern):
                continue

            inst_name = field.name
            mod_name = self._sanitize_sv_name(ref_t.name)

            port_conn: Dict[str, str] = {}

            def match_subport(expr) -> Any:
                if not isinstance(expr, ir.ExprRefField):
                    return None
                if not isinstance(expr.base, ir.ExprRefField):
                    return None
                if not isinstance(expr.base.base, ir.TypeExprRefSelf):
                    return None
                inst_idx = expr.base.index
                port_idx = expr.index
                if inst_idx >= len(comp.fields):
                    return None
                if comp.fields[inst_idx].name != inst_name:
                    return None
                if port_idx >= len(ref_t.fields):
                    return None
                return ref_t.fields[port_idx].name

            for b in comp.bind_map:
                lhs_p = match_subport(b.lhs)
                rhs_p = match_subport(b.rhs)

                # Check if this is a bundle-to-bundle connection
                # The port is on whichever side matched (lhs_p or rhs_p)
                port_name = lhs_p if lhs_p is not None else rhs_p
                if port_name is not None:
                    inst_port_field = None
                    for f in ref_t.fields:
                        if f.name == port_name:
                            inst_port_field = f
                            break
                    
                    if inst_port_field and isinstance(inst_port_field.datatype, ir.DataTypeRef):
                        # This is a bundle port - need to flatten connections
                        bundle_type = self._ctxt.type_m.get(inst_port_field.datatype.ref_name)
                        if isinstance(bundle_type, ir.DataTypeStruct):
                            # Get the RHS or LHS expression (whichever is NOT the subport)
                            conn_expr = b.lhs if lhs_p is None else b.rhs
                            conn_base = self._generate_expr(conn_expr, comp)
                            # Expand into individual signal connections
                            for struct_field in bundle_type.fields:
                                flat_port = f"{port_name}_{struct_field.name}"
                                flat_conn = f"{conn_base}_{struct_field.name}"
                                port_conn[flat_port] = flat_conn
                            continue  # Skip normal connection
                
                # Normal (non-bundle) connections
                if lhs_p is not None and rhs_p is None:
                    port_conn[lhs_p] = self._generate_expr(b.rhs, comp)
                elif rhs_p is not None and lhs_p is None:
                    port_conn[rhs_p] = self._generate_expr(b.lhs, comp)

            if not port_conn:
                continue

            lines.append(f"  {mod_name} {inst_name} (")
            conns = []
            for pname, pexpr in port_conn.items():
                conns.append(f"    .{pname}({pexpr})")
            lines.append(",\n".join(conns))
            lines.append("  );")

        return lines

    def _generate_sync_process(self, func: ir.Function, comp: ir.DataTypeComponent) -> List[str]:
        """Generate SystemVerilog always block from sync process."""
        lines = []
        
        # Extract clock and reset from metadata
        clock_name = None
        reset_name = None
        if 'clock' in func.metadata:
            clock_expr = func.metadata['clock']
            if isinstance(clock_expr, ir.ExprRefField):
                clock_name = comp.fields[clock_expr.index].name
        if 'reset' in func.metadata:
            reset_expr = func.metadata['reset']
            if isinstance(reset_expr, ir.ExprRefField):
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

    def _generate_stmt(self, stmt: ir.Stmt, comp: ir.DataTypeComponent, indent: int = 0) -> List[str]:
        """Generate SystemVerilog statement."""
        ind = "  " * indent
        lines = []
        
        if isinstance(stmt, ir.StmtIf):
            test_expr = self._generate_expr(stmt.test, comp)
            lines.append(f"{ind}if ({test_expr}) begin")
            for s in stmt.body:
                lines.extend(self._generate_stmt(s, comp, indent + 1))
            if stmt.orelse:
                lines.append(f"{ind}end else begin")
                for s in stmt.orelse:
                    lines.extend(self._generate_stmt(s, comp, indent + 1))
            lines.append(f"{ind}end")
        
        elif isinstance(stmt, ir.StmtAssign):
            for target in stmt.targets:
                target_expr = self._generate_expr(target, comp)
                value_expr = self._generate_expr(stmt.value, comp)
                lines.append(f"{ind}{target_expr} <= {value_expr};")
        
        elif isinstance(stmt, ir.StmtAugAssign):
            target_expr = self._generate_expr(stmt.target, comp)
            value_expr = self._generate_expr(stmt.value, comp)
            op = self._get_sv_op(stmt.op)
            lines.append(f"{ind}{target_expr} <= {target_expr} {op} {value_expr};")
        
        return lines

    def _generate_expr(self, expr: ir.Expr, comp: ir.DataTypeComponent) -> str:
        """Generate SystemVerilog expression."""
        if isinstance(expr, ir.ExprRefField):
            # Look up field by index
            if expr.index < len(comp.fields):
                return comp.fields[expr.index].name
            return f"field_{expr.index}"
        
        elif isinstance(expr, ir.ExprConstant):
            value = expr.value
            if isinstance(value, int):
                # Determine bit width if possible
                return str(value)
            return str(value)
        
        elif isinstance(expr, ir.ExprAttribute):
            # Handle bundle field access: flatten to bundle_field
            obj = self._generate_expr(expr.value, comp)
            return f"{obj}_{expr.attr}"
        
        elif isinstance(expr, ir.ExprBin):
            left = self._generate_expr(expr.lhs, comp)
            right = self._generate_expr(expr.rhs, comp)
            op = self._get_sv_binop(expr.op)
            return f"{left} {op} {right}"
        
        elif isinstance(expr, ir.ExprCompare):
            # Handle comparison expressions
            result = self._generate_expr(expr.left, comp)
            for i, (op, comparator) in enumerate(zip(expr.ops, expr.comparators)):
                cmp_op = self._get_sv_cmpop(op)
                cmp_expr = self._generate_expr(comparator, comp)
                result = f"{result} {cmp_op} {cmp_expr}"
            return result
        
        elif isinstance(expr, ir.ExprBool):
            # Handle boolean expressions (and, or)
            op = self._get_sv_boolop(expr.op)
            operands = [self._generate_expr(v, comp) for v in expr.values]
            return f" {op} ".join(operands)
        
        elif isinstance(expr, ir.ExprUnary):
            # Handle unary expressions (not, -, +, ~)
            op = self._get_sv_unaryop(expr.op)
            operand = self._generate_expr(expr.operand, comp)
            return f"{op}{operand}"
        
        elif isinstance(expr, ir.ExprRefParam):
            # Parameter reference
            return expr.name
        
        elif isinstance(expr, ir.ExprRefLocal):
            # Local variable reference
            return expr.name
        
        return "/* unknown expr */"

    def _get_sv_binop(self, op: ir.BinOp) -> str:
        """Convert binary operator to SystemVerilog."""
        op_map = {
            ir.BinOp.Add: "+",
            ir.BinOp.Sub: "-",
            ir.BinOp.Mult: "*",
            ir.BinOp.Div: "/",
            ir.BinOp.Mod: "%",
            ir.BinOp.LShift: "<<",
            ir.BinOp.RShift: ">>",
            ir.BinOp.BitOr: "|",
            ir.BinOp.BitXor: "^",
            ir.BinOp.BitAnd: "&",
        }
        return op_map.get(op, "?")

    def _get_sv_op(self, op: ir.AugOp) -> str:
        """Convert augmented assignment operator to SystemVerilog."""
        op_map = {
            ir.AugOp.Add: "+",
            ir.AugOp.Sub: "-",
            ir.AugOp.Mult: "*",
            ir.AugOp.Div: "/",
        }
        return op_map.get(op, "?")

    def _get_sv_cmpop(self, op: ir.CmpOp) -> str:
        """Convert comparison operator to SystemVerilog."""
        op_map = {
            ir.CmpOp.Eq: "==",
            ir.CmpOp.NotEq: "!=",
            ir.CmpOp.Lt: "<",
            ir.CmpOp.LtE: "<=",
            ir.CmpOp.Gt: ">",
            ir.CmpOp.GtE: ">=",
        }
        return op_map.get(op, "?")

    def _get_sv_boolop(self, op: ir.BoolOp) -> str:
        """Convert boolean operator to SystemVerilog."""
        op_map = {
            ir.BoolOp.And: "&&",
            ir.BoolOp.Or: "||",
        }
        return op_map.get(op, "?")
    
    def _get_sv_unaryop(self, op: ir.UnaryOp) -> str:
        """Convert unary operator to SystemVerilog."""
        op_map = {
            ir.UnaryOp.Not: "!",
            ir.UnaryOp.Invert: "~",
            ir.UnaryOp.UAdd: "+",
            ir.UnaryOp.USub: "-",
        }
        return op_map.get(op, "?")
    
    def _is_field_wire(self, comp: ir.DataTypeComponent, field: ir.Field) -> bool:
        """Determine if a field should be declared as wire (driven by subcomponent output)."""
        # Check if this field is on the RHS of a binding where LHS is a subcomponent output
        field_idx = -1
        for idx, f in enumerate(comp.fields):
            if f == field:
                field_idx = idx
                break
        
        if field_idx == -1:
            return False
        
        for bind in comp.bind_map:
            # Check if RHS is this field
            if isinstance(bind.rhs, ir.ExprRefField):
                if isinstance(bind.rhs.base, ir.TypeExprRefSelf) and bind.rhs.index == field_idx:
                    # Check if LHS is a subcomponent field
                    if isinstance(bind.lhs, ir.ExprRefField):
                        if isinstance(bind.lhs.base, ir.ExprRefField):
                            # This is self.sub.port format
                            # Assume if bound from subcomponent, it's driven by that subcomponent
                            return True
        
        return False

    def _generate_export_interfaces(self, comp: ir.DataTypeComponent) -> str:
        """Generate SystemVerilog interfaces for export fields.
        
        Interfaces are declared outside the module with local signals.
        The module will use assign statements to connect signals bidirectionally.
        """
        interfaces = []
        
        module_name = self._sanitize_sv_name(comp.name)
        
        for field in comp.fields:
            if field.kind != ir.FieldKind.Export:
                continue
            
            # Find methods bound to this export field
            bound_methods = self._find_bound_methods(comp, field)
            
            if not bound_methods:
                continue
            
            # Collect all signals that need to be accessed
            needed_signals = set()
            for method in bound_methods:
                for stmt in method.body:
                    self._collect_signal_refs(stmt, needed_signals, comp)
            
            # Generate interface declaration with local signal declarations
            interface_name = f"{module_name}_{field.name}"
            interface_lines = []
            
            interface_lines.append(f"interface {interface_name};")
            interface_lines.append("")
            
            # Determine which signals are written by interface tasks
            signals_written_by_interface = set()
            for sig in needed_signals:
                if self._is_signal_written_in_methods(sig, bound_methods, comp):
                    signals_written_by_interface.add(sig)
            
            # Declare local signals in the interface with inline initialization only for writable signals
            interface_lines.append("  // Local signals")
            for signal_name in sorted(needed_signals):
                # Find signal type
                signal_type = None
                for fld in comp.fields:
                    if fld.name == signal_name:
                        if isinstance(fld.datatype, ir.DataTypeInt):
                            signal_type = self._get_sv_type(fld.datatype)
                        break
                else:
                    # Check for flattened bundle signals
                    if '_' in signal_name:
                        parts = signal_name.split('_', 1)
                        bundle_name = parts[0]
                        for fld in comp.fields:
                            if fld.name == bundle_name and isinstance(fld.datatype, ir.DataTypeRef):
                                ref_type = self._ctxt.type_m.get(fld.datatype.ref_name)
                                if isinstance(ref_type, ir.DataTypeStruct):
                                    flattened = self._get_flattened_bundle_fields(bundle_name, fld.datatype)
                                    for flat_name, flat_type in flattened:
                                        if flat_name == signal_name:
                                            signal_type = self._get_sv_type(flat_type)
                                            break
                                break
                
                if signal_type:
                    # Only initialize signals that are written by interface (to avoid conflict with continuous assigns)
                    if signal_name in signals_written_by_interface:
                        interface_lines.append(f"  {signal_type} {signal_name} = 0;")
                    else:
                        interface_lines.append(f"  {signal_type} {signal_name};")
            
            interface_lines.append("")
            
            # Generate tasks for each bound method
            for method in bound_methods:
                interface_lines.extend(self._generate_interface_task(method, comp))
            
            interface_lines.append("endinterface")
            interfaces.append("\n".join(interface_lines))
        
        return "\n\n".join(interfaces)
    
    def _collect_interface_ports(self, comp: ir.DataTypeComponent, methods: List[ir.Function]) -> List[str]:
        """Collect signals that interface tasks need access to (clock, internal signals)."""
        ports = []
        needed_signals = set()
        
        # Always need clock for timing control
        for field in comp.fields:
            if field.name == "clock" and isinstance(field, ir.FieldInOut):
                ports.append("input logic clock")
                needed_signals.add("clock")
                break
        
        # Check what signals are referenced in method bodies
        for method in methods:
            for stmt in method.body:
                self._collect_signal_refs(stmt, needed_signals, comp)
        
        # Now add the needed signals as interface ports
        # Check if we have exports (XtorComponent) - if so, bundles are internal, use ref
        has_exports = any(f.kind == ir.FieldKind.Export for f in comp.fields)
        
        for signal_name in sorted(needed_signals):
            if signal_name == "clock":
                continue  # Already added
            
            # Find the field or flattened bundle field
            found = False
            
            # First check regular fields
            for field in comp.fields:
                if field.name == signal_name:
                    if isinstance(field, ir.FieldInOut):
                        # Module input/output port
                        port_dir = "input" if not field.is_out else "output"
                        port_type = self._get_sv_type(field.datatype)
                        ports.append(f"{port_dir} {port_type} {signal_name}")
                    elif isinstance(field.datatype, ir.DataTypeInt):
                        # Internal signal - use ref
                        if field.datatype.bits == 1 or field.datatype.bits == -1:
                            ports.append(f"ref logic {signal_name}")
                        else:
                            ports.append(f"ref logic [{field.datatype.bits-1}:0] {signal_name}")
                    found = True
                    break
            
            if not found:
                # Check if this is a flattened bundle signal (e.g., io_ready)
                # Signal names like io_ready come from bundle fields
                if '_' in signal_name:
                    # Try to find the bundle field this belongs to
                    parts = signal_name.split('_', 1)
                    bundle_name = parts[0]
                    for field in comp.fields:
                        if field.name == bundle_name and isinstance(field.datatype, ir.DataTypeRef):
                            ref_type = self._ctxt.type_m.get(field.datatype.ref_name)
                            if isinstance(ref_type, ir.DataTypeStruct):
                                # This is a flattened bundle signal - determine its type
                                flattened = self._get_flattened_bundle_fields(bundle_name, field.datatype)
                                for flat_name, flat_type in flattened:
                                    if flat_name == signal_name:
                                        if flat_type.bits == 1 or flat_type.bits == -1:
                                            ports.append(f"ref logic {signal_name}")
                                        else:
                                            ports.append(f"ref logic [{flat_type.bits-1}:0] {signal_name}")
                                        found = True
                                        break
                                if found:
                                    break
        
        return ports
    
    def _collect_signal_refs(self, stmt: ir.Stmt, signals: set, comp: ir.DataTypeComponent):
        """Recursively collect signal references from statement."""
        if isinstance(stmt, ir.StmtAssign):
            # Check targets and values
            for target in stmt.targets:
                self._collect_signal_refs_from_expr(target, signals, comp)
            self._collect_signal_refs_from_expr(stmt.value, signals, comp)
        
        elif isinstance(stmt, ir.StmtExpr):
            if isinstance(stmt.expr, ir.ExprAwait):
                # Check await expression for signal references
                if isinstance(stmt.expr.value, ir.ExprCall):
                    for arg in stmt.expr.value.args:
                        self._collect_signal_refs_from_expr(arg, signals, comp)
        
        elif isinstance(stmt, ir.StmtWhile):
            # Check test expression and body
            self._collect_signal_refs_from_expr(stmt.test, signals, comp)
            for s in stmt.body:
                self._collect_signal_refs(s, signals, comp)
    
    def _collect_signal_refs_from_expr(self, expr: ir.Expr, signals: set, comp: ir.DataTypeComponent):
        """Recursively collect signal references from expression."""
        if isinstance(expr, ir.ExprRefField):
            if isinstance(expr.base, ir.TypeExprRefSelf):
                if expr.index < len(comp.fields):
                    field = comp.fields[expr.index]
                    # Check if this is a bundle field - need to flatten
                    if isinstance(field.datatype, ir.DataTypeRef):
                        ref_type = self._ctxt.type_m.get(field.datatype.ref_name)
                        if isinstance(ref_type, ir.DataTypeStruct):
                            # This is a bundle reference - add all flattened signals
                            flattened = self._get_flattened_bundle_fields(field.name, field.datatype)
                            for flat_name, _ in flattened:
                                signals.add(flat_name)
                        else:
                            signals.add(field.name)
                    else:
                        signals.add(field.name)
        elif isinstance(expr, ir.ExprAttribute):
            # Bundle field access: self.io.ready → io_ready
            base_expr = expr.value
            if isinstance(base_expr, ir.ExprRefField) and isinstance(base_expr.base, ir.TypeExprRefSelf):
                if base_expr.index < len(comp.fields):
                    bundle_name = comp.fields[base_expr.index].name
                    flat_name = f"{bundle_name}_{expr.attr}"
                    signals.add(flat_name)
        elif isinstance(expr, ir.ExprUnary):
            self._collect_signal_refs_from_expr(expr.operand, signals, comp)
        elif isinstance(expr, ir.ExprBin):
            self._collect_signal_refs_from_expr(expr.lhs, signals, comp)
            self._collect_signal_refs_from_expr(expr.rhs, signals, comp)
        elif isinstance(expr, ir.ExprBool):
            for val in expr.values:
                self._collect_signal_refs_from_expr(val, signals, comp)
        elif isinstance(expr, ir.ExprCompare):
            self._collect_signal_refs_from_expr(expr.left, signals, comp)
            for comparator in expr.comparators:
                self._collect_signal_refs_from_expr(comparator, signals, comp)
    
    def _is_signal_written_in_methods(self, signal_name: str, methods: List[ir.Function], comp: ir.DataTypeComponent) -> bool:
        """Check if a signal is written to (assigned) in any of the methods."""
        for method in methods:
            if self._is_signal_written_in_stmts(signal_name, method.body, comp):
                return True
        return False
    
    def _is_signal_written_in_stmts(self, signal_name: str, stmts: List[ir.Stmt], comp: ir.DataTypeComponent) -> bool:
        """Check if a signal is written to in a list of statements."""
        for stmt in stmts:
            if isinstance(stmt, ir.StmtAssign):
                # Check if any target matches the signal
                for target in stmt.targets:
                    if self._expr_matches_signal(target, signal_name, comp):
                        return True
            elif isinstance(stmt, ir.StmtWhile):
                # Check body recursively
                if self._is_signal_written_in_stmts(signal_name, stmt.body, comp):
                    return True
            elif isinstance(stmt, ir.StmtIf):
                # Check both branches
                if self._is_signal_written_in_stmts(signal_name, stmt.body, comp):
                    return True
                if stmt.orelse and self._is_signal_written_in_stmts(signal_name, stmt.orelse, comp):
                    return True
        return False
    
    def _expr_matches_signal(self, expr: ir.Expr, signal_name: str, comp: ir.DataTypeComponent) -> bool:
        """Check if an expression refers to a specific signal."""
        if isinstance(expr, ir.ExprRefField):
            if isinstance(expr.base, ir.TypeExprRefSelf) and expr.index < len(comp.fields):
                return comp.fields[expr.index].name == signal_name
        elif isinstance(expr, ir.ExprAttribute):
            # Bundle field access: self.io.ready → io_ready
            base_expr = expr.value
            if isinstance(base_expr, ir.ExprRefField) and isinstance(base_expr.base, ir.TypeExprRefSelf):
                if base_expr.index < len(comp.fields):
                    bundle_name = comp.fields[base_expr.index].name
                    flat_name = f"{bundle_name}_{expr.attr}"
                    return flat_name == signal_name
        return False
    
    def _find_bound_methods(self, comp: ir.DataTypeComponent, export_field: ir.Field) -> List[ir.Function]:
        """Find methods bound to an export field via bind_map."""
        bound_methods = []
        
        # Look through bind_map for bindings to export field methods
        for bind in comp.bind_map:
            # Check if LHS is a method reference on the export field
            # Pattern: self.xtor_if.send -> self.send
            if isinstance(bind.lhs, ir.ExprRefPy):
                if isinstance(bind.lhs.base, ir.ExprRefField):
                    # Check if base refers to the export field
                    if bind.lhs.base.index < len(comp.fields):
                        if comp.fields[bind.lhs.base.index] == export_field:
                            # Find the method in functions list
                            method_name = bind.lhs.ref
                            for func in comp.functions:
                                if func.name == method_name:
                                    bound_methods.append(func)
                                    break
        
        return bound_methods
    
    def _generate_interface_task(self, func: ir.Function, comp: ir.DataTypeComponent) -> List[str]:
        """Generate a SystemVerilog task for an interface method."""
        lines = []
        
        # Task signature
        params = []
        
        # Return value as output parameter if function has return
        if func.returns and func.returns is not None:
            return_type = self._get_sv_type(func.returns)
            # TODO: Fix data_model_factory to properly extract return type width
            # For now, if return type is generic "logic" (bits=-1), assume 32-bit
            if return_type == "logic" and isinstance(func.returns, ir.DataTypeInt) and func.returns.bits == -1:
                return_type = "logic [31:0]"
            params.append(f"output {return_type} __ret")
        
        # Input parameters
        for arg in func.args.args:
            if hasattr(arg, 'annotation') and isinstance(arg.annotation, ir.ExprConstant):
                # annotation is a type class like int
                arg_type_class = arg.annotation.value
                if arg_type_class is int:
                    arg_type = "logic [31:0]"  # Default to 32-bit
                else:
                    arg_type = "logic [31:0]"
            else:
                arg_type = "logic [31:0]"
            params.append(f"input {arg_type} {arg.arg}")
        
        # Task declaration
        task_sig = f"  task {func.name}("
        if params:
            lines.append(task_sig)
            for i, param in enumerate(params):
                if i < len(params) - 1:
                    lines.append(f"    {param},")
                else:
                    lines.append(f"    {param});")
        else:
            lines.append(f"{task_sig});")
        
        # Task body - convert async/await to SV
        lines.extend(self._generate_task_body(func, comp, indent=2))
        
        lines.append("  endtask")
        lines.append("")
        
        return lines
    
    def _generate_task_body(self, func: ir.Function, comp: ir.DataTypeComponent, indent: int = 0) -> List[str]:
        """Generate task body, converting async/await to SystemVerilog."""
        lines = []
        ind = "  " * indent
        
        # Collect local variables used in the function
        local_vars = set()
        for stmt in func.body:
            self._collect_local_vars(stmt, local_vars)
        
        # Declare local variables
        for var_name in sorted(local_vars):
            lines.append(f"{ind}logic [31:0] {var_name};  // TODO: determine actual type")
        
        if local_vars:
            lines.append("")
        
        # Add debug entry message
        lines.append(f'{ind}$display("%0t: [{func.name}] Task started", $time);')
        
        for stmt in func.body:
            lines.extend(self._generate_task_stmt(stmt, comp, indent))
        
        # Add debug exit message
        lines.append(f'{ind}$display("%0t: [{func.name}] Task completed", $time);')
        
        return lines
    
    def _collect_local_vars(self, stmt: ir.Stmt, local_vars: set):
        """Collect local variable names from statements."""
        if isinstance(stmt, ir.StmtAssign):
            for target in stmt.targets:
                if isinstance(target, ir.ExprRefLocal):
                    local_vars.add(target.name)
        elif isinstance(stmt, ir.StmtWhile):
            for s in stmt.body:
                self._collect_local_vars(s, local_vars)
    
    def _generate_task_stmt(self, stmt: ir.Stmt, comp: ir.DataTypeComponent, indent: int = 0) -> List[str]:
        """Generate SystemVerilog statement for task body."""
        ind = "  " * indent
        lines = []
        
        if isinstance(stmt, ir.StmtExpr):
            # Expression statement - could be await
            if isinstance(stmt.expr, ir.ExprAwait):
                # await expression - convert to @(posedge ...)
                await_expr = stmt.expr.value
                if isinstance(await_expr, ir.ExprCall):
                    # Check if it's posedge(signal)
                    if hasattr(await_expr, 'func') and isinstance(await_expr.func, ir.ExprAttribute):
                        if await_expr.func.attr == 'posedge' and len(await_expr.args) > 0:
                            signal_expr = self._generate_expr(await_expr.args[0], comp)
                            lines.append(f"{ind}@(posedge {signal_expr});")
                else:
                    lines.append(f"{ind}/* TODO: await {await_expr} */")
        
        elif isinstance(stmt, ir.StmtWhile):
            # While loop
            test_expr = self._generate_expr(stmt.test, comp)
            lines.append(f'{ind}$display("%0t:   While loop checking: {test_expr}=%b", $time, {test_expr});')
            lines.append(f"{ind}while ({test_expr}) begin")
            lines.append(f'{ind}  $display("%0t:     Inside while loop, waiting...", $time);')
            for s in stmt.body:
                lines.extend(self._generate_task_stmt(s, comp, indent + 1))
            lines.append(f"{ind}end")
            lines.append(f'{ind}$display("%0t:   While loop exited", $time);')
        
        elif isinstance(stmt, ir.StmtAssign):
            # Assignment - use blocking assignment in tasks
            for target in stmt.targets:
                target_expr = self._generate_expr(target, comp)
                value_expr = self._generate_expr(stmt.value, comp)
                lines.append(f"{ind}{target_expr} = {value_expr};")
        
        elif isinstance(stmt, ir.StmtReturn):
            # Return statement - assign to __ret
            if stmt.value:
                value_expr = self._generate_expr(stmt.value, comp)
                lines.append(f"{ind}__ret = {value_expr};")
        
        else:
            # For other statements, try to use existing generator
            lines.extend(self._generate_stmt(stmt, comp, indent))
        
        return lines

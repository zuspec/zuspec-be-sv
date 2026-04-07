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
from typing import List, Dict, Any, Optional
from zuspec.dataclasses import ir


class SVGenerator:
    """Main SystemVerilog code generator from datamodel."""

    def __init__(self, output_dir: Path, debug_annotations: bool = False):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._ctxt: ir.Context = None
        self.debug_annotations = debug_annotations

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

    def _resolve_bundle_type(self, field: ir.Field, comp: ir.DataTypeComponent) -> Optional[ir.DataTypeStruct]:
        """Resolve a bundle field to its struct type definition.
        
        Tries to resolve from:
        1. Context type map (if bundle type was explicitly included)
        2. Component's Python module (lookup by ref_name)
        
        Returns None if not a bundle or can't be resolved.
        """
        if not isinstance(field.datatype, ir.DataTypeRef):
            return None
        
        # Try to get from context first
        ref_type = self._ctxt.type_m.get(field.datatype.ref_name)
        if isinstance(ref_type, ir.DataTypeStruct):
            return ref_type
        
        # Fallback: Try to find the bundle class and build its IR
        bundle_class = None
        
        # Try to get from component's module
        if comp.py_type:
            try:
                import importlib
                comp_module = importlib.import_module(comp.py_type.__module__)
                # Look for the bundle class in the module
                if hasattr(comp_module, field.datatype.ref_name):
                    bundle_class = getattr(comp_module, field.datatype.ref_name)
            except Exception:
                pass
        
        # If not found in component module, try importing from known protocol packages
        if not bundle_class:
            try:
                # Try common protocol packages
                for pkg in ['org.featherweight_ip.protocol.core.wishbone',
                           'org.featherweight_ip.protocol.core.axi']:
                    try:
                        import importlib
                        mod = importlib.import_module(pkg)
                        if hasattr(mod, field.datatype.ref_name):
                            bundle_class = getattr(mod, field.datatype.ref_name)
                            break
                    except ImportError:
                        continue
            except Exception:
                pass
        
        # If we found the bundle class, build its IR
        if bundle_class:
            try:
                import zuspec.dataclasses as zdc
                factory = zdc.DataModelFactory()
                bundle_ctxt = factory.build(bundle_class)
                # Look for the struct type in the built context
                for name, dtype in bundle_ctxt.type_m.items():
                    if isinstance(dtype, ir.DataTypeStruct):
                        return dtype
            except Exception:
                pass
        
        return None

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

    def _create_binding_map(self, comp: ir.DataTypeComponent) -> Dict[str, str]:
        """Create a map from expression names to binding signal names.
        
        For each binding pair, both sides map to the same internal signal.
        
        Returns dict: {expr_name -> binding_signal_name}
        """
        binding_map = {}
        
        for b in comp.bind_map:
            # Get the canonical signal name (from LHS)
            signal_name = self._generate_expr(b.lhs, comp)
            if signal_name.startswith("/*"):
                continue
            
            # Map both sides to this signal
            lhs_name = self._generate_expr(b.lhs, comp)
            rhs_name = self._generate_expr(b.rhs, comp)
            
            if not lhs_name.startswith("/*"):
                binding_map[lhs_name] = signal_name
            if not rhs_name.startswith("/*"):
                binding_map[rhs_name] = signal_name
        
        return binding_map
    
    def _collect_binding_signals(self, comp: ir.DataTypeComponent) -> Dict[str, ir.DataType]:
        """Collect all unique signals referenced in bindings.
        
        For each binding, we only need ONE signal (the connection point).
        We use the "simpler" side as the signal name.
        
        Returns a dict mapping signal name to datatype.
        """
        signals = {}
        
        for b in comp.bind_map:
            # Get signal names from both sides
            lhs_name = self._generate_expr(b.lhs, comp)
            rhs_name = self._generate_expr(b.rhs, comp)
            
            # Skip unknown expressions
            if lhs_name.startswith("/*") or rhs_name.startswith("/*"):
                continue
            
            # Use the first side as the canonical signal name
            # (We'll connect both sides to this signal)
            signal_name = lhs_name
            
            # Try to infer the datatype from either side
            datatype = self._infer_binding_signal_type(b.lhs, comp)
            if not datatype:
                datatype = self._infer_binding_signal_type(b.rhs, comp)
            
            if signal_name not in signals and datatype:
                signals[signal_name] = datatype
        
        return signals
    
    def _infer_binding_signal_type(self, expr: ir.Expr, comp: ir.DataTypeComponent) -> Optional[ir.DataType]:
        """Infer the datatype of a binding signal expression."""
        if isinstance(expr, ir.ExprRefField):
            # Nested reference: self.instance.port
            if isinstance(expr.base, ir.ExprRefField) and isinstance(expr.base.base, ir.TypeExprRefSelf):
                inst_idx = expr.base.index
                port_idx = expr.index
                if inst_idx < len(comp.fields):
                    inst_field = comp.fields[inst_idx]
                    if isinstance(inst_field.datatype, ir.DataTypeRef):
                        inst_type = self._ctxt.type_m.get(inst_field.datatype.ref_name)
                        if inst_type and hasattr(inst_type, 'fields') and port_idx < len(inst_type.fields):
                            return inst_type.fields[port_idx].datatype
        
        elif isinstance(expr, ir.ExprRefPy):
            # Python reference to extern port: self.instance.attr
            if isinstance(expr.base, ir.ExprRefField) and isinstance(expr.base.base, ir.TypeExprRefSelf):
                inst_idx = expr.base.index
                if inst_idx < len(comp.fields):
                    inst_field = comp.fields[inst_idx]
                    if isinstance(inst_field.datatype, ir.DataTypeRef):
                        inst_type = self._ctxt.type_m.get(inst_field.datatype.ref_name)
                        # For extern types, try to infer from Python type
                        if isinstance(inst_type, ir.DataTypeExtern) and inst_type.py_type:
                            # Get the Python field definition
                            if hasattr(inst_type.py_type, '__dataclass_fields__'):
                                py_field = inst_type.py_type.__dataclass_fields__.get(expr.ref)
                                if py_field and py_field.metadata:
                                    # Try to get width from metadata
                                    width_meta = py_field.metadata.get('width')
                                    if width_meta:
                                        # Common types
                                        if width_meta == 8:
                                            return ir.DataTypeInt(bits=8)
                                        elif width_meta == 32:
                                            return ir.DataTypeInt(bits=32)
                                    # Check field name for common patterns
                                    field_name = expr.ref
                                    if 'clk' in field_name or 'rst' in field_name or 'enable' in field_name:
                                        return ir.DataTypeInt(bits=1)
                                    elif 'count' in field_name:
                                        return ir.DataTypeInt(bits=8)
        
        # Default fallback
        return ir.DataTypeInt(bits=1)
    
    def _generate_component(self, comp: ir.DataTypeComponent) -> str:
        """Generate SystemVerilog code for a component."""
        all_code = []
        
        # Add source file header comment if debug mode is enabled
        if self.debug_annotations and comp.loc:
            all_code.append(f"// Generated from: {comp.loc.file}:{comp.loc.line}")
            all_code.append("")
        
        # First, generate interfaces for any export fields
        export_interfaces = self._generate_export_interfaces(comp)
        if export_interfaces:
            all_code.append(export_interfaces)
            all_code.append("")
        
        lines = []
        
        # Sanitize module name for SystemVerilog
        module_name = self._sanitize_sv_name(comp.name)
        
        # Collect const fields (parameters)
        const_fields = [f for f in comp.fields if f.is_const]
        
        # Module declaration with parameters
        if const_fields:
            lines.append(f"module {module_name} #(")
            param_lines = []
            for field in const_fields:
                # Get parameter type and default value
                param_type = "int"  # Default to int for const fields
                if isinstance(field.datatype, ir.DataTypeInt):
                    if field.datatype.bits <= 32:
                        param_type = "int"
                
                # Get default value from field metadata or datatype
                # The default should be in the py_type's field definition
                default_val = 32  # Fallback default
                if comp.py_type and hasattr(comp.py_type, '__dataclass_fields__'):
                    py_field = comp.py_type.__dataclass_fields__.get(field.name)
                    if py_field and py_field.default is not None:
                        default_val = py_field.default
                
                param_lines.append(f"  parameter {param_type} {field.name} = {default_val}")
            
            lines.append(",\n".join(param_lines))
            lines.append(")(")
        else:
            lines.append(f"module {module_name}(")
        
        # Generate port list
        ports = []
        
        # Check if this component has exports (is a top-level XtorComponent)
        has_exports = any(f.kind == ir.FieldKind.Export for f in comp.fields)
        
        for field in comp.fields:
            # Skip const fields - they're parameters, not ports
            if field.is_const:
                continue
                
            if isinstance(field, ir.FieldInOut):
                port_dir = "output" if field.is_out else "input"
                # Check if field has parameterized width
                if field.width_expr:
                    # Evaluate width expression to get parameter-based width
                    port_type = self._get_sv_parameterized_type(field, comp)
                else:
                    port_type = self._get_sv_type(field.datatype)
                ports.append(f"  {port_dir} {port_type} {field.name}")
            elif isinstance(field.datatype, ir.DataTypeRef) and field.kind != ir.FieldKind.Export:
                # Expose bundles as flattened ports for external connections (only if no exports)
                ref_type = self._resolve_bundle_type(field, comp)
                if ref_type:
                    # If component has exports (XtorComponent), bundles are internal, not ports
                    if not has_exports:
                        # This is a bundle - expose as flattened ports with proper directions
                        # Get the struct type to determine field directions
                        for struct_field in ref_type.fields:
                            if isinstance(struct_field, ir.FieldInOut):
                                # Use the direction from the struct definition
                                port_dir = "output" if struct_field.is_out else "input"
                                flat_name = f"{field.name}_{struct_field.name}"
                                # Handle parameterized widths in bundle fields
                                if struct_field.width_expr and hasattr(struct_field.width_expr, 'callable'):
                                    width_expr = self._eval_width_lambda_to_sv(struct_field.width_expr.callable, ref_type)
                                    if width_expr == "1":
                                        type_str = "logic"
                                    else:
                                        type_str = f"logic [({width_expr}-1):0]"
                                elif struct_field.datatype.bits == 1 or struct_field.datatype.bits == -1:
                                    type_str = "logic"
                                else:
                                    type_str = f"logic [{struct_field.datatype.bits-1}:0]"
                                ports.append(f"  {port_dir} {type_str} {flat_name}")
        
        lines.append(",\n".join(ports))
        lines.append(");")
        lines.append("")
        
        # Internal signal declarations
        for field in comp.fields:
            # Skip const fields - they're parameters
            if field.is_const:
                continue
            if isinstance(field, ir.FieldInOut):
                continue
            # Skip export fields - they become interface instances
            if field.kind == ir.FieldKind.Export:
                continue
            # Handle bundle fields
            if isinstance(field.datatype, ir.DataTypeRef):
                ref_type = self._resolve_bundle_type(field, comp)
                if ref_type:
                    # This is a bundle - if component has exports, declare as internal signals
                    if has_exports:
                        # Flatten bundle fields into individual signal declarations
                        for struct_field in ref_type.fields:
                            if isinstance(struct_field, ir.FieldInOut):
                                flat_name = f"{field.name}_{struct_field.name}"
                                # Handle parameterized widths in bundle fields
                                if struct_field.width_expr and hasattr(struct_field.width_expr, 'callable'):
                                    width_expr = self._eval_width_lambda_to_sv(struct_field.width_expr.callable, ref_type)
                                    if width_expr == "1":
                                        type_str = "logic"
                                    else:
                                        type_str = f"logic [({width_expr}-1):0]"
                                elif struct_field.datatype.bits == 1 or struct_field.datatype.bits == -1:
                                    type_str = "logic"
                                else:
                                    type_str = f"logic [{struct_field.datatype.bits-1}:0]"
                                lines.append(f"  {type_str} {flat_name};")
                    # Skip further processing - either declared as internal or as ports
                    continue
                # Non-bundle DataTypeRef (component instances) - skip
                continue
            # Declare regular fields
            if isinstance(field.datatype, ir.DataTypeInt):
                # Get the data type (logic, logic [N:0])
                if field.datatype.bits == 1 or field.datatype.bits == -1:
                    type_str = "logic"
                else:
                    type_str = f"logic [{field.datatype.bits-1}:0]"
                lines.append(f"  {type_str} {field.name};")
            elif isinstance(field.datatype, ir.DataTypeArray):
                # Memory / register-file array: logic [W-1:0] name [0:N-1]
                elem = field.datatype.element_type
                size = field.datatype.size
                if isinstance(elem, ir.DataTypeInt):
                    bits = elem.bits
                    elem_type = "logic" if bits <= 1 else f"logic [{bits-1}:0]"
                else:
                    elem_type = "logic [31:0]"
                lines.append(f"  {elem_type} {field.name} [0:{size-1}];")
            elif type(field.datatype) == ir.DataType:
                # Bare DataType - infer from field name
                type_str = self._infer_signal_type_from_name(field.name)
                lines.append(f"  {type_str} {field.name};")
        if any((not isinstance(f, ir.FieldInOut) and
                (isinstance(f.datatype, (ir.DataTypeInt, ir.DataTypeArray)) or
                 type(f.datatype) == ir.DataType) and
                f.kind != ir.FieldKind.Export) for f in comp.fields):
            lines.append("")

        # Declare wire signals for @property processes
        for func in comp.wire_processes:
            ret = func.returns
            if isinstance(ret, ir.DataTypeInt):
                wtype = "logic" if ret.bits <= 1 else f"logic [{ret.bits-1}:0]"
            else:
                wtype = "logic"
            lines.append(f"  {wtype} {func.name};")
        if comp.wire_processes:
            lines.append("")
        
        # Declare internal signals for bindings
        if comp.bind_map:
            binding_signals = self._collect_binding_signals(comp)
            if binding_signals:
                # Collect names of signals already declared (ports and internal fields)
                declared_names = set()
                for field in comp.fields:
                    if field.is_const:
                        continue
                    # FieldInOut are ports
                    if isinstance(field, ir.FieldInOut):
                        declared_names.add(field.name)
                    elif isinstance(field.datatype, ir.DataTypeRef) and field.kind != ir.FieldKind.Export:
                        ref_type = self._resolve_bundle_type(field, comp)
                        if ref_type:
                            # Bundle/extern fields - flattened names
                            for struct_field in ref_type.fields:
                                if isinstance(struct_field, ir.FieldInOut):
                                    declared_names.add(f"{field.name}_{struct_field.name}")
                    elif isinstance(field.datatype, ir.DataTypeInt) or type(field.datatype) == ir.DataType:
                        # Regular internal fields declared above
                        declared_names.add(field.name)
                
                # Filter out signals that are already declared
                filtered_signals = {k: v for k, v in binding_signals.items() if k not in declared_names}
                if filtered_signals:
                    lines.append("  // Internal signals for bindings")
                    for sig_name, sig_type in sorted(filtered_signals.items()):
                        type_str = self._get_sv_type(sig_type)
                        lines.append(f"  {type_str} {sig_name};")
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

        # Generate combinational processes (@comb → always @(*))
        for func in comp.comb_processes:
            lines.extend(self._generate_comb_process(func, comp))

        # Generate continuous-assignment wires (@property → assign)
        for func in comp.wire_processes:
            lines.extend(self._generate_wire_process(func, comp))

        
        # Filter for async functions that aren't already in sync_processes
        async_funcs = []
        for f in comp.functions:
            # Check if function has is_async attribute and is async
            if hasattr(f, 'is_async') and f.is_async and f.name.startswith('_'):
                # Make sure it's not already in sync_processes
                if f not in comp.sync_processes:
                    async_funcs.append(f)
        
        for func in async_funcs:
            lines.extend(self._generate_async_process(func, comp))
        
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
    
    def _get_sv_parameterized_type(self, field: ir.Field, comp: ir.DataTypeComponent) -> str:
        """Get SystemVerilog type for field with parameterized width.
        
        Generates types like: logic [(DATA_WIDTH-1):0]
        """
        if not field.width_expr:
            return self._get_sv_type(field.datatype)
        
        # Check if this is an ExprLambda (from either expr.py or expr_phase2.py)
        if hasattr(field.width_expr, 'callable') and callable(getattr(field.width_expr, 'callable', None)):
            # Create a mock object to evaluate the lambda and extract parameter references
            width_expr_sv = self._eval_width_lambda_to_sv(field.width_expr.callable, comp)
            
            if width_expr_sv == "1":
                return "logic"
            else:
                return f"logic [({width_expr_sv}-1):0]"
        
        # Fallback
        return self._get_sv_type(field.datatype)
    
    def _eval_width_lambda_to_sv(self, width_lambda: callable, comp: ir.DataTypeComponent) -> str:
        """Evaluate width lambda to generate SystemVerilog parameter expression.
        
        Converts Python lambda like 'lambda s:s.DATA_WIDTH' to 'DATA_WIDTH'.
        Handles expressions like 'lambda s:s.DATA_WIDTH/8' to 'DATA_WIDTH/8'.
        """
        # Create a mock object that records parameter accesses
        class ParamAccessRecorder:
            def __init__(self, comp):
                self._comp = comp
                self._accesses = []
            
            def __getattribute__(self, name):
                if name.startswith('_'):
                    return object.__getattribute__(self, name)
                
                comp = object.__getattribute__(self, '_comp')
                accesses = object.__getattribute__(self, '_accesses')
                
                # Check if this is a const field
                for field in comp.fields:
                    if field.name == name and field.is_const:
                        accesses.append(name)
                        # Return a special object that tracks operations
                        return ParamValue(name)
                
                # Fallback for unknown attributes
                raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")
        
        class ParamValue:
            """Represents a parameter value that tracks arithmetic operations."""
            def __init__(self, expr_str):
                self.expr_str = expr_str
            
            def __truediv__(self, other):
                return ParamValue(f"{self.expr_str}/{other}")
            
            def __floordiv__(self, other):
                return ParamValue(f"{self.expr_str}/{other}")
            
            def __add__(self, other):
                if isinstance(other, ParamValue):
                    return ParamValue(f"{self.expr_str}+{other.expr_str}")
                return ParamValue(f"{self.expr_str}+{other}")
            
            def __sub__(self, other):
                if isinstance(other, ParamValue):
                    return ParamValue(f"{self.expr_str}-{other.expr_str}")
                return ParamValue(f"{self.expr_str}-{other}")
            
            def __mul__(self, other):
                if isinstance(other, ParamValue):
                    return ParamValue(f"{self.expr_str}*{other.expr_str}")
                return ParamValue(f"{self.expr_str}*{other}")
            
            def __int__(self):
                # When int() is called on a ParamValue, we want to preserve the expression
                # but signal that it was wrapped in int(). Since Python requires __int__
                # to return an actual int, we need a workaround.
                # Instead, we'll raise a special exception that we catch and handle.
                raise _IntWrapException(self)
            
            def __str__(self):
                return self.expr_str
        
        class _IntWrapException(Exception):
            """Exception to signal int() was called on ParamValue."""
            def __init__(self, param_value):
                self.param_value = param_value
                super().__init__()
        
        try:
            recorder = ParamAccessRecorder(comp)
            result = width_lambda(recorder)
            
            if isinstance(result, ParamValue):
                return result.expr_str
            elif isinstance(result, int):
                return str(result)
            else:
                return str(result)
        except _IntWrapException as e:
            # int() was called on a ParamValue - just use the expression
            return e.param_value.expr_str
        except Exception as e:
            # Fallback: return a generic expression
            # In production, could log this for debugging
            return "WIDTH"

    def _extract_param_overrides(self, field: ir.Field, comp: ir.DataTypeComponent) -> Dict[str, str]:
        """Extract parameter overrides from field's kwargs_expr.
        
        Converts kwargs like:
        - kwargs=lambda s:dict(DATA_WIDTH=16) → {"DATA_WIDTH": "16"}
        - kwargs=lambda s:dict(WIDTH=s.DATA_WIDTH+4) → {"WIDTH": "DATA_WIDTH+4"}
        """
        if not field.kwargs_expr:
            return {}
        
        # Check if this is an ExprLambda with a callable
        if not (hasattr(field.kwargs_expr, 'callable') and callable(getattr(field.kwargs_expr, 'callable', None))):
            return {}
        
        kwargs_lambda = field.kwargs_expr.callable
        
        # Use the same param tracking mechanism as for width expressions
        class ParamAccessRecorder:
            def __init__(self, comp):
                self._comp = comp
            
            def __getattribute__(self, name):
                if name.startswith('_'):
                    return object.__getattribute__(self, name)
                
                comp = object.__getattribute__(self, '_comp')
                
                # Check if this is a const field
                for fld in comp.fields:
                    if fld.name == name and fld.is_const:
                        # Return a special object that tracks operations
                        return ParamValue(name)
                
                # Fallback for unknown attributes
                raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")
        
        class ParamValue:
            """Represents a parameter value that tracks arithmetic operations."""
            def __init__(self, expr_str):
                self.expr_str = expr_str
            
            def __truediv__(self, other):
                if isinstance(other, ParamValue):
                    return ParamValue(f"{self.expr_str}/{other.expr_str}")
                return ParamValue(f"{self.expr_str}/{other}")
            
            def __floordiv__(self, other):
                if isinstance(other, ParamValue):
                    return ParamValue(f"{self.expr_str}/{other.expr_str}")
                return ParamValue(f"{self.expr_str}/{other}")
            
            def __add__(self, other):
                if isinstance(other, ParamValue):
                    return ParamValue(f"{self.expr_str}+{other.expr_str}")
                return ParamValue(f"{self.expr_str}+{other}")
            
            def __sub__(self, other):
                if isinstance(other, ParamValue):
                    return ParamValue(f"{self.expr_str}-{other.expr_str}")
                return ParamValue(f"{self.expr_str}-{other}")
            
            def __mul__(self, other):
                if isinstance(other, ParamValue):
                    return ParamValue(f"{self.expr_str}*{other.expr_str}")
                return ParamValue(f"{self.expr_str}*{other}")
            
            def __int__(self):
                return self
            
            def __str__(self):
                return self.expr_str
        
        try:
            recorder = ParamAccessRecorder(comp)
            result = kwargs_lambda(recorder)
            
            # Result should be a dict
            if not isinstance(result, dict):
                return {}
            
            # Convert dict values to SV expressions
            sv_params = {}
            for key, value in result.items():
                if isinstance(value, ParamValue):
                    sv_params[key] = value.expr_str
                elif isinstance(value, int):
                    sv_params[key] = str(value)
                else:
                    sv_params[key] = str(value)
            
            return sv_params
        except Exception as e:
            # If evaluation fails, return empty dict
            return {}
    
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
                """Match expressions referencing this instance's ports."""
                # Handle ExprRefField (regular ports)
                if isinstance(expr, ir.ExprRefField):
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
                
                # Handle ExprRefPy (extern ports)
                elif isinstance(expr, ir.ExprRefPy):
                    if not isinstance(expr.base, ir.ExprRefField):
                        return None
                    if not isinstance(expr.base.base, ir.TypeExprRefSelf):
                        return None
                    inst_idx = expr.base.index
                    if inst_idx >= len(comp.fields):
                        return None
                    if comp.fields[inst_idx].name != inst_name:
                        return None
                    # For ExprRefPy, the port name is in expr.ref
                    return expr.ref
                
                return None

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
            
            # Extract parameter overrides from kwargs_expr
            param_overrides = self._extract_param_overrides(field, comp)

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

            # Generate instantiation with parameter overrides
            if param_overrides:
                param_list = ", ".join([f".{k}({v})" for k, v in param_overrides.items()])
                lines.append(f"  {mod_name} #({param_list}) {inst_name} (")
            else:
                lines.append(f"  {mod_name} {inst_name} (")
            conns = []
            for pname, pexpr in port_conn.items():
                conns.append(f"    .{pname}({pexpr})")
            lines.append(",\n".join(conns))
            lines.append("  );")

        return lines

    def _collect_local_vars(self, stmts: list) -> set:
        """Recursively collect ExprRefLocal targets to find locally-assigned variables."""
        found = set()
        for stmt in stmts:
            if hasattr(stmt, 'targets'):
                for t in stmt.targets:
                    if isinstance(t, ir.ExprRefLocal):
                        found.add(t.name)
            if hasattr(stmt, 'target') and isinstance(stmt.target, ir.ExprRefLocal):
                found.add(stmt.target.name)
            for attr in ('body', 'orelse', 'handlers'):
                sub = getattr(stmt, attr, None)
                if sub:
                    found.update(self._collect_local_vars(sub))
            if hasattr(stmt, 'cases'):
                for case in stmt.cases:
                    found.update(self._collect_local_vars(case.body))
        return found

    def _generate_wire_process(self, func: ir.Function, comp: ir.DataTypeComponent) -> List[str]:
        """Generate assign statement for a @property wire."""
        from zuspec.dataclasses.ir.stmt import StmtReturn
        for stmt in func.body:
            if isinstance(stmt, StmtReturn) and stmt.value is not None:
                expr_str = self._generate_expr(stmt.value, comp)
                return [f"  assign {func.name} = {expr_str};", ""]
        return []

    def _generate_comb_process(self, func: ir.Function, comp: ir.DataTypeComponent) -> List[str]:
        """Generate always @(*) block from a @comb method."""
        lines = []
        if self.debug_annotations and func.loc:
            lines.append(f"  // Source: {func.loc.file}:{func.loc.line}")
        lines.append(f"  always @(*) begin")
        local_vars = self._collect_local_vars(func.body)
        for name in sorted(local_vars):
            lines.append(f"    logic [31:0] {name};")
        for stmt in func.body:
            lines.extend(self._generate_stmt(stmt, comp, indent=2, blocking=True))
        lines.append("  end")
        lines.append("")
        return lines

    def _has_active_reset_branch(self, body: list, reset_name: str, comp) -> bool:
        """Return True only if the first statement is StmtIf(!reset) with real reset assignments."""
        if not body:
            return False
        first = body[0]
        if not isinstance(first, ir.StmtIf):
            return False
        # Verify the test is !reset_signal (ExprUnary Not of the reset field)
        test = first.test
        if not isinstance(test, ir.ExprUnary) or test.op != ir.UnaryOp.Not:
            return False
        if not isinstance(test.operand, ir.ExprRefField):
            return False
        if not isinstance(test.operand.base, ir.TypeExprRefSelf):
            return False
        reset_field = comp.fields[test.operand.index].name
        if reset_field != reset_name:
            return False
        # Check that the reset branch body has at least one real assignment
        def has_real_stmts(stmts):
            for s in stmts:
                if isinstance(s, (ir.StmtAssign, ir.StmtAugAssign)):
                    return True
                if isinstance(s, ir.StmtIf) and (has_real_stmts(s.body) or has_real_stmts(s.orelse)):
                    return True
            return False
        return has_real_stmts(first.body)

    def _generate_sync_process(self, func: ir.Function, comp: ir.DataTypeComponent) -> List[str]:
        """Generate SystemVerilog always block from sync process."""
        lines = []
        
        # Add source location annotation if debug mode is enabled
        if self.debug_annotations and func.loc:
            lines.append(f"  // Source: {func.loc.file}:{func.loc.line}")
        
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
        
        # Generate always block sensitivity list — always synchronous (posedge clk only).
        # Reset logic inside the body is checked synchronously (if !resetn).
        sensitivity = []
        if clock_name:
            sensitivity.append(f"posedge {clock_name}")
        
        lines.append(f"  always @({' or '.join(sensitivity)}) begin")

        # Declare local variables used within this process
        local_vars = self._collect_local_vars(func.body)
        for name in sorted(local_vars):
            lines.append(f"    logic [31:0] {name};")

        # Generate body (early-return hoisting converts `if (cond) { ...; return; }` + remaining
        # statements into `if (cond) { ...; } else { remaining }`, preserving reset priority)
        lines.extend(self._generate_stmt_list(func.body, comp, indent=2))
        
        lines.append("  end")
        lines.append("")
        
        return lines

    def _generate_async_process(self, func: ir.Function, comp: ir.DataTypeComponent) -> List[str]:
        """Generate SystemVerilog initial block from async process (@process).
        
        Converts async/await statements to SystemVerilog timing controls.
        """
        lines = []
        
        # Add source location annotation if debug mode is enabled
        if self.debug_annotations and func.loc:
            lines.append(f"  // Source: {func.loc.file}:{func.loc.line}")
        
        lines.append("  initial begin")
        
        # Generate body, converting async/await to SV
        for stmt in func.body:
            lines.extend(self._generate_async_stmt(stmt, comp, indent=2))
        
        lines.append("  end")
        lines.append("")
        
        return lines
    
    def _generate_async_stmt(self, stmt: ir.Stmt, comp: ir.DataTypeComponent, indent: int = 0) -> List[str]:
        """Generate SystemVerilog statement from async process, handling await."""
        ind = "  " * indent
        lines = []
        
        if isinstance(stmt, ir.StmtExpr):
            # Check if this is an await statement
            if isinstance(stmt.expr, ir.ExprAwait):
                # await statement - convert to delay
                await_expr = stmt.expr.value
                if isinstance(await_expr, ir.ExprCall):
                    # Check if it's a wait call
                    if hasattr(await_expr, 'func') and isinstance(await_expr.func, ir.ExprAttribute):
                        if await_expr.func.attr == 'wait' and len(await_expr.args) > 0:
                            # Extract time value
                            time_arg = await_expr.args[0]
                            time_val = self._extract_time_value(time_arg)
                            if time_val:
                                lines.append(f"{ind}#{time_val};")
                            else:
                                lines.append(f"{ind}// TODO: await {await_expr}")
                        elif await_expr.func.attr == 'posedge':
                            # await posedge(signal)
                            signal_expr = self._generate_expr(await_expr.args[0], comp)
                            lines.append(f"{ind}@(posedge {signal_expr});")
                    else:
                        lines.append(f"{ind}// TODO: await {await_expr}")
            else:
                # Regular expression statement
                lines.extend(self._generate_stmt(stmt, comp, indent))
        
        elif isinstance(stmt, ir.StmtFor):
            # For loop
            # Extract iterator info
            target = stmt.target
            iter_expr = stmt.iter
            
            # Try to determine range
            if isinstance(iter_expr, ir.ExprCall):
                if hasattr(iter_expr, 'func') and isinstance(iter_expr.func, ir.ExprRefBuiltin):
                    if iter_expr.func.name == 'range':
                        # Handle range(n) loop
                        if len(iter_expr.args) == 1:
                            end_val = self._generate_expr(iter_expr.args[0], comp)
                            target_name = target.name if isinstance(target, ir.ExprRefLocal) else "i"
                            lines.append(f"{ind}for (int {target_name} = 0; {target_name} < {end_val}; {target_name}++) begin")
                        elif len(iter_expr.args) == 2:
                            start_val = self._generate_expr(iter_expr.args[0], comp)
                            end_val = self._generate_expr(iter_expr.args[1], comp)
                            target_name = target.name if isinstance(target, ir.ExprRefLocal) else "i"
                            lines.append(f"{ind}for (int {target_name} = {start_val}; {target_name} < {end_val}; {target_name}++) begin")
                        
                        # Generate loop body
                        for s in stmt.body:
                            lines.extend(self._generate_async_stmt(s, comp, indent + 1))
                        
                        lines.append(f"{ind}end")
                    else:
                        lines.append(f"{ind}// TODO: for loop over {iter_expr}")
        
        elif isinstance(stmt, ir.StmtAssign):
            # Regular assignment
            for target in stmt.targets:
                target_expr = self._generate_expr(target, comp)
                value_expr = self._generate_expr(stmt.value, comp)
                lines.append(f"{ind}{target_expr} = {value_expr};")
        
        else:
            # Fall back to regular statement generation
            lines.extend(self._generate_stmt(stmt, comp, indent))
        
        return lines
    
    def _extract_time_value(self, expr: ir.Expr) -> Optional[str]:
        """Extract time value from Time.ns(N) or similar expressions.
        
        Returns: String like "10ns" or None if can't extract
        """
        if isinstance(expr, ir.ExprCall):
            if hasattr(expr, 'func') and isinstance(expr.func, ir.ExprAttribute):
                # Time.ns(10) -> func.attr == 'ns'
                if expr.func.attr in ['ns', 'us', 'ms', 'ps']:
                    if len(expr.args) > 0:
                        val_expr = expr.args[0]
                        if isinstance(val_expr, ir.ExprConstant):
                            return f"{val_expr.value}{expr.func.attr}"
        
        return None


    def _generate_stmt_list(self, stmts: list, comp: ir.DataTypeComponent, indent: int = 0, subst: dict = None, blocking: bool = False) -> list:
        """Generate SV for a list of statements with early-return hoisting.

        If a StmtIf with no orelse ends its body with StmtReturn, all subsequent
        sibling statements are moved into the if's else branch.  This preserves
        reset-priority semantics:

            Python:                        Generated SV:
            if not resetn:                 if (!resetn) begin
                Q = 0                          Q <= 0;
                return                     end else begin
            Q = D  # else path                 Q <= D;
                                           end

        Without this transform the generator would emit two separate if-blocks,
        making the enable (second if) win over reset — wrong SDFFCE instead of SDFFE.
        """
        lines = []
        i = 0
        while i < len(stmts):
            stmt = stmts[i]
            # Detect: if-block whose body ends with StmtReturn and has no orelse
            if (isinstance(stmt, ir.StmtIf) and
                    not stmt.orelse and
                    stmt.body and
                    isinstance(stmt.body[-1], ir.StmtReturn)):
                # Collect remaining sibling statements (skip any bare StmtReturn)
                remaining = [s for s in stmts[i+1:] if not isinstance(s, ir.StmtReturn)]
                ind = "  " * indent
                comment = getattr(stmt, 'comment', None)
                if comment:
                    for cline in comment.splitlines():
                        lines.append(f"{ind}// {cline}")
                pragmas = getattr(stmt, 'pragmas', {})
                if pragmas:
                    attr_parts = [k for k in pragmas if pragmas[k] is True]
                    if attr_parts:
                        lines.append(f"{ind}(* {', '.join(attr_parts)} *)")
                test_expr = self._generate_expr(stmt.test, comp, subst)
                lines.append(f"{ind}if ({test_expr}) begin")
                # Emit body without the trailing StmtReturn
                lines.extend(self._generate_stmt_list(stmt.body[:-1], comp, indent + 1, subst=subst, blocking=blocking))
                if remaining:
                    lines.append(f"{ind}end else begin")
                    lines.extend(self._generate_stmt_list(remaining, comp, indent + 1, subst=subst, blocking=blocking))
                lines.append(f"{ind}end")
                return lines  # all remaining siblings consumed
            else:
                lines.extend(self._generate_stmt(stmt, comp, indent, subst=subst, blocking=blocking))
            i += 1
        return lines

    def _generate_stmt(self, stmt: ir.Stmt, comp: ir.DataTypeComponent, indent: int = 0, subst: dict = None, blocking: bool = False) -> List[str]:
        """Generate SystemVerilog statement."""
        ind = "  " * indent
        lines = []

        # Emit leading comment lines attached to this statement
        comment = getattr(stmt, 'comment', None)
        if comment:
            for cline in comment.splitlines():
                lines.append(f"{ind}// {cline}")

        if isinstance(stmt, ir.StmtIf):
            pragmas = getattr(stmt, 'pragmas', {})
            if pragmas:
                attr_parts = [k for k in pragmas if pragmas[k] is True]
                if attr_parts:
                    lines.append(f"{ind}(* {', '.join(attr_parts)} *)")
            test_expr = self._generate_expr(stmt.test, comp, subst)
            lines.append(f"{ind}if ({test_expr}) begin")
            lines.extend(self._generate_stmt_list(stmt.body, comp, indent + 1, subst=subst, blocking=blocking))
            # Walk elif chain: orelse containing a single StmtIf → emit "else if"
            orelse = stmt.orelse
            while orelse and len(orelse) == 1 and isinstance(orelse[0], ir.StmtIf):
                elif_stmt = orelse[0]
                elif_pragmas = getattr(elif_stmt, 'pragmas', {})
                elif_test_expr = self._generate_expr(elif_stmt.test, comp, subst)
                if elif_pragmas:
                    attr_parts = [k for k in elif_pragmas if elif_pragmas[k] is True]
                    if attr_parts:
                        lines.append(f"{ind}end else (* {', '.join(attr_parts)} *) if ({elif_test_expr}) begin")
                    else:
                        lines.append(f"{ind}end else if ({elif_test_expr}) begin")
                else:
                    lines.append(f"{ind}end else if ({elif_test_expr}) begin")
                lines.extend(self._generate_stmt_list(elif_stmt.body, comp, indent + 1, subst=subst, blocking=blocking))
                orelse = elif_stmt.orelse
            if orelse:
                lines.append(f"{ind}end else begin")
                lines.extend(self._generate_stmt_list(orelse, comp, indent + 1, subst=subst, blocking=blocking))
            lines.append(f"{ind}end")
        
        elif isinstance(stmt, ir.StmtMatch):
            pragmas = getattr(stmt, 'pragmas', {})
            if pragmas:
                attr_parts = [k for k in pragmas if pragmas[k] is True]
                if attr_parts:
                    lines.append(f"{ind}(* {', '.join(attr_parts)} *)")
            subject_expr = self._generate_expr(stmt.subject, comp, subst)
            lines.append(f"{ind}case ({subject_expr})")
            
            for case in stmt.cases:
                case_label = self._generate_pattern(case.pattern, comp)
                lines.append(f"{ind}  {case_label}: begin")
                for s in case.body:
                    lines.extend(self._generate_stmt(s, comp, indent + 2, subst=subst, blocking=blocking))
                lines.append(f"{ind}  end")
            
            lines.append(f"{ind}endcase")
        
        elif isinstance(stmt, ir.StmtAssign):
            pragmas = getattr(stmt, 'pragmas', {})
            use_x = pragmas.get('x', False)
            for target in stmt.targets:
                is_local = isinstance(target, ir.ExprRefLocal)
                assign_op = "=" if (blocking or is_local) else "<="
                target_expr = self._generate_expr(target, comp, subst)
                if use_x:
                    lines.append(f"{ind}{target_expr} {assign_op} 'bx;")
                else:
                    value_expr = self._generate_expr(stmt.value, comp, subst)
                    lines.append(f"{ind}{target_expr} {assign_op} {value_expr};")
        
        elif isinstance(stmt, ir.StmtAugAssign):
            is_local = isinstance(stmt.target, ir.ExprRefLocal)
            assign_op = "=" if (blocking or is_local) else "<="
            target_expr = self._generate_expr(stmt.target, comp, subst)
            value_expr = self._generate_expr(stmt.value, comp, subst)
            op = self._get_sv_op(stmt.op)
            lines.append(f"{ind}{target_expr} {assign_op} {target_expr} {op} {value_expr};")

        elif isinstance(stmt, ir.StmtFor):
            target = stmt.target
            iter_expr = stmt.iter
            # Unroll for-loops that iterate over a list of strings (e.g. field names)
            if isinstance(iter_expr, ir.ExprConstant) and isinstance(iter_expr.value, list):
                var_name = target.name if isinstance(target, ir.ExprRefLocal) else "_i"
                for val in iter_expr.value:
                    subst = {var_name: str(val)}
                    for s in stmt.body:
                        lines.extend(self._generate_stmt(s, comp, indent, subst=subst, blocking=blocking))
            elif isinstance(iter_expr, ir.ExprCall):
                # range() loop
                func = getattr(iter_expr, 'func', None)
                fname = getattr(func, 'name', None)
                if fname == 'range' and iter_expr.args:
                    var_name = target.name if isinstance(target, ir.ExprRefLocal) else "i"
                    if len(iter_expr.args) == 1:
                        end_val = self._generate_expr(iter_expr.args[0], comp)
                        lines.append(f"{ind}for (integer {var_name} = 0; {var_name} < {end_val}; {var_name}++) begin")
                    else:
                        start_val = self._generate_expr(iter_expr.args[0], comp)
                        end_val   = self._generate_expr(iter_expr.args[1], comp)
                        lines.append(f"{ind}for (integer {var_name} = {start_val}; {var_name} < {end_val}; {var_name}++) begin")
                    for s in stmt.body:
                        lines.extend(self._generate_stmt(s, comp, indent + 1, blocking=blocking))
                    lines.append(f"{ind}end")

        elif isinstance(stmt, ir.StmtExpr):
            # setattr(self, field_name_const, value) from unrolled loop
            if isinstance(stmt.expr, ir.ExprCall):
                call = stmt.expr
                if (isinstance(call.func, ir.ExprRefUnresolved) and
                        call.func.name == 'setattr' and len(call.args) == 3):
                    name_arg = call.args[1]
                    val_arg  = call.args[2]
                    field_name = None
                    if isinstance(name_arg, ir.ExprConstant) and isinstance(name_arg.value, str):
                        field_name = name_arg.value
                    if isinstance(name_arg, ir.ExprRefLocal) and subst and name_arg.name in subst:
                        field_name = subst[name_arg.name]
                    if field_name:
                        assign_op = "=" if blocking else "<="
                        val_str = self._generate_expr(val_arg, comp, subst)
                        lines.append(f"{ind}{field_name} {assign_op} {val_str};")

        elif isinstance(stmt, ir.StmtReturn):
            pass  # return in always blocks is just end-of-path; handled by if/else structure

        return lines

    def _generate_expr(self, expr: ir.Expr, comp: ir.DataTypeComponent, subst: dict = None) -> str:
        """Generate SystemVerilog expression."""
        if isinstance(expr, ir.ExprRefField):
            # Look up field by index
            if isinstance(expr.base, ir.TypeExprRefSelf):
                # Direct field reference: self.field
                if expr.index < len(comp.fields):
                    return comp.fields[expr.index].name
                return f"field_{expr.index}"
            elif isinstance(expr.base, ir.ExprRefField):
                # Nested field reference: self.instance.field
                if isinstance(expr.base.base, ir.TypeExprRefSelf):
                    inst_idx = expr.base.index
                    port_idx = expr.index
                    if inst_idx < len(comp.fields):
                        inst_name = comp.fields[inst_idx].name
                        # Look up the instance type to get port name
                        inst_field = comp.fields[inst_idx]
                        if isinstance(inst_field.datatype, ir.DataTypeRef):
                            inst_type = self._ctxt.type_m.get(inst_field.datatype.ref_name)
                            if inst_type and port_idx < len(inst_type.fields):
                                port_name = inst_type.fields[port_idx].name
                                # For bindings, we want just the signal name (no prefix)
                                # The signal will be an internal wire connecting the instances
                                return f"{inst_name}_{port_name}"
                return "/* nested field */"
            return f"field_{expr.index}"
        
        elif isinstance(expr, ir.ExprRefPy):
            # Python reference: self.instance.attr
            if hasattr(expr, 'base') and isinstance(expr.base, ir.ExprRefField):
                if isinstance(expr.base.base, ir.TypeExprRefSelf):
                    inst_idx = expr.base.index
                    if inst_idx < len(comp.fields):
                        inst_name = comp.fields[inst_idx].name
                        # expr.ref is the Python attribute name
                        return f"{inst_name}_{expr.ref}"
            return "/* unknown py ref */"
        
        elif isinstance(expr, ir.ExprConstant):
            value = expr.value
            if isinstance(value, int):
                # Determine bit width if possible
                return str(value)
            return str(value)
        
        elif isinstance(expr, ir.ExprAttribute):
            # self.attr where attr is a @property not tracked as a field index
            if isinstance(expr.value, ir.TypeExprRefSelf):
                return expr.attr
            # ClassName.attr where ClassName is a Python enum/constant class
            if isinstance(expr.value, ir.ExprRefUnresolved):
                resolved = self._resolve_py_class_attr(expr.value.name, expr.attr, comp)
                if resolved is not None:
                    return str(resolved)
                return f"{expr.value.name}_{expr.attr}"
            # Handle bundle field access: flatten to bundle_field
            obj = self._generate_expr(expr.value, comp)
            return f"{obj}_{expr.attr}"

        elif isinstance(expr, ir.ExprSubscript):
            array_expr = self._generate_expr(expr.value, comp)
            if isinstance(expr.slice, ir.ExprSlice):
                # Bit-slice: array[high:low]
                high = self._generate_expr(expr.slice.lower, comp) if expr.slice.lower is not None else None
                low  = self._generate_expr(expr.slice.upper, comp) if expr.slice.upper is not None else None
                if high is not None and low is not None:
                    return f"{array_expr}[{high}:{low}]"
                elif high is not None:
                    return f"{array_expr}[{high}]"
            else:
                idx_expr = self._generate_expr(expr.slice, comp)
                return f"{array_expr}[{idx_expr}]"

        elif isinstance(expr, ir.ExprIfExp):
            # Python  : body if test else orelse
            # SV      : test ? body : orelse
            test_s   = self._generate_expr(expr.test,   comp)
            body_s   = self._generate_expr(expr.body,   comp)
            orelse_s = self._generate_expr(expr.orelse, comp)
            return f"({test_s} ? {body_s} : {orelse_s})"

        elif isinstance(expr, ir.ExprCall):
            # any([a, b, c]) → (a | b | c)
            if (isinstance(expr.func, ir.ExprRefUnresolved) and
                    expr.func.name == 'any' and expr.args):
                from zuspec.dataclasses.ir.expr_phase2 import ExprList
                arg = expr.args[0]
                if isinstance(arg, ExprList) and arg.elts:
                    parts = [f"({self._generate_expr(e, comp)})" for e in arg.elts]
                    return f"({' | '.join(parts)})"
            # bool(x) → identity (already 1-bit or treated as boolean in SV)
            if (isinstance(expr.func, ir.ExprRefUnresolved) and
                    expr.func.name == 'bool' and len(expr.args) == 1):
                return self._generate_expr(expr.args[0], comp, subst)
            # zdc.zext(x, n) → zero-extend lower n bits to 32 bits via masking
            if (isinstance(expr.func, ir.ExprAttribute) and
                    isinstance(expr.func.value, ir.ExprRefUnresolved) and
                    expr.func.value.name == 'zdc' and expr.func.attr == 'zext' and
                    len(expr.args) == 2):
                x_expr = self._generate_expr(expr.args[0], comp, subst)
                n_arg = expr.args[1]
                if isinstance(n_arg, ir.ExprConstant) and isinstance(n_arg.value, int):
                    n = n_arg.value
                    mask = (1 << n) - 1
                    return f"(({x_expr}) & 32'h{mask:08X})"
                return f"({x_expr})"
            
            if (isinstance(expr.func, ir.ExprAttribute) and
                    isinstance(expr.func.value, ir.ExprRefUnresolved) and
                    expr.func.value.name == 'zdc' and expr.func.attr == 'sext' and
                    len(expr.args) == 2):
                x_expr = self._generate_expr(expr.args[0], comp)
                n_arg = expr.args[1]
                if isinstance(n_arg, ir.ExprConstant) and isinstance(n_arg.value, int):
                    n = n_arg.value
                    if n == 32:
                        return f"$signed({x_expr})"
                    fill = 32 - n
                    # Use shift-based sign extension: valid for any expression, avoids
                    # bit-select on parenthesized expressions which is not legal Verilog.
                    return f"($signed(({x_expr}) << {fill}) >>> {fill})"
                return f"$signed({x_expr})"

        elif isinstance(expr, ir.ExprBin):
            # zdc.sext(x, n) >> y → $signed(x) >>> y (arithmetic right shift)
            if (expr.op == ir.BinOp.RShift and
                    isinstance(expr.lhs, ir.ExprCall) and
                    isinstance(expr.lhs.func, ir.ExprAttribute) and
                    isinstance(expr.lhs.func.value, ir.ExprRefUnresolved) and
                    expr.lhs.func.value.name == 'zdc' and expr.lhs.func.attr == 'sext' and
                    expr.lhs.args):
                x_expr = self._generate_expr(expr.lhs.args[0], comp)
                right = self._generate_expr(expr.rhs, comp)
                return f"$signed({x_expr}) >>> {right}"
            left = self._generate_expr(expr.lhs, comp)
            right = self._generate_expr(expr.rhs, comp)
            op = self._get_sv_binop(expr.op)
            return f"({left} {op} {right})"
        
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
            # Wrap each operand in parens to preserve precedence when
            # and/or are mixed (e.g. A && (B || C) must not become A && B || C).
            op = self._get_sv_boolop(expr.op)
            operands = [f"({self._generate_expr(v, comp)})" for v in expr.values]
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
            # Local variable reference — may be substituted in unrolled loops
            if subst and expr.name in subst:
                return subst[expr.name]
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
            ir.AugOp.Add:      "+",
            ir.AugOp.Sub:      "-",
            ir.AugOp.Mult:     "*",
            ir.AugOp.Div:      "/",
            ir.AugOp.Mod:      "%",
            ir.AugOp.LShift:   "<<",
            ir.AugOp.RShift:   ">>",
            ir.AugOp.BitAnd:   "&",
            ir.AugOp.BitOr:    "|",
            ir.AugOp.BitXor:   "^",
            ir.AugOp.FloorDiv: "/",
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

    def _resolve_py_class_attr(self, class_name: str, attr_name: str,
                               comp: ir.DataTypeComponent) -> Optional[int]:
        """Resolve ClassName.attr to an integer constant via the component's Python module."""
        try:
            import importlib
            if comp.py_type:
                mod = importlib.import_module(comp.py_type.__module__)
                cls = getattr(mod, class_name, None)
                if cls is not None:
                    val = getattr(cls, attr_name, None)
                    if val is not None:
                        return int(val)
        except Exception:
            pass
        return None

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
    
    def _generate_pattern(self, pattern: ir.Pattern, comp: ir.DataTypeComponent) -> str:
        """Generate SystemVerilog case label from match pattern."""
        if isinstance(pattern, ir.PatternValue):
            # Literal value pattern (case 0:, case 1:, etc.)
            return self._generate_expr(pattern.value, comp)
        elif isinstance(pattern, ir.PatternAs):
            # Wildcard pattern (case _:) becomes default
            if pattern.name is None:
                return "default"
            # Named capture pattern - treat as default for now
            return "default"
        elif isinstance(pattern, ir.PatternOr):
            # Or pattern (case 1 | 2:) - comma-separated in SV
            patterns = [self._generate_pattern(p, comp) for p in pattern.patterns]
            return ", ".join(patterns)
        else:
            # Unsupported pattern type - use default
            return "default"
    
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
                        elif type(fld.datatype) == ir.DataType:
                            # Bare DataType - infer from field name
                            signal_type = self._infer_signal_type_from_name(signal_name)
                        break
                else:
                    # Check for flattened bundle signals
                    if '_' in signal_name:
                        signal_type = self._infer_bundle_signal_type(signal_name, comp)
                
                # Default to logic if still not found
                if not signal_type:
                    signal_type = "logic"
                
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
        
        elif isinstance(stmt, ir.StmtIf):
            # Check test expression, body, and orelse
            self._collect_signal_refs_from_expr(stmt.test, signals, comp)
            for s in stmt.body:
                self._collect_signal_refs(s, signals, comp)
            for s in stmt.orelse:
                self._collect_signal_refs(s, signals, comp)
        
        elif isinstance(stmt, ir.StmtReturn):
            # Check return value expression
            if stmt.value:
                self._collect_signal_refs_from_expr(stmt.value, signals, comp)
        
        elif isinstance(stmt, ir.StmtMatch):
            # Check subject and all case bodies
            self._collect_signal_refs_from_expr(stmt.subject, signals, comp)
            for case in stmt.cases:
                for s in case.body:
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
        elif isinstance(expr, ir.ExprTuple):
            # Tuple expression - check all elements
            for elem in expr.elts:
                self._collect_signal_refs_from_expr(elem, signals, comp)
    
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
    
    def _infer_expr_type(self, expr: ir.Expr, comp: ir.DataTypeComponent) -> ir.DataType:
        """Infer the data type of an expression."""
        if isinstance(expr, ir.ExprRefField):
            # Field reference - get type from component field
            if isinstance(expr.base, ir.TypeExprRefSelf) and expr.index < len(comp.fields):
                field_type = comp.fields[expr.index].datatype
                # If type is bare DataType (not specialized), try to infer from field name
                if type(field_type) == ir.DataType:
                    field_name = comp.fields[expr.index].name
                    # Use naming conventions to infer types
                    if 'err' in field_name or field_name.endswith('_we') or field_name in ['_req', '_ack', 'clock', 'reset']:
                        return ir.DataTypeInt(bits=1)
                    elif 'dat' in field_name or 'adr' in field_name:
                        return ir.DataTypeInt(bits=32)  # Common data/address width
                    else:
                        return ir.DataTypeInt(bits=32)  # Default
                return field_type
        elif isinstance(expr, ir.ExprConstant):
            # Constant - infer type from value
            if isinstance(expr.value, bool):
                return ir.DataTypeInt(bits=1)
            elif isinstance(expr.value, int):
                return ir.DataTypeInt(bits=32)  # Default to 32-bit
        elif isinstance(expr, ir.ExprAttribute):
            # Bundle field access
            base_expr = expr.value
            if isinstance(base_expr, ir.ExprRefField) and isinstance(base_expr.base, ir.TypeExprRefSelf):
                if base_expr.index < len(comp.fields):
                    bundle_field = comp.fields[base_expr.index]
                    if isinstance(bundle_field.datatype, ir.DataTypeRef):
                        # Look up bundle struct
                        ref_type = self._ctxt.type_m.get(bundle_field.datatype.ref_name)
                        if isinstance(ref_type, ir.DataTypeStruct):
                            # Find the field in the bundle
                            for fld in ref_type.fields:
                                if fld.name == expr.attr:
                                    return fld.datatype
        
        # Default fallback
        return ir.DataTypeInt(bits=32)
    
    def _infer_signal_type_from_name(self, signal_name: str) -> str:
        """Infer SystemVerilog type from signal name conventions."""
        # Error, request, acknowledge, enable signals are typically 1-bit
        if any(keyword in signal_name for keyword in ['err', '_we', '_req', '_ack', 'cyc', 'stb']):
            return "logic"
        # State machines are typically small integers
        elif 'state' in signal_name:
            return "logic [7:0]"
        # Sel (byte enable) is typically 4 bits for 32-bit data
        elif 'sel' in signal_name:
            return "logic [3:0]"
        # Data and address signals are typically 32 bits
        elif any(keyword in signal_name for keyword in ['dat', 'adr', 'data', 'addr']):
            return "logic [31:0]"
        # Clock and reset
        elif signal_name in ['clock', 'reset']:
            return "logic"
        # Default
        return "logic [31:0]"
    
    def _infer_bundle_signal_type(self, signal_name: str, comp: ir.DataTypeComponent) -> str:
        """Infer type for bundle flattened signal like init_cyc, init_adr."""
        if '_' not in signal_name:
            return None
        
        parts = signal_name.split('_', 1)
        bundle_name = parts[0]
        field_name = parts[1]
        
        # Find bundle field
        for fld in comp.fields:
            if fld.name == bundle_name and isinstance(fld.datatype, ir.DataTypeRef):
                # Get bundle struct type
                ref_type = self._ctxt.type_m.get(fld.datatype.ref_name)
                if isinstance(ref_type, ir.DataTypeStruct):
                    # Find the field in the bundle
                    for bundle_fld in ref_type.fields:
                        if bundle_fld.name == field_name:
                            if isinstance(bundle_fld.datatype, ir.DataTypeInt):
                                return self._get_sv_type(bundle_fld.datatype)
                            elif type(bundle_fld.datatype) == ir.DataType:
                                # Bare type - infer from field name
                                return self._infer_signal_type_from_name(field_name)
        
        # Fallback to name-based inference
        return self._infer_signal_type_from_name(field_name)
    
    def _generate_interface_task(self, func: ir.Function, comp: ir.DataTypeComponent) -> List[str]:
        """Generate a SystemVerilog task for an interface method."""
        lines = []
        
        # Add source location annotation if debug mode is enabled
        if self.debug_annotations and func.loc:
            lines.append(f"  // Source: {func.loc.file}:{func.loc.line}")
        
        # Task signature
        params = []
        
        # Return value as output parameter if function has return
        # Check if we have a tuple return by looking at the return statement
        is_tuple_return = False
        tuple_elements = []
        
        if func.returns and func.returns is not None:
            # Check return statements in function body for tuple
            for stmt in func.body:
                if isinstance(stmt, ir.StmtReturn) and stmt.value:
                    from zuspec.dataclasses.ir.expr_phase2 import ExprTuple
                    if isinstance(stmt.value, ExprTuple):
                        is_tuple_return = True
                        # Get type of each tuple element
                        for elem_expr in stmt.value.elts:
                            elem_type = self._infer_expr_type(elem_expr, comp)
                            tuple_elements.append(elem_type)
                        break
        
        # Input parameters first (conventional SV ordering)
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
        
        # Output parameters last (return values)
        if is_tuple_return:
            # Create output parameters for each tuple element
            for i, elem_type in enumerate(tuple_elements):
                elem_sv_type = self._get_sv_type(elem_type)
                params.append(f"output {elem_sv_type} __ret_{i}")
        elif func.returns and func.returns is not None:
            # Single return value
            return_type = self._get_sv_type(func.returns)
            # TODO: Fix data_model_factory to properly extract return type width
            # For now, if return type is generic "logic" (bits=-1), assume 32-bit
            if return_type == "logic" and isinstance(func.returns, ir.DataTypeInt) and func.returns.bits == -1:
                return_type = "logic [31:0]"
            params.append(f"output {return_type} __ret")
        
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
            # Return statement - assign to __ret or unpack tuple to __ret_0, __ret_1, ...
            if stmt.value:
                from zuspec.dataclasses.ir.expr_phase2 import ExprTuple
                if isinstance(stmt.value, ExprTuple):
                    # Tuple return - unpack to individual output parameters
                    for i, elem_expr in enumerate(stmt.value.elts):
                        elem_sv_expr = self._generate_expr(elem_expr, comp)
                        lines.append(f"{ind}__ret_{i} = {elem_sv_expr};")
                else:
                    # Single return value
                    value_expr = self._generate_expr(stmt.value, comp)
                    lines.append(f"{ind}__ret = {value_expr};")
        
        else:
            # For other statements, try to use existing generator
            lines.extend(self._generate_stmt(stmt, comp, indent))
        
        return lines

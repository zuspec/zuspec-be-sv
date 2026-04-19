"""CLI plugin for zuspec-be-sv.

Registers :class:`RTLSVBackend` with the zuspec-cli
:class:`~zuspec.cli.Registry`.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import List, Tuple, TYPE_CHECKING

from zuspec.cli.plugin import Plugin
from zuspec.cli.backend import Backend
from zuspec.cli.ir import IR

if TYPE_CHECKING:
    from zuspec.cli.registry import Registry


def _wrap_module(cc, body_lines: List[str], module_name: str, prefix: str) -> str:
    """Wrap ``cc.emit_sv()`` body lines in a self-contained SV module.

    Args:
        cc:          A ``ConstraintCompiler`` that has been through ``minimize()``.
        body_lines:  Lines returned by ``cc.emit_sv()``.
        module_name: SV module name.
        prefix:      Wire-name prefix used inside the module body (e.g. ``'d'``).

    Returns:
        Complete SystemVerilog module text.
    """
    cset = cc.cset
    internal: set = set(getattr(cset, "internal_fields", []))

    input_ports: List[Tuple[str, int]] = [(cset.input_field, cset.input_width)]
    output_ports: List[Tuple[str, int]] = [
        (f.name, f.width)
        for f in cset.output_fields
        if f.name not in internal
    ]

    def _port_decl(
        ins: List[Tuple[str, int]], outs: List[Tuple[str, int]]
    ) -> str:
        lines = []
        for name, w in ins:
            if w == 1:
                lines.append(f"    input  logic        {name}")
            else:
                lines.append(f"    input  logic [{w-1}:0]  {name}")
        for name, w in outs:
            if w == 1:
                lines.append(f"    output logic        {name}")
            else:
                lines.append(f"    output logic [{w-1}:0]  {name}")
        return ",\n".join(lines)

    sv_lines = [
        f"// {module_name} — constraint-synthesized by zuspec-cli",
        "",
        f"module {module_name} (",
        _port_decl(input_ports, output_ports),
        ");",
        "",
    ]
    # When a prefix is used, emit_sv() references the input as `{prefix}_{field}`.
    # Bridge the port name to the prefixed internal name with a wire alias.
    if prefix and input_ports:
        in_name, in_width = input_ports[0]
        if in_width == 1:
            sv_lines.append(f"    wire {prefix}_{in_name} = {in_name};")
        else:
            sv_lines.append(
                f"    wire [{in_width-1}:0] {prefix}_{in_name} = {in_name};"
            )
        sv_lines.append("")
    for line in body_lines:
        sv_lines.append(f"    {line}")
    sv_lines.append("")
    sv_lines.append("    // Connect internal wires to output ports.")
    for name, _ in output_ports:
        if prefix:
            sv_lines.append(f"    assign {name} = {prefix}_{name};")
        else:
            sv_lines.append(f"    assign {name} = _{name};")
    sv_lines.extend(["", "endmodule", ""])

    return "\n".join(sv_lines)


class RTLSVBackend(Backend):
    """Emit a combinational SystemVerilog module from a ``ConstraintCompiler`` IR.

    Expects an :class:`~zuspec.cli.IR` of kind
    ``'zuspec.constraint.compiler'`` whose payload is a ``ConstraintCompiler``
    that has already been through ``minimize()``.
    """

    @property
    def name(self) -> str:
        return "rtl-sv"

    @property
    def description(self) -> str:
        return "Combinational RTL (SystemVerilog wire/assign)"

    @property
    def requires_ir_kind(self) -> str:
        return "zuspec.constraint.compiler"

    def add_args(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--module-name",
            default=None,
            metavar="NAME",
            help="Module name in emitted SV (default: derived from --top)",
        )

    def emit(self, ir: IR, args: argparse.Namespace) -> None:
        cc = ir.payload

        module_name = getattr(args, "module_name", None) or None
        if not module_name:
            top = getattr(args, "top", "unknown")
            module_name = top.replace(":", "_").replace(".", "_")

        prefix = getattr(args, "be_prefix", "d") or "d"
        body_lines = cc.emit_sv()
        sv_text = _wrap_module(cc, body_lines, module_name, prefix)

        output = getattr(args, "output", "-")
        if output == "-":
            print(sv_text, end="")
        else:
            out_path = Path(output)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(sv_text)


class SVBackendPlugin(Plugin):
    """Plugin that registers :class:`RTLSVBackend` for SV RTL emission."""

    @property
    def name(self) -> str:
        return "zuspec-be-sv"

    @property
    def description(self) -> str:
        return "SystemVerilog RTL back-end"

    def register(self, registry: "Registry") -> None:
        registry.add_backend(RTLSVBackend())

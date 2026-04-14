"""RTL IR — module-level structures for the generic RTL representation."""
from __future__ import annotations

import dataclasses as dc
import enum
from typing import Dict, List, Optional

from zuspec.be.sv.ir.rtl_expr import RTLExpr


class PortDirection(enum.Enum):
    """Direction of an RTL module port."""

    INPUT = "input"
    OUTPUT = "output"
    INOUT = "inout"


@dc.dataclass
class RTLPort:
    """A module port (input, output, or inout).

    Args:
        name: Port name.
        width: Bit-width.
        direction: ``PortDirection`` enum value.
    """

    name: str = dc.field()
    width: int = dc.field(default=1)
    direction: PortDirection = dc.field(default=PortDirection.INPUT)


@dc.dataclass
class RTLWire:
    """An internal wire declaration.

    Args:
        name: Wire name.
        width: Bit-width.
        dtype: Optional typedef type name (e.g. ``"fetch_pld_t"``).
            When set, overrides the ``logic [width-1:0]`` default declaration.
    """

    name: str = dc.field()
    width: int = dc.field(default=1)
    dtype: Optional[str] = dc.field(default=None)


@dc.dataclass
class RTLAssign:
    """A continuous assignment ``assign lhs = rhs``.

    Args:
        lhs: Left-hand-side expression (typically ``RTLIdent``).
        rhs: Right-hand-side expression.
    """

    lhs: RTLExpr = dc.field()
    rhs: RTLExpr = dc.field()


@dc.dataclass
class RTLAlways:
    """A procedural ``always`` block.

    Args:
        sensitivity: List of sensitivity strings (e.g. ``["posedge clk"]``).
            An empty list means ``always_comb``.
        body: List of statement strings or nested IR objects representing the block body.
    """

    sensitivity: List[str] = dc.field(default_factory=list)
    body: List = dc.field(default_factory=list)


@dc.dataclass
class RTLInstance:
    """An instantiation of a sub-module.

    Args:
        module_name: Name of the module being instantiated.
        inst_name: Instance name.
        port_map: Mapping from port name to connection expression.
    """

    module_name: str = dc.field()
    inst_name: str = dc.field()
    port_map: Dict[str, RTLExpr] = dc.field(default_factory=dict)


@dc.dataclass
class RTLModule:
    """A complete RTL module.

    Args:
        name: Module name.
        ports: Ordered list of port declarations.
        wires: Internal wire declarations.
        assigns: Continuous assignment statements.
        always_blocks: Procedural always blocks.
        instances: Sub-module instantiations.
    """

    name: str = dc.field()
    ports: List[RTLPort] = dc.field(default_factory=list)
    wires: List[RTLWire] = dc.field(default_factory=list)
    assigns: List[RTLAssign] = dc.field(default_factory=list)
    always_blocks: List[RTLAlways] = dc.field(default_factory=list)
    instances: List[RTLInstance] = dc.field(default_factory=list)


@dc.dataclass
class RTLRawModule:
    """A module represented as raw SystemVerilog text lines (escape hatch).

    Used during incremental migration when a module's body is too complex to
    represent with the structured ``RTLModule`` IR.  ``RTLEmitter`` emits the
    lines verbatim.

    Args:
        name: Module name (informational; not re-emitted by ``RTLEmitter``).
        lines: Raw SV text lines (each element is one SV source line; no
            trailing newline on individual lines).
    """

    name: str = dc.field()
    lines: List[str] = dc.field(default_factory=list)

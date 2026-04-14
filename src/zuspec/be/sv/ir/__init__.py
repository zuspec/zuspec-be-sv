"""zuspec.be.sv.ir — SystemVerilog IR and emitter.

This package contains two complementary IR families:

**RTL IR** (``rtl.py``, ``rtl_expr.py``, ``rtl_emit.py``)
    Synthesisable RTL module structure: ports, wires, continuous
    assignments, always blocks, sub-module instances.  Produced by
    synthesis passes and serialised by ``RTLEmitter``.

**SV IR** (``sv.py``, ``sv_emit.py``)
    Full-spectrum SystemVerilog constructs: packages, typedefs (struct /
    enum / union), classes, interfaces.  Serialised by ``SVEmitter``,
    which also delegates RTL IR nodes to ``RTLEmitter`` so that any mix
    of IR types can be passed to a single ``emit_all()`` call.
"""

from .rtl_expr import (
    RTLBinop,
    RTLCase,
    RTLCaseItem,
    RTLConcat,
    RTLExpr,
    RTLIdent,
    RTLLiteral,
    RTLRawExpr,
    RTLSlice,
    RTLTernary,
    RTLUnop,
)
from .rtl import (
    PortDirection,
    RTLAlways,
    RTLAssign,
    RTLInstance,
    RTLModule,
    RTLPort,
    RTLRawModule,
    RTLWire,
)
from .rtl_emit import RTLEmitter
from .sv import (
    SVClass,
    SVField,
    SVInterface,
    SVPackage,
    SVRawItem,
    SVTypedefEnum,
    SVTypedefStruct,
    SVTypedefUnion,
)
from .sv_emit import SVEmitter

__all__ = [
    # RTL expr
    "RTLExpr", "RTLRawExpr", "RTLLiteral", "RTLIdent", "RTLSlice",
    "RTLConcat", "RTLBinop", "RTLUnop", "RTLTernary", "RTLCase", "RTLCaseItem",
    # RTL IR
    "PortDirection", "RTLPort", "RTLWire", "RTLAssign", "RTLAlways",
    "RTLInstance", "RTLModule", "RTLRawModule",
    # RTL emitter
    "RTLEmitter",
    # SV IR
    "SVField", "SVTypedefStruct", "SVTypedefEnum", "SVTypedefUnion",
    "SVPackage", "SVRawItem", "SVClass", "SVInterface",
    # SV emitter
    "SVEmitter",
]

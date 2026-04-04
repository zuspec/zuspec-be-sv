"""RTLExpr — expression hierarchy for the generic RTL IR."""
from __future__ import annotations

import dataclasses as dc
from typing import List, Optional, Tuple


@dc.dataclass
class RTLExpr:
    """Abstract base for all RTL expressions."""


@dc.dataclass
class RTLLiteral(RTLExpr):
    """An integer literal.

    Args:
        value: Integer value.
        width: Bit-width context (0 = unspecified).
    """

    value: int = dc.field()
    width: int = dc.field(default=0)


@dc.dataclass
class RTLIdent(RTLExpr):
    """A bare identifier (wire or port name).

    Args:
        name: Signal or wire name.
    """

    name: str = dc.field()


@dc.dataclass
class RTLSlice(RTLExpr):
    """A bit-slice ``base[hi:lo]``.

    Args:
        base: The expression being sliced.
        hi: Upper bit index (inclusive).
        lo: Lower bit index (inclusive).
    """

    base: RTLExpr = dc.field()
    hi: int = dc.field()
    lo: int = dc.field()


@dc.dataclass
class RTLConcat(RTLExpr):
    """Concatenation ``{parts[0], parts[1], ...}``.

    Args:
        parts: Expressions to concatenate (MSB first).
    """

    parts: List[RTLExpr] = dc.field(default_factory=list)


@dc.dataclass
class RTLBinop(RTLExpr):
    """A binary operation ``lhs op rhs``.

    Args:
        op: Operator string (e.g. ``"+"``, ``"&"``, ``"=="``).
        lhs: Left operand.
        rhs: Right operand.
    """

    op: str = dc.field()
    lhs: RTLExpr = dc.field()
    rhs: RTLExpr = dc.field()


@dc.dataclass
class RTLUnop(RTLExpr):
    """A unary operation ``op operand``.

    Args:
        op: Operator string (e.g. ``"~"``, ``"!"``, ``"-"``).
        operand: The expression.
    """

    op: str = dc.field()
    operand: RTLExpr = dc.field()


@dc.dataclass
class RTLTernary(RTLExpr):
    """Conditional expression ``cond ? then_ : else_``.

    Args:
        cond: Boolean condition.
        then_: Value when true.
        else_: Value when false.
    """

    cond: RTLExpr = dc.field()
    then_: RTLExpr = dc.field()
    else_: RTLExpr = dc.field()


@dc.dataclass
class RTLCaseItem:
    """One arm of an ``RTLCase`` expression.

    Args:
        matches: List of match expressions (``None`` = default arm).
        body: The expression selected when any match fires.
    """

    matches: List[Optional[RTLExpr]] = dc.field(default_factory=list)
    body: RTLExpr = dc.field(default=None)  # type: ignore[assignment]


@dc.dataclass
class RTLCase(RTLExpr):
    """A ``case`` expression.

    Args:
        sel: Selector expression.
        items: Ordered list of case arms.
    """

    sel: RTLExpr = dc.field()
    items: List[RTLCaseItem] = dc.field(default_factory=list)


@dc.dataclass
class RTLRawExpr(RTLExpr):
    """A raw SystemVerilog expression string (escape hatch for content not yet in the IR).

    Use this when the expression is too complex to represent with the current
    ``RTLExpr`` hierarchy.  The ``sv`` string is emitted verbatim by
    ``RTLEmitter.emit_expr()``.

    Args:
        sv: The raw SystemVerilog expression text.
    """

    sv: str = dc.field()

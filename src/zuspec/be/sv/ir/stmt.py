"""Structured SV procedural-statement IR (plan task C3).

These nodes let task/function bodies be represented structurally instead of as
pre-rendered ``body_lines: List[str]``. Expression-valued fields hold
``zuspec.ir.core`` ``Expr`` nodes, rendered via :class:`SVExprEmitter` (D4), so
there is a single expression-rendering path across constraints and procedural
code.

These are be-sv SV-IR nodes (plain dataclasses), not core IR. ``SVStmtRaw``
remains as an escape hatch for constructs not yet modeled.
"""
from __future__ import annotations

import dataclasses as dc
from typing import Any, List, Optional


@dc.dataclass
class SVStmt:
    """Base class for a structured SV statement."""
    pass


@dc.dataclass
class SVStmtRaw(SVStmt):
    """A verbatim SV statement line (escape hatch)."""
    text: str = dc.field()


@dc.dataclass
class SVStmtComment(SVStmt):
    """A ``// ...`` comment line."""
    text: str = dc.field()


@dc.dataclass
class SVVarDecl(SVStmt):
    """A local variable declaration: ``<dtype> <name> [= <init>];``."""
    name: str = dc.field()
    dtype: str = dc.field(default="int")
    init: Optional[Any] = dc.field(default=None)  # core Expr


@dc.dataclass
class SVStmtExpr(SVStmt):
    """An expression statement: ``<expr>;`` (e.g. a call)."""
    expr: Any = dc.field()  # core Expr


@dc.dataclass
class SVStmtAssign(SVStmt):
    """An assignment: ``<lhs> <op> <rhs>;`` (op = ``=`` / ``<=`` / ``+=`` ...)."""
    lhs: Any = dc.field()  # core Expr
    rhs: Any = dc.field()  # core Expr
    op: str = dc.field(default="=")


@dc.dataclass
class SVStmtReturn(SVStmt):
    """``return [<value>];``."""
    value: Optional[Any] = dc.field(default=None)  # core Expr


@dc.dataclass
class SVStmtIf(SVStmt):
    """``if (<cond>) begin ... end [ else begin ... end ]``."""
    cond: Any = dc.field()  # core Expr
    then_body: List[SVStmt] = dc.field(default_factory=list)
    else_body: List[SVStmt] = dc.field(default_factory=list)


@dc.dataclass
class SVStmtFor(SVStmt):
    """Counted loop: ``for (<dtype> <var> = <start>; <var> < <limit>; <var>++)``."""
    var: str = dc.field()
    limit: Any = dc.field()              # core Expr
    body: List[SVStmt] = dc.field(default_factory=list)
    start: Optional[Any] = dc.field(default=None)  # core Expr; None => 0
    dtype: str = dc.field(default="int")


@dc.dataclass
class SVStmtForeach(SVStmt):
    """``foreach (<array>[<index_var>]) begin ... end``."""
    array: Any = dc.field()  # core Expr
    index_var: str = dc.field()
    body: List[SVStmt] = dc.field(default_factory=list)


@dc.dataclass
class SVStmtWhile(SVStmt):
    """``while (<cond>) begin ... end``."""
    cond: Any = dc.field()  # core Expr
    body: List[SVStmt] = dc.field(default_factory=list)


@dc.dataclass
class SVStmtRepeat(SVStmt):
    """``repeat (<count>) begin ... end``."""
    count: Any = dc.field()  # core Expr
    body: List[SVStmt] = dc.field(default_factory=list)


@dc.dataclass
class SVStmtFork(SVStmt):
    """``fork <branch> ... join[_any|_none]`` — each branch is a statement list
    run in its own process (plan task C6)."""
    branches: List[List[SVStmt]] = dc.field(default_factory=list)
    join: str = dc.field(default="join")  # "join" | "join_any" | "join_none"


@dc.dataclass
class SVStmtRandomize(SVStmt):
    """A ``randomize()`` call, optionally with inline constraints and a
    fail-check: ``if (!<target>.randomize() with { ... }) $fatal(1, msg);``.

    ``target`` is the object expression (core ``Expr``). ``constraints`` are core
    ``Constraint`` items rendered into the ``with { ... }`` body (plan task C4).
    """
    target: Any = dc.field()  # core Expr
    constraints: List[Any] = dc.field(default_factory=list)  # core Constraint items
    fail_msg: Optional[str] = dc.field(default=None)
    check: bool = dc.field(default=True)


@dc.dataclass
class SVCaseItem:
    """One arm of a case statement. Empty ``labels`` => ``default``."""
    labels: List[Any] = dc.field(default_factory=list)  # core Exprs
    body: List[SVStmt] = dc.field(default_factory=list)


@dc.dataclass
class SVStmtCase(SVStmt):
    """``case (<subject>) <labels>: begin ... end ... endcase``."""
    subject: Any = dc.field()  # core Expr
    items: List[SVCaseItem] = dc.field(default_factory=list)

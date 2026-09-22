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
"""Emit a SystemVerilog rand class from a zuspec dataclass.

Translates ``@zdc.dataclass`` definitions into SV ``class``/``endclass``
declarations with ``rand`` field declarations and ``constraint`` blocks.
No benchmark-specific knowledge (DPI timing, module harness) lives here.
"""
import ast
import copy
import inspect
import math
from typing import Any, Dict, List, Optional, Tuple


class _ConstraintExpander:
    """Flatten one constraint method's body into parsed constraint exprs.

    ``ConstraintParser`` handles a body made only of ``assert``/``if``/
    expression statements and silently skips anything else, so a constraint
    built with a loop -- ``for j in range(i + 1, _N): assert vi != getattr(
    self, f"v{j}")`` -- came back with no expressions at all, and the emitted
    class was unconstrained. Free names were emitted verbatim too, so a
    module constant such as ``_ADDR_TOP`` reached SV as an undeclared
    identifier.

    This expands the body first, against the method's own constant
    environment (module globals, closure variables, loop indices):

      * ``for <name> in <iterable>`` with an iterable computable from that
        environment (``range(...)``, a tuple/list of constants) is unrolled;
      * ``<name> = <expr>`` binds a local, substituted where it is used;
      * ``getattr(self, <str>)`` with a computable name becomes ``self.<str>``;
      * a name bound to an integer constant becomes that constant.

    Each resulting ``assert``/``if``/expression statement is then handed to
    ``ConstraintParser`` exactly as before. Anything else raises
    ``NotImplementedError``: an SV class missing a constraint is worse than
    no SV class.
    """

    _EVAL_BUILTINS = {"range": range, "len": len, "min": min, "max": max,
                      "abs": abs}

    def __init__(self, method, parser, cls_name: str):
        self._parser = parser
        self._where = "%s.%s" % (cls_name, getattr(method, "__name__", "?"))
        fn = inspect.unwrap(method)
        env: Dict[str, Any] = {}
        try:
            cv = inspect.getclosurevars(fn)
            env.update(cv.globals)
            env.update(cv.nonlocals)
        except (TypeError, ValueError):
            pass
        g = getattr(fn, "__globals__", {})
        # Only integer constants are substituted; any other name is left for
        # the emitter to reject rather than turned into something plausible.
        self._env = {k: v for k, v in {**g, **env}.items() if _is_int(v)}

    def expand(self, func_def: ast.FunctionDef) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        self._stmts(func_def.body, {}, out)
        return out

    # -- statements ------------------------------------------------------

    def _stmts(self, body, local: Dict[str, ast.expr], out: list) -> None:
        for stmt in body:
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) \
                    and isinstance(stmt.value.value, str):
                continue                                    # docstring
            if isinstance(stmt, ast.Pass):
                continue
            if isinstance(stmt, ast.Assert):
                out.append(self._parse(stmt.test, local))
            elif isinstance(stmt, ast.Expr):
                out.append(self._parse(stmt.value, local))
            elif isinstance(stmt, ast.If):
                if stmt.orelse:
                    raise NotImplementedError(
                        "%s: else branches in constraint if-statements are "
                        "not supported" % self._where)
                cons: list = []
                self._stmts(stmt.body, dict(local), cons)
                out.append({"type": "implies",
                            "antecedent": self._parse(stmt.test, local),
                            "consequent": cons})
            elif isinstance(stmt, ast.For) and isinstance(stmt.target, ast.Name) \
                    and not stmt.orelse:
                for v in self._eval(stmt.iter, local):
                    if not _is_int(v):
                        raise NotImplementedError(
                            "%s: loop over non-integer value %r" % (self._where, v))
                    inner = dict(local)
                    inner[stmt.target.id] = ast.Constant(v)
                    self._stmts(stmt.body, inner, out)
            elif isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 \
                    and isinstance(stmt.targets[0], ast.Name):
                local[stmt.targets[0].id] = self._subst(stmt.value, local)
            else:
                raise NotImplementedError(
                    "%s: cannot express %s statement (line %s) in SV"
                    % (self._where, type(stmt).__name__,
                       getattr(stmt, "lineno", "?")))

    # -- expressions -----------------------------------------------------

    def _parse(self, node: ast.expr, local) -> Dict[str, Any]:
        return self._parser.parse_expr(self._subst(node, local))

    def _subst(self, node: ast.expr, local) -> ast.expr:
        expander = self

        class _T(ast.NodeTransformer):
            def visit_Name(self, n):
                if n.id in local:
                    return copy.deepcopy(local[n.id])
                if n.id != "self" and n.id in expander._env:
                    return ast.copy_location(
                        ast.Constant(int(expander._env[n.id])), n)
                return n

            def visit_Call(self, n):
                self.generic_visit(n)
                if isinstance(n.func, ast.Name) and n.func.id == "getattr" \
                        and len(n.args) == 2 and isinstance(n.args[0], ast.Name) \
                        and n.args[0].id == "self":
                    attr = expander._eval(n.args[1], {})
                    if not isinstance(attr, str):
                        raise NotImplementedError(
                            "%s: getattr name %r is not a string"
                            % (expander._where, attr))
                    return ast.copy_location(
                        ast.Attribute(ast.Name("self", ast.Load()), attr,
                                      ast.Load()), n)
                return n

        return _T().visit(copy.deepcopy(node))

    def _eval(self, node: ast.expr, local):
        """Evaluate a compile-time expression (loop bounds, attribute names)."""
        node = self._subst(node, local)
        expr = ast.fix_missing_locations(ast.Expression(node))
        try:
            return eval(compile(expr, "<constraint %s>" % self._where, "eval"),
                        {"__builtins__": self._EVAL_BUILTINS}, dict(self._env))
        except Exception as e:
            raise NotImplementedError(
                "%s: cannot evaluate %s at generation time (%s)"
                % (self._where, ast.unparse(node), e)) from e


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


class SVRandClassEmitter:
    """Translate a zuspec dataclass into a SV rand class declaration.

    Usage::

        emitter = SVRandClassEmitter()
        sv_text = emitter.emit_class(MemTransaction)
        # sv_text is the 'class ... endclass' string, ready to include in SV

    The emitter reads field metadata via ``zdc.extract_rand_fields()`` and
    parses constraint method bodies via ``zdc.ConstraintParser``.
    """

    # Map Python bin_op IR operators to SV equivalents
    _BINOP_MAP: Dict[str, str] = {
        '+': '+', '-': '-', '*': '*', '/': '/',
        '%': '%', '//': '/', '<<': '<<', '>>': '>>',
        '|': '|', '^': '^', '&': '&',
    }

    # Map Python compare IR operators to SV equivalents
    _CMPOP_MAP: Dict[str, str] = {
        '==': '==', '!=': '!=',
        '<': '<', '<=': '<=', '>': '>', '>=': '>=',
    }

    def emit_class(self, cls: type) -> str:
        """Emit a SV rand class declaration for *cls*.

        Returns a string starting with ``// AUTO-GENERATED`` and ending with
        ``endclass``.  Does **not** include DPI imports or module wrappers.
        """
        import zuspec.dataclasses as zdc

        fields = zdc.extract_rand_fields(cls)
        cp = zdc.ConstraintParser()
        constraints = cp.extract_constraints(cls)

        lines: List[str] = []
        lines.append(f"// AUTO-GENERATED by SVRandClassEmitter — do not edit")
        lines.append(f"// Source: {cls.__module__} :: {cls.__name__}")
        lines.append("")
        lines.append(f"class {cls.__name__};")

        # Field declarations
        for field in fields:
            decl = self._field_decl(field)
            lines.append(f"  {decl}")

        # Domain constraints for list-domain fields (emitted as separate constraint blocks)
        domain_constraints: List[str] = []
        for field in fields:
            domain = field.get('domain')
            if domain is not None and isinstance(domain, (list, tuple)):
                if not isinstance(domain, tuple) or len(domain) != 2:
                    # List domain → inside {v0, v1, ...}
                    vals = ', '.join(self._sv_literal(v) for v in domain)
                    domain_constraints.append(
                        f"  constraint c_{field['name']}_domain "
                        f"{{ {field['name']} inside {{{vals}}}; }}"
                    )
                else:
                    lo, hi = domain
                    sv_lo = self._sv_literal(lo)
                    sv_hi = self._sv_literal(hi)
                    domain_constraints.append(
                        f"  constraint c_{field['name']}_domain "
                        f"{{ {field['name']} inside {{[{sv_lo}:{sv_hi}]}}; }}"
                    )

        if domain_constraints:
            lines.append("")
            lines.extend(domain_constraints)

        # User-defined constraint blocks
        for c in constraints:
            exprs = _ConstraintExpander(c['method'], cp, cls.__name__).expand(
                c['ast'])
            if not exprs:
                continue
            lines.append("")
            lines.append(f"  constraint {c['name']} {{")
            for expr in exprs:
                sv_expr = self._emit_expr(expr)
                lines.append(f"    {sv_expr};")
            lines.append("  }")

        lines.append("endclass")
        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------------
    # Field width & declaration helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _sv_literal(v: int) -> str:
        """Return a SV integer literal for *v*, sized when needed.

        SV unsized literals default to 32 bits.  Values that don't fit in
        32 bits need an explicit size prefix (e.g. ``64'd1099511627775``).
        """
        if not isinstance(v, int) or isinstance(v, bool):
            return str(v)
        if abs(v) > 0xFFFF_FFFF:
            n_bits = max(64, v.bit_length() + (1 if v < 0 else 0))
            # round up to power-of-2 width (32, 64, 128 ...)
            pw = 1 << (n_bits - 1).bit_length()
            prefix = "-" if v < 0 else ""
            return f"{prefix}{pw}'d{abs(v)}"
        return str(v)

    def _bits_for_domain(self, domain) -> int:
        """Return the minimum bit width to represent all values in *domain*."""
        if isinstance(domain, tuple) and len(domain) == 2:
            lo, hi = domain
            if hi < 0:
                # Signed; not common for rand fields but handle gracefully
                return max(1, math.ceil(math.log2(abs(hi) + 1)) + 1)
            return max(1, math.ceil(math.log2(hi + 1)) if hi > 0 else 1)
        else:
            # List domain
            max_val = max(abs(v) for v in domain) if domain else 0
            return max(1, math.ceil(math.log2(max_val + 1)) if max_val > 0 else 1)

    def _field_decl(self, field: Dict[str, Any]) -> str:
        """Return SV field declaration string, e.g. ``rand bit [7:0] addr;``"""
        name = field['name']
        kind = field.get('kind', 'rand')
        rand_kw = 'randc' if kind == 'randc' else 'rand'
        domain = field.get('domain')
        if domain is not None:
            bits = self._bits_for_domain(domain)
        else:
            bits = 32  # default width when no domain is given

        if bits == 1:
            return f"{rand_kw} bit {name};"
        return f"{rand_kw} bit [{bits - 1}:0] {name};"

    # ------------------------------------------------------------------
    # Expression IR → SV string
    # ------------------------------------------------------------------

    def _emit_expr(self, node: Dict[str, Any]) -> str:
        t = node.get('type')
        if t == 'constant':
            return self._sv_literal(node['value'])
        elif t == 'attribute':
            # Strip 'self.' prefix
            attr = node.get('attr', '')
            return attr
        elif t == 'name':
            # Constants were substituted by _ConstraintExpander; a name still
            # here would be an undeclared identifier in SV.
            raise ValueError(
                "SVRandClassEmitter: cannot resolve name '%s' to a field or "
                "an integer constant" % node.get('id', ''))
        elif t == 'bin_op':
            op = self._BINOP_MAP.get(node['op'], node['op'])
            left = self._emit_expr(node['left'])
            right = self._emit_expr(node['right'])
            return f"({left} {op} {right})"
        elif t == 'compare':
            return self._emit_compare(node)
        elif t == 'bool_op':
            sv_op = '&&' if node['op'] == 'and' else '||'
            parts = [self._emit_expr(v) for v in node['values']]
            joined = f" {sv_op} ".join(parts)
            return f"({joined})"
        elif t == 'unary_op':
            op = '!' if node['op'] == 'not' else node['op']
            return f"({op}{self._emit_expr(node['operand'])})"
        elif t == 'implies':
            ante = self._emit_expr(node['antecedent'])
            # implies(a, b) carries one consequent; an if-statement carries
            # the list of its body's constraints.
            c = node['consequent']
            parts = c if isinstance(c, list) else [c]
            if not parts:
                return "1"
            cons = " && ".join(f"({self._emit_expr(p)})" for p in parts)
            return f"({ante}) -> ({cons})"
        else:
            raise ValueError(f"SVRandClassEmitter: unsupported IR node type '{t}'")

    def _emit_compare(self, node: Dict[str, Any]) -> str:
        ops = node['ops']
        comparators = node['comparators']
        left_expr = self._emit_expr(node['left'])

        if len(ops) == 1 and ops[0] == 'in':
            return self._emit_in(left_expr, comparators[0])
        if len(ops) == 1 and ops[0] == 'not_in':
            return f"!({self._emit_in(left_expr, comparators[0])})"

        # Simple comparison or chained comparison (a < b < c → a < b && b < c)
        if len(ops) == 1:
            sv_op = self._CMPOP_MAP.get(ops[0], ops[0])
            right = self._emit_expr(comparators[0])
            return f"{left_expr} {sv_op} {right}"

        # Chained: a op0 b op1 c  →  (a op0 b) && (b op1 c)
        parts: List[str] = []
        operands = [node['left']] + comparators
        for i, op in enumerate(ops):
            sv_op = self._CMPOP_MAP.get(op, op)
            l = self._emit_expr(operands[i])
            r = self._emit_expr(operands[i + 1])
            parts.append(f"{l} {sv_op} {r}")
        return '(' + ' && '.join(parts) + ')'

    def _emit_in(self, left_expr: str, comparator: Dict[str, Any]) -> str:
        """Emit ``left inside {range_or_list}``."""
        t = comparator.get('type')
        if t == 'call' and comparator.get('func') == 'range':
            args = comparator.get('args', [])
            if len(args) == 2:
                lo = self._emit_expr(args[0])
                hi_val = self._emit_expr(args[1])
                return f"{left_expr} inside {{[{lo}:{hi_val}]}}"
        if t == 'list':
            elems = comparator.get('elements', [])
            vals = ', '.join(self._emit_expr(e) for e in elems)
            return f"{left_expr} inside {{{vals}}}"
        # Fallback: treat comparator as single value
        return f"{left_expr} == {self._emit_expr(comparator)}"

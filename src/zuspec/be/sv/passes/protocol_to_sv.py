"""ProtocolToSV — build be.sv IR for protocol library modules (unification U-3).

Moves protocol SV emission (FIFO/queue, arbiters, port decls) off the legacy
``zuspec.synth.sprtl.protocol_sv`` string generators onto structured ``zuspec.be.sv``
IR so ``SVEmitter`` is the sole SystemVerilog serialiser.  This is the first
*structural* migration of a non-FSM emitter (the FSM builder in ``fsm_to_sv`` is
the template).

Implements the synchronous FIFO (``build_fifo_sv``), the fixed-priority arbiter
(``build_priority_arbiter_sv``) and the round-robin arbiter
(``build_rr_arbiter_sv``); IfProtocol port declarations follow (U-3
continuation).  Each builder returns a list of be.sv IR nodes (``[RTLModule]``)
that ``SVEmitter.emit_all`` serialises.
"""
from __future__ import annotations

from typing import Any, List

import zuspec.ir.core as ir

from zuspec.be.sv.ir import stmt as s
from zuspec.be.sv.ir.rtl import (
    PortDirection,
    RTLAlways,
    RTLAssign,
    RTLMemory,
    RTLModule,
    RTLPort,
    RTLWire,
)
from zuspec.be.sv.ir.rtl_expr import RTLBinop, RTLIdent, RTLLiteral, RTLTernary


class ProtocolSVUnsupported(Exception):
    """Raised when a protocol construct isn't (yet) mapped to be.sv IR.

    The wiring pass catches this and falls back to the legacy generator for that
    node — e.g. a degenerate empty select, or a builder handed a select of the
    wrong kind (priority vs round-robin).
    """


# --------------------------------------------------------------------------- #
# small core-Expr / RTL-Expr helpers
# --------------------------------------------------------------------------- #

def _ref(name: str):
    return ir.ExprRefLocal(name=name)


def _lit(v: int):
    return ir.ExprConstant(value=v)


def _and(*terms):
    return ir.ExprBool(op=ir.BoolOp.And, values=list(terms))


def _not(x):
    return ir.ExprUnary(op=ir.UnaryOp.Not, operand=x)


def _bin(op, a, b):
    return ir.ExprBin(lhs=a, op=op, rhs=b)


def _sub(base: str, idx: str):
    return ir.ExprSubscript(value=_ref(base), slice=_ref(idx))


def _bit(base: str, i: int):
    """Constant bit-select ``base[i]``."""
    return ir.ExprSubscript(value=_ref(base), slice=_lit(i))


def _assign(lhs, rhs, op="<="):
    return s.SVStmtAssign(lhs=lhs, rhs=rhs, op=op)


# --------------------------------------------------------------------------- #
# synchronous FIFO
# --------------------------------------------------------------------------- #

def build_fifo_sv(q: Any, module_prefix: str = "") -> List[Any]:
    """Build be.sv IR for a synchronous FIFO from a ``QueueIR`` *q*.

    Structural port-for-port equivalent of
    ``sprtl/protocol_sv.py::generate_fifo_sv``: registered-output (fall-through)
    FIFO with ``wr_en``/``wr_data``/``full``/``rd_en``/``rd_data``/``empty``/
    ``count`` ports over a single-port synchronous memory, active-high sync reset.

    The legacy ``case ({wr_en&&!full, rd_en&&!empty})`` count update is emitted as
    the functionally-identical ``if/else-if`` chain (``10``→+1, ``01``→-1, else
    hold) because the core Expr set has no concatenation node.
    """
    name = f"{module_prefix}{q.name}_fifo"
    W, D = q.elem_width, q.depth
    A, C = q.addr_bits, q.count_bits

    ports = [
        RTLPort(name="clk", direction=PortDirection.INPUT),
        RTLPort(name="rst", direction=PortDirection.INPUT),
        RTLPort(name="wr_en", direction=PortDirection.INPUT),
        RTLPort(name="wr_data", width=W, direction=PortDirection.INPUT),
        RTLPort(name="full", direction=PortDirection.OUTPUT, net="logic"),
        RTLPort(name="rd_en", direction=PortDirection.INPUT),
        RTLPort(name="rd_data", width=W, direction=PortDirection.OUTPUT, net="logic"),
        RTLPort(name="empty", direction=PortDirection.OUTPUT, net="logic"),
        RTLPort(name="count", width=C, direction=PortDirection.OUTPUT, net="logic"),
    ]
    memories = [RTLMemory(name="mem", width=W, depth=D)]
    wires = [
        RTLWire(name="wr_ptr", width=A, net="logic"),
        RTLWire(name="rd_ptr", width=A, net="logic"),
        RTLWire(name="count_r", width=C, net="logic"),
    ]
    assigns = [
        RTLAssign(lhs=RTLIdent(name="full"),
                  rhs=RTLBinop(op="==", lhs=RTLIdent(name="count_r"),
                               rhs=RTLLiteral(value=D))),
        RTLAssign(lhs=RTLIdent(name="empty"),
                  rhs=RTLBinop(op="==", lhs=RTLIdent(name="count_r"),
                               rhs=RTLLiteral(value=0))),
        RTLAssign(lhs=RTLIdent(name="count"), rhs=RTLIdent(name="count_r")),
    ]

    do_wr = _and(_ref("wr_en"), _not(_ref("full")))
    do_rd = _and(_ref("rd_en"), _not(_ref("empty")))

    # Registered read (fall-through): if (rd_en && !empty) rd_data <= mem[rd_ptr];
    read_ff = RTLAlways(sensitivity=["posedge clk"], body=[
        s.SVStmtIf(cond=_and(_ref("rd_en"), _not(_ref("empty"))),
                   then_body=[_assign(_ref("rd_data"), _sub("mem", "rd_ptr"))]),
    ])

    # Write / pointer / count update with sync reset.
    write_ff = RTLAlways(sensitivity=["posedge clk"], body=[
        s.SVStmtIf(
            cond=_ref("rst"),
            then_body=[
                _assign(_ref("wr_ptr"), _lit(0)),
                _assign(_ref("rd_ptr"), _lit(0)),
                _assign(_ref("count_r"), _lit(0)),
            ],
            else_body=[
                s.SVStmtIf(cond=do_wr, then_body=[
                    _assign(_sub("mem", "wr_ptr"), _ref("wr_data")),
                    _assign(_ref("wr_ptr"),
                            _bin(ir.BinOp.Add, _ref("wr_ptr"), _lit(1))),
                ]),
                s.SVStmtIf(cond=do_rd, then_body=[
                    _assign(_ref("rd_ptr"),
                            _bin(ir.BinOp.Add, _ref("rd_ptr"), _lit(1))),
                ]),
                # count update: 10 -> +1, 01 -> -1, else hold (was case-on-concat)
                s.SVStmtIf(
                    cond=_and(do_wr, _not(do_rd)),
                    then_body=[_assign(_ref("count_r"),
                                       _bin(ir.BinOp.Add, _ref("count_r"), _lit(1)))],
                    else_body=[s.SVStmtIf(
                        cond=_and(_not(do_wr), do_rd),
                        then_body=[_assign(_ref("count_r"),
                                           _bin(ir.BinOp.Sub, _ref("count_r"), _lit(1)))])]),
            ]),
    ])

    return [RTLModule(name=name, ports=ports, wires=wires, memories=memories,
                      assigns=assigns, always_blocks=[read_ff, write_ff])]


# --------------------------------------------------------------------------- #
# fixed-priority arbiter
# --------------------------------------------------------------------------- #

def build_priority_arbiter_sv(sel: Any, module_prefix: str = "") -> List[Any]:
    """Build be.sv IR for a fixed-priority arbiter from a ``SelectIR`` *sel*.

    Structural equivalent of ``protocol_sv.generate_priority_arbiter_sv``: a
    combinational ``always @(*)`` that defaults ``gnt_o``/``sel_o``/``gnt_valid_o``
    to zero then grants the lowest-index requesting branch via an ``if/else-if``
    chain (branch 0 = highest priority).

    Raises :class:`ProtocolSVUnsupported` for a round-robin select (not yet
    structural) or an empty select — the caller falls back to legacy.
    """
    if getattr(sel, "round_robin", False):
        raise ProtocolSVUnsupported("round-robin arbiter not yet mapped to be.sv IR")
    n = len(sel.branches)
    if n == 0:
        raise ProtocolSVUnsupported("empty select — no arbiter module")

    name = f"{module_prefix}{sel.name}_arb"
    idx_bits = max(1, (n - 1).bit_length()) if n > 1 else 1

    ports = [
        RTLPort(name="req_i", width=n, direction=PortDirection.INPUT),
        RTLPort(name="gnt_o", width=n, direction=PortDirection.OUTPUT, net="logic"),
        RTLPort(name="sel_o", width=idx_bits, direction=PortDirection.OUTPUT, net="logic"),
        RTLPort(name="gnt_valid_o", direction=PortDirection.OUTPUT, net="logic"),
    ]

    # Fixed-priority if/else-if chain, built inner→outer so branch 0 is outermost.
    chain: List[s.SVStmt] = []
    for i in reversed(range(n)):
        terms = [_bit("req_i", i)] + [_not(_bit("req_i", j)) for j in range(i)]
        cond = _and(*terms) if len(terms) > 1 else terms[0]
        body = [
            _assign(_bit("gnt_o", i), ir.ExprConstant(value=True), op="="),
            _assign(_ref("sel_o"), _lit(sel.branches[i].tag_value), op="="),
            _assign(_ref("gnt_valid_o"), ir.ExprConstant(value=True), op="="),
        ]
        chain = [s.SVStmtIf(cond=cond, then_body=body, else_body=chain)]

    comb = RTLAlways(sensitivity=[], body=[
        _assign(_ref("gnt_o"), _lit(0), op="="),
        _assign(_ref("sel_o"), _lit(0), op="="),
        _assign(_ref("gnt_valid_o"), ir.ExprConstant(value=False), op="="),
        *chain,
    ])
    return [RTLModule(name=name, ports=ports, always_blocks=[comb])]


# --------------------------------------------------------------------------- #
# round-robin arbiter
# --------------------------------------------------------------------------- #

def build_rr_arbiter_sv(sel: Any, module_prefix: str = "") -> List[Any]:
    """Build be.sv IR for a round-robin arbiter from a ``SelectIR`` *sel*.

    Structural equivalent of ``protocol_sv.generate_rr_arbiter_sv``: a rotating
    priority mask (``mask_r``).  Two combinational priority encoders grant the
    lowest set bit of ``req_i & mask_r`` (masked) and of ``req_i`` (unmasked
    fallback); ``gnt_o`` picks the masked grant when non-zero.  A clocked block
    rotates the mask one position past the winner after each grant.

    Every SV construct is emitted structurally through ``SVEmitter``:
      * ``&`` / ``!=`` and the fall-back ternary as continuous ``RTLAssign``\\s,
      * the priority encoders / mask rotation as ``always`` blocks whose bodies
        are ``SVStmtFor`` / ``SVStmtIf`` over core ``Expr`` nodes.

    Functional notes (all functionally identical to the legacy generator):
      * ``'0`` / ``'1`` all-bits literals become the unsized decimals ``0`` /
        ``(2**n)-1`` — same value in the N-bit target.
      * ``sel_o = idx_bits'(i)`` (size cast of the loop var) is emitted as the
        equivalent truncating part-select ``i[idx_bits-1:0]`` via ``ExprZext``.
      * the mask rotation ``(n'd1 << ((i+1) % n)) - 1'b1`` is the same low-N-bit
        value as the unsized ``(1 << ((i+1) % n)) - 1``.

    Raises :class:`ProtocolSVUnsupported` for a non-round-robin or empty select
    (the caller falls back to legacy / the priority builder).
    """
    if not getattr(sel, "round_robin", False):
        raise ProtocolSVUnsupported("not a round-robin select")
    n = len(sel.branches)
    if n == 0:
        raise ProtocolSVUnsupported("empty select — no arbiter module")

    name = f"{module_prefix}{sel.name}_rr_arb"
    idx_bits = max(1, (n - 1).bit_length()) if n > 1 else 1

    ports = [
        RTLPort(name="clk", direction=PortDirection.INPUT),
        RTLPort(name="rst", direction=PortDirection.INPUT),
        RTLPort(name="req_i", width=n, direction=PortDirection.INPUT),
        RTLPort(name="gnt_o", width=n, direction=PortDirection.OUTPUT, net="logic"),
        RTLPort(name="sel_o", width=idx_bits, direction=PortDirection.OUTPUT, net="logic"),
        RTLPort(name="gnt_valid_o", direction=PortDirection.OUTPUT, net="logic"),
    ]
    wires = [
        RTLWire(name="mask_r", width=n, net="logic"),
        RTLWire(name="masked_req", width=n, net="logic"),
        RTLWire(name="unmasked_gnt", width=n, net="logic"),
        RTLWire(name="masked_gnt", width=n, net="logic"),
    ]
    assigns = [
        RTLAssign(lhs=RTLIdent(name="masked_req"),
                  rhs=RTLBinop(op="&", lhs=RTLIdent(name="req_i"),
                               rhs=RTLIdent(name="mask_r"))),
        RTLAssign(lhs=RTLIdent(name="gnt_o"),
                  rhs=RTLTernary(
                      cond=RTLBinop(op="!=", lhs=RTLIdent(name="masked_gnt"),
                                    rhs=RTLLiteral(value=0)),
                      then_=RTLIdent(name="masked_gnt"),
                      else_=RTLIdent(name="unmasked_gnt"))),
        RTLAssign(lhs=RTLIdent(name="gnt_valid_o"),
                  rhs=RTLBinop(op="!=", lhs=RTLIdent(name="req_i"),
                               rhs=RTLLiteral(value=0))),
    ]

    def _encode(dst: str, src: str) -> RTLAlways:
        """Comb priority encoder: one-hot the lowest set bit of *src* into *dst*."""
        return RTLAlways(sensitivity=[], body=[
            _assign(_ref(dst), _lit(0), op="="),
            s.SVStmtFor(var="i", limit=_lit(n), body=[
                s.SVStmtIf(
                    cond=_and(_sub(src, "i"),
                              _bin(ir.BinOp.Eq, _ref(dst), _lit(0))),
                    then_body=[_assign(_sub(dst, "i"),
                                       ir.ExprConstant(value=True), op="=")]),
            ]),
        ])

    masked_enc = _encode("masked_gnt", "masked_req")
    unmasked_enc = _encode("unmasked_gnt", "req_i")

    # sel_o = index of the granted (one-hot gnt_o) branch.
    sel_enc = RTLAlways(sensitivity=[], body=[
        _assign(_ref("sel_o"), _lit(0), op="="),
        s.SVStmtFor(var="i", limit=_lit(n), body=[
            s.SVStmtIf(cond=_sub("gnt_o", "i"),
                       then_body=[_assign(_ref("sel_o"),
                                          ir.ExprZext(value=_ref("i"), bits=idx_bits),
                                          op="=")]),
        ]),
    ])

    # Rotate mask one position past the winner: mask_r <= (1 << ((i+1) % n)) - 1.
    rotate = s.SVStmtFor(var="i", limit=_lit(n), body=[
        s.SVStmtIf(cond=_sub("gnt_o", "i"), then_body=[
            _assign(_ref("mask_r"),
                    _bin(ir.BinOp.Sub,
                         _bin(ir.BinOp.LShift, _lit(1),
                              _bin(ir.BinOp.Mod,
                                   _bin(ir.BinOp.Add, _ref("i"), _lit(1)),
                                   _lit(n))),
                         _lit(1))),
        ]),
    ])
    mask_ff = RTLAlways(sensitivity=["posedge clk"], body=[
        s.SVStmtIf(
            cond=_ref("rst"),
            then_body=[_assign(_ref("mask_r"), _lit((1 << n) - 1))],
            else_body=[s.SVStmtIf(cond=_ref("gnt_valid_o"), then_body=[rotate])]),
    ])

    return [RTLModule(name=name, ports=ports, wires=wires, assigns=assigns,
                      always_blocks=[masked_enc, unmasked_enc, sel_enc, mask_ff])]


# --------------------------------------------------------------------------- #
# IfProtocol port declarations / instantiation
# --------------------------------------------------------------------------- #

def _ifprotocol_rtl_ports(port: Any) -> List[RTLPort]:
    """Map an ``IfProtocolPortIR``'s ``all_sv_ports()`` triples to ``RTLPort``\\s.

    The bundle signals are procedurally driven, so ``net="logic"`` (matching the
    legacy ``generate_ifprotocol_port_decls``, which emits ``logic`` for every
    port regardless of direction).
    """
    from zuspec.be.sv.ir.rtl import PortDirection as _PD
    out: List[RTLPort] = []
    for direction, width, name in port.all_sv_ports():
        d = _PD.OUTPUT if direction == "output" else _PD.INPUT
        out.append(RTLPort(name=name, width=width, direction=d, net="logic"))
    return out


def build_ifprotocol_port_decls(port: Any, indent: str = "  ") -> str:
    """Return an SV port-declaration *fragment* for one IfProtocol port bundle.

    Structural equivalent of ``protocol_sv.generate_ifprotocol_port_decls``:
    builds ``RTLPort`` IR from ``all_sv_ports()`` and serialises it through
    ``RTLEmitter.emit_port_decl_fragment`` (be.sv owns the text).  Each line ends
    with a comma for splicing into the enclosing top-module header.
    """
    from zuspec.be.sv.ir.rtl_emit import RTLEmitter
    return RTLEmitter().emit_port_decl_fragment(_ifprotocol_rtl_ports(port), indent)


def build_ifprotocol_port_instantiation(port: Any, indent: str = "  ") -> str:
    """Return an SV ``.sig(sig),`` connection *fragment* for one IfProtocol port.

    Structural equivalent of ``protocol_sv.generate_port_instantiation`` — the
    by-name connection snippet, serialised via ``RTLEmitter.emit_port_connections``.
    """
    from zuspec.be.sv.ir.rtl_emit import RTLEmitter
    names = [name for _d, _w, name in port.all_sv_ports()]
    return RTLEmitter().emit_port_connections(names, indent)

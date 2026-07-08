"""MmrToSV — build be.sv IR for MMR (memory-mapped) register files (unification U-5).

Moves the MMR register-file SV emission off the legacy
``zuspec.synth.passes.mmr_regfile_emit.MmrRegFileRtlEmitter`` string generator onto
structured ``zuspec.be.sv`` IR so ``SVEmitter``/``RTLEmitter`` are the sole SV
serialisers.

Slice 1 (this file) covers the **plain SW-writable / HW-readable CSR** shape: fields
with no ``stickybit`` / ``singlepulse`` / ``hwset`` / ``hwclr`` / ``we`` / ``wel`` /
``swmod`` / ``onwrite`` semantics, ``precedence != 'hw'``, and ``hw ∈ {R, NA}`` (no
hardware write path).  Any register file containing a field outside that envelope
raises :class:`MmrSVUnsupported` and the caller falls back to the legacy emitter — so
the flip is regression-safe and coverage grows one field-semantic slice at a time.

Input is the raw ``@zdc.regfile`` class metadata (``_mmr_reg_classes`` + per-register
``_mmr_offset`` / ``_mmr_fields`` + field descriptor attributes) — a finite structured
schema, so the mapping is template-driven.
"""
from __future__ import annotations

import re
from typing import Any, List, Optional, Tuple

import zuspec.ir.core as ir

from zuspec.be.sv.ir import stmt as s
from zuspec.be.sv.ir.rtl import (
    PortDirection,
    RTLAlways,
    RTLAssign,
    RTLModule,
    RTLPort,
    RTLWire,
)
from zuspec.be.sv.ir.rtl_expr import RTLBinop, RTLIdent, RTLLiteral
from zuspec.be.sv.ir.sv import SVField, SVPackage, SVTypedefStruct


class MmrSVUnsupported(Exception):
    """Raised when a register file uses a field semantic slice 1 doesn't map yet.

    The caller catches this and falls back to the legacy
    ``MmrRegFileRtlEmitter`` for that register file.
    """


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _snake(name: str) -> str:
    """CamelCase → snake_case (matches the legacy emitter's module naming)."""
    s0 = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1_\2', name)
    s0 = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', s0)
    return s0.lower()


def _ref(name: str):
    return ir.ExprRefLocal(name=name)


def _lit(v: int):
    return ir.ExprConstant(value=v)


def _bit1(v: bool):
    """A width-1 constant — renders ``1'b1`` / ``1'b0`` via SVExprEmitter."""
    return ir.ExprConstant(value=bool(v))


def _assign(lhs, rhs, op="="):
    return s.SVStmtAssign(lhs=lhs, rhs=rhs, op=op)


def _wrdata_slice(width: int, lsb: int):
    """``cpuif_wr_data[lsb]`` (1-bit) or ``cpuif_wr_data[msb:lsb]`` (multi-bit)."""
    base = _ref("cpuif_wr_data")
    if width == 1:
        return ir.ExprSubscript(value=base, slice=_lit(lsb))
    return ir.ExprSubscript(
        value=base, slice=ir.ExprSlice(lower=_lit(lsb), upper=_lit(lsb + width - 1)))


def _prdata_slice(width: int, lsb: int):
    base = _ref("prdata")
    if width == 1:
        return ir.ExprSubscript(value=base, slice=_lit(lsb))
    return ir.ExprSubscript(
        value=base, slice=ir.ExprSlice(lower=_lit(lsb), upper=_lit(lsb + width - 1)))


def _zero_expr(width: int):
    """All-zeros for *width* — ``1'b0`` (width 1) or unsized ``0``."""
    return _bit1(False) if width == 1 else _lit(0)


def _ones_expr(width: int):
    """All-ones for *width* — ``1'b1`` (width 1) or unsized ``(2**w)-1``."""
    return _bit1(True) if width == 1 else _lit((1 << width) - 1)


def _bin(lhs, op, rhs):
    return ir.ExprBin(lhs=lhs, op=op, rhs=rhs)


def _invert(x):
    return ir.ExprUnary(op=ir.UnaryOp.Invert, operand=x)


def _hwin(reg_name: str, fname: str, suffix: str):
    return _ref(f"hwif_in.{reg_name}.{fname}_{suffix}")


def _fields(reg_cls):
    return getattr(reg_cls, '_mmr_fields', [])


def _sw_can_write(fd) -> bool:
    from zuspec.dataclasses.mmr.enums import SW
    return fd.sw not in (SW.RO, SW.NA)


def _hw_can_write(fd) -> bool:
    from zuspec.dataclasses.mmr.enums import HW
    return (fd.hw in (HW.W, HW.RW) or fd.hwset or fd.hwclr
            or bool(fd.stickybit))


def _field_supported(fd) -> bool:
    """True if *fd* is within the covered envelope.

    Slice 1 = plain SW-write / HW-read CSR.  Slice 2 adds ``singlepulse`` (``Pulse``).
    Slice 3 adds the full HW-write block: ``onwrite`` (woclr/woset/wot/wzs/wzc/wzt/
    wclr/wset), ``stickybit`` (posedge/negedge/bothedge) + ``hwset``/``hwclr``, plain
    ``hw=W``/``RW`` with ``we``/``wel``, ``field_q`` edge-detect registers and
    interrupt aggregation.
    """
    if fd.precedence == 'hw':
        return False  # precedence='hw' reordering deferred to a later slice
    # The legacy emitter emits a dangling ``else`` (illegal SV) when HW writes but
    # SW cannot — exclude that shape (it never compiled; fall back).
    if _hw_can_write(fd) and not _sw_can_write(fd):
        return False
    return True


def _require_supported(regfile_cls) -> None:
    reg_classes = getattr(regfile_cls, '_mmr_reg_classes', [])
    for _reg_name, reg_cls in reg_classes:
        for fname, fd in _fields(reg_cls):
            if not _field_supported(fd):
                raise MmrSVUnsupported(
                    f"field {reg_cls.__name__}.{fname} uses a semantic not yet "
                    f"mapped to be.sv IR (slice 1 = plain SW-write / HW-read)")


# --------------------------------------------------------------------------- #
# module builder
# --------------------------------------------------------------------------- #

def build_mmr_regfile_sv(regfile_cls, data_width: int = 32, addr_width: int = 8,
                         module_name: Optional[str] = None) -> List[Any]:
    """Build be.sv IR (``[RTLModule]``) for a plain-CSR ``@zdc.regfile`` class.

    Structural equivalent of ``MmrRegFileRtlEmitter.emit()`` for the slice-1
    envelope.  Raises :class:`MmrSVUnsupported` for any out-of-envelope field.
    """
    from zuspec.dataclasses.mmr.enums import SW, HW
    _require_supported(regfile_cls)

    mn = module_name or _snake(regfile_cls.__name__)
    aw, dw = addr_width, data_width
    reg_classes = getattr(regfile_cls, '_mmr_reg_classes', [])

    IN, OUT = PortDirection.INPUT, PortDirection.OUTPUT
    ports = [
        RTLPort(name="clk", direction=IN),
        RTLPort(name="rst", direction=IN),
        RTLPort(name="psel", direction=IN),
        RTLPort(name="penable", direction=IN),
        RTLPort(name="pwrite", direction=IN),
        RTLPort(name="paddr", width=aw, direction=IN),
        RTLPort(name="pwdata", width=dw, direction=IN),
        RTLPort(name="prdata", width=dw, direction=OUT, net="logic"),
        RTLPort(name="pready", direction=OUT, net="logic"),
        RTLPort(name="pslverr", direction=OUT, net="logic"),
        RTLPort(name="hwif_in", direction=IN, dtype=f"{mn}__in_t"),
        RTLPort(name="hwif_out", direction=OUT, dtype=f"{mn}__out_t"),
    ]

    wires = [
        RTLWire(name="cpuif_req", net="logic"),
        RTLWire(name="cpuif_req_is_wr", net="logic"),
        RTLWire(name="cpuif_addr", width=aw, net="logic"),
        RTLWire(name="cpuif_wr_data", width=dw, net="logic"),
    ]
    for reg_name, reg_cls in reg_classes:
        for fname, fd in _fields(reg_cls):
            w = fd._width
            wires.append(RTLWire(name=f"field_storage_{reg_name}_{fname}", width=w, net="logic"))
            wires.append(RTLWire(name=f"field_combo_next_{reg_name}_{fname}", width=w, net="logic"))
            wires.append(RTLWire(name=f"field_combo_load_{reg_name}_{fname}", net="logic"))
            if _has_field_q(fd):
                wires.append(RTLWire(name=f"field_q_{reg_name}_{fname}", net="logic"))
    for reg_name, _ in reg_classes:
        wires.append(RTLWire(name=f"decoded_reg_strb_{reg_name}", net="logic"))

    # Continuous assigns: APB tie-offs, cpuif shims, hwif_out value exports.
    assigns = [
        RTLAssign(lhs=RTLIdent(name="pready"), rhs=RTLLiteral(value=1, width=1)),
        RTLAssign(lhs=RTLIdent(name="pslverr"), rhs=RTLLiteral(value=0, width=1)),
        RTLAssign(lhs=RTLIdent(name="cpuif_req"),
                  rhs=RTLBinop(op="&", lhs=RTLIdent(name="psel"), rhs=RTLIdent(name="penable"))),
        RTLAssign(lhs=RTLIdent(name="cpuif_req_is_wr"), rhs=RTLIdent(name="pwrite")),
        RTLAssign(lhs=RTLIdent(name="cpuif_addr"), rhs=RTLIdent(name="paddr")),
        RTLAssign(lhs=RTLIdent(name="cpuif_wr_data"), rhs=RTLIdent(name="pwdata")),
    ]
    for reg_name, reg_cls in reg_classes:
        for fname, fd in _fields(reg_cls):
            if fd.hw in (HW.R, HW.RW):
                assigns.append(RTLAssign(
                    lhs=RTLIdent(name=f"hwif_out.{reg_name}.{fname}_value"),
                    rhs=RTLIdent(name=f"field_storage_{reg_name}_{fname}")))
            if fd.singlepulse or getattr(fd, 'swmod', False):
                # SW-modified pulse: high the cycle SW writes this register.
                assigns.append(RTLAssign(
                    lhs=RTLIdent(name=f"hwif_out.{reg_name}.{fname}_swmod"),
                    rhs=RTLBinop(op="&", lhs=RTLIdent(name=f"decoded_reg_strb_{reg_name}"),
                                 rhs=RTLIdent(name="cpuif_req_is_wr"))))
        # Interrupt aggregation: OR of the register's sticky-bit fields.
        sticky = [fn for fn, fd in _fields(reg_cls) if fd.stickybit]
        if sticky:
            expr = RTLIdent(name=f"field_storage_{reg_name}_{sticky[0]}")
            for fn in sticky[1:]:
                expr = RTLBinop(op="|", lhs=expr,
                                rhs=RTLIdent(name=f"field_storage_{reg_name}_{fn}"))
            assigns.append(RTLAssign(
                lhs=RTLIdent(name=f"hwif_out.{reg_name}.intr"), rhs=expr))

    always: List[RTLAlways] = [_build_decode(reg_classes, aw)]
    for reg_name, reg_cls in reg_classes:
        for fname, fd in _fields(reg_cls):
            always.append(_build_field_comb(reg_name, fname, fd))
            always.append(_build_field_ff(reg_name, fname, fd))
    always.append(_build_read_mux(reg_classes, aw, dw))

    return [RTLModule(name=mn, ports=ports, wires=wires, assigns=assigns,
                      always_blocks=always)]


def _build_decode(reg_classes, aw: int) -> RTLAlways:
    """Address strobe decoder: one ``decoded_reg_strb_<reg>`` per register."""
    body: List[s.SVStmt] = [
        _assign(_ref(f"decoded_reg_strb_{reg_name}"), _bit1(False))
        for reg_name, _ in reg_classes
    ]
    items = [
        s.SVCaseItem(labels=[_lit(reg_cls._mmr_offset)],
                     body=[_assign(_ref(f"decoded_reg_strb_{reg_name}"), _bit1(True))])
        for reg_name, reg_cls in reg_classes
    ]
    items.append(s.SVCaseItem(labels=[], body=[]))  # default: ;
    body.append(s.SVStmtIf(
        cond=_ref("cpuif_req"),
        then_body=[s.SVStmtCase(subject=_ref("cpuif_addr"), items=items)]))
    return RTLAlways(sensitivity=[], body=body)


def _sw_next_expr(reg_name: str, fname: str, fd):
    """RHS of the SW-write ``next`` assignment, per ``onwrite`` semantics."""
    w = fd._width
    sv = _ref(f"field_storage_{reg_name}_{fname}")
    wi = _wrdata_slice(w, fd.lsb)
    ow = fd.onwrite
    B = ir.BinOp
    if ow == 'woclr':   # sv & ~wi
        return _bin(sv, B.BitAnd, _invert(wi))
    if ow == 'woset':   # sv | wi
        return _bin(sv, B.BitOr, wi)
    if ow == 'wot':     # sv ^ wi
        return _bin(sv, B.BitXor, wi)
    if ow == 'wzs':     # wi ? sv : all-ones
        return ir.ExprIfExp(test=wi, body=sv, orelse=_ones_expr(w))
    if ow == 'wzc':     # wi ? sv : 0
        return ir.ExprIfExp(test=wi, body=sv, orelse=_zero_expr(w))
    if ow == 'wzt':     # wi ? sv : ~sv
        return ir.ExprIfExp(test=wi, body=sv, orelse=_invert(sv))
    if ow == 'wclr':
        return _zero_expr(w)
    if ow == 'wset':
        return _ones_expr(w)
    return wi           # plain write


def _hw_cond_body(reg_name: str, fname: str, fd):
    """Return ``(cond, next_expr)`` for the HW-write block, or ``(None, None)`` if the
    field has no HW write path.  ``cond is None`` with a next_expr means an
    unconditional ``else`` (plain ``hw=W``/``RW`` without ``we``/``wel``)."""
    from zuspec.dataclasses.mmr.enums import HW
    sv = _ref(f"field_storage_{reg_name}_{fname}")
    sb = fd.stickybit
    B = ir.BinOp
    if sb in (True, 'posedge'):
        hws = _hwin(reg_name, fname, "hwset"); q = _ref(f"field_q_{reg_name}_{fname}")
        return _bin(_invert(q), B.BitAnd, hws), _bin(sv, B.BitOr, hws)
    if sb == 'negedge':
        hws = _hwin(reg_name, fname, "hwset"); q = _ref(f"field_q_{reg_name}_{fname}")
        return _bin(q, B.BitAnd, _invert(hws)), _bin(sv, B.BitOr, _bit1(True))
    if sb == 'bothedge':
        hws = _hwin(reg_name, fname, "hwset"); q = _ref(f"field_q_{reg_name}_{fname}")
        return _bin(q, B.BitXor, hws), _bin(sv, B.BitOr, _bit1(True))
    if fd.hwset:
        hws = _hwin(reg_name, fname, "hwset")
        return hws, _bin(sv, B.BitOr, hws)
    if fd.hwclr:
        hwc = _hwin(reg_name, fname, "hwclr")
        return hwc, _bin(sv, B.BitAnd, _invert(hwc))
    if fd.hw in (HW.W, HW.RW):
        hw_next = _hwin(reg_name, fname, "next")
        if fd.we:
            return _hwin(reg_name, fname, "we"), hw_next
        if fd.wel:
            return ir.ExprUnary(op=ir.UnaryOp.Not, operand=_hwin(reg_name, fname, "wel")), hw_next
        return None, hw_next          # unconditional else
    return None, None


def _build_field_comb(reg_name: str, fname: str, fd) -> RTLAlways:
    w = fd._width
    sv = f"field_storage_{reg_name}_{fname}"
    nxt = f"field_combo_next_{reg_name}_{fname}"
    ldn = f"field_combo_load_{reg_name}_{fname}"
    strb = f"decoded_reg_strb_{reg_name}"

    body: List[s.SVStmt] = [
        _assign(_ref(nxt), _ref(sv)),
        _assign(_ref(ldn), _bit1(False)),
    ]
    sw_write = ir.ExprBool(op=ir.BoolOp.And,
                           values=[_ref(strb), _ref("cpuif_req_is_wr")])

    # HW-write block, chained as the SW block's ``else`` (matches legacy order).
    hw_cond, hw_next = _hw_cond_body(reg_name, fname, fd)
    hw_else: List[s.SVStmt] = []
    if hw_next is not None:
        hw_body = [_assign(_ref(nxt), hw_next), _assign(_ref(ldn), _bit1(True))]
        hw_else = [s.SVStmtIf(cond=hw_cond, then_body=hw_body)] if hw_cond is not None \
            else hw_body

    if _sw_can_write(fd):
        body.append(s.SVStmtIf(
            cond=sw_write,
            then_body=[
                _assign(_ref(nxt), _sw_next_expr(reg_name, fname, fd)),
                _assign(_ref(ldn), _bit1(True)),
            ],
            else_body=hw_else))
    elif hw_else:
        # HW-only write path (no SW write) — a standalone block (be.sv avoids the
        # legacy dangling-``else``; excluded from _field_supported for now anyway).
        body.extend(hw_else)

    if fd.singlepulse:
        # Auto-clear one cycle after the pulse.
        body.append(s.SVStmtIf(
            cond=ir.ExprBool(op=ir.BoolOp.And, values=[
                _bin(_ref(sv), ir.BinOp.NotEq, _zero_expr(w)),
                ir.ExprUnary(op=ir.UnaryOp.Not, operand=sw_write)]),
            then_body=[
                _assign(_ref(nxt), _zero_expr(w)),
                _assign(_ref(ldn), _bit1(True)),
            ]))
    return RTLAlways(sensitivity=[], body=body)


def _has_field_q(fd) -> bool:
    return fd.stickybit in (True, 'posedge', 'negedge', 'bothedge')


def _build_field_ff(reg_name: str, fname: str, fd) -> RTLAlways:
    sv = f"field_storage_{reg_name}_{fname}"
    nxt = f"field_combo_next_{reg_name}_{fname}"
    ldn = f"field_combo_load_{reg_name}_{fname}"
    rst_val = _reset_expr(fd)
    body: List[s.SVStmt] = [
        s.SVStmtIf(
            cond=_ref("rst"),
            then_body=[_assign(_ref(sv), rst_val, op="<=")],
            else_body=[s.SVStmtIf(
                cond=_ref(ldn),
                then_body=[_assign(_ref(sv), _ref(nxt), op="<=")])]),
    ]
    if _has_field_q(fd):
        # Edge-detect pipeline register: sample hwset each cycle.
        body.append(_assign(_ref(f"field_q_{reg_name}_{fname}"),
                            _hwin(reg_name, fname, "hwset"), op="<="))
    return RTLAlways(sensitivity=["posedge clk"], body=body)


def _reset_expr(fd):
    w = fd._width
    v = fd.default & ((1 << w) - 1)
    if w == 1:
        return _bit1(bool(v))
    return _lit(v)


def _build_read_mux(reg_classes, aw: int, dw: int) -> RTLAlways:
    from zuspec.dataclasses.mmr.enums import SW
    items: List[s.SVCaseItem] = []
    for reg_name, reg_cls in reg_classes:
        stmts: List[s.SVStmt] = []
        for fname, fd in _fields(reg_cls):
            if fd.sw in (SW.RO, SW.RW):
                stmts.append(_assign(_prdata_slice(fd._width, fd.lsb),
                                     _ref(f"field_storage_{reg_name}_{fname}")))
        items.append(s.SVCaseItem(labels=[_lit(reg_cls._mmr_offset)], body=stmts))
    items.append(s.SVCaseItem(labels=[], body=[]))  # default: ;

    return RTLAlways(sensitivity=[], body=[
        _assign(_ref("prdata"), _lit(0)),
        s.SVStmtIf(
            cond=ir.ExprBool(op=ir.BoolOp.And, values=[
                _ref("cpuif_req"),
                ir.ExprUnary(op=ir.UnaryOp.Not, operand=_ref("cpuif_req_is_wr"))]),
            then_body=[s.SVStmtCase(subject=_ref("cpuif_addr"), items=items)]),
    ])


# --------------------------------------------------------------------------- #
# package builder (hwif struct typedefs)
# --------------------------------------------------------------------------- #

def build_mmr_regfile_package(regfile_cls, module_name: Optional[str] = None) -> List[Any]:
    """Build be.sv IR (``[SVPackage]``) for the hwif ``in_t``/``out_t`` typedefs."""
    _require_supported(regfile_cls)
    mn = module_name or _snake(regfile_cls.__name__)
    reg_classes = getattr(regfile_cls, '_mmr_reg_classes', [])

    in_fields = _hwif_typedef_fields(reg_classes, _hwif_in_members)
    out_fields = _hwif_typedef_fields(reg_classes, _hwif_out_members, add_intr=True)
    pkg = SVPackage(name=f"{mn}_pkg", items=[
        SVTypedefStruct(name=f"{mn}__in_t", fields=in_fields),
        SVTypedefStruct(name=f"{mn}__out_t", fields=out_fields),
    ])
    return [pkg]


def _hwif_typedef_fields(reg_classes, member_fn, add_intr: bool = False) -> List[SVField]:
    out: List[SVField] = []
    for reg_name, reg_cls in reg_classes:
        members: List[Tuple[str, int]] = []
        for fname, fd in _fields(reg_cls):
            members += member_fn(fname, fd)
        if add_intr and any(fd.stickybit for _, fd in _fields(reg_cls)):
            members.append(("intr", 1))
        if members:
            out.append(SVField(name=reg_name,
                               fields=[SVField(name=sig, width=w) for sig, w in members]))
    if not out:
        # An empty ``struct packed { }`` is illegal SV (the legacy emitter's latent
        # bug — its tests never compile). Emit a reserved placeholder so the typedef
        # and its port are valid (e.g. a register file with no hwif_in signals).
        out.append(SVField(name="_reserved", width=1))
    return out


def _hwif_in_members(fname: str, fd) -> List[Tuple[str, int]]:
    from zuspec.dataclasses.mmr.enums import HW
    out: List[Tuple[str, int]] = []
    if fd.stickybit or fd.hwset:
        out.append((f"{fname}_hwset", 1))
    elif fd.hw in (HW.W, HW.RW):
        out.append((f"{fname}_next", fd._width))
    if fd.hwclr:
        out.append((f"{fname}_hwclr", 1))
    if fd.we:
        out.append((f"{fname}_we", 1))
    if fd.wel:
        out.append((f"{fname}_wel", 1))
    return out


def _hwif_out_members(fname: str, fd) -> List[Tuple[str, int]]:
    from zuspec.dataclasses.mmr.enums import HW
    out: List[Tuple[str, int]] = []
    if fd.hw in (HW.R, HW.RW):
        out.append((f"{fname}_value", fd._width))
    if fd.singlepulse or getattr(fd, 'swmod', False):
        out.append((f"{fname}_swmod", 1))
    return out

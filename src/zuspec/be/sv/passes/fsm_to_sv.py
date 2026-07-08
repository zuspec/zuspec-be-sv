"""FsmToSVPass — build be.sv IR from an sprtl ``FSMModule`` (unification phase U-2).

The FSM SV emission moves off ``zuspec.synth.sprtl.sv_codegen`` onto ``zuspec.be.sv``
so that ``SVEmitter`` is the sole SystemVerilog serialiser (mirrors the existing
``PipelineToSVPass``).  This module maps the FSM IR to be.sv RTL/SV IR nodes:

    FSMModule   -> RTLModule (+ SVTypedefEnum for a multi-state FSM's state type)
    FSMPort     -> RTLPort
    FSMRegister -> RTLWire
    states      -> SVTypedefEnum ``state_t`` + next-state ``always_comb`` case
                   + output ``always_ff`` case
    FSMAssign   -> SVStmtAssign     (``<=`` / ``=``)
    FSMCond     -> SVStmtIf
    transitions -> next_state assignments (conditional)

Coverage note: this builder handles the **core** FSM shape (ports, state enum,
registers, ``FSMAssign``/``FSMCond`` operations, conditional transitions, and
protocol-port handshake calls ``FSMPortCall``/``FSMPortOutput`` — including
derivation of their ``<port>_<method>_valid/arg*/ack/rdata`` ports and the
result register, mirroring ``sv_codegen.generate``).  The output/datapath
``always_ff`` carries a reset block that zeroes outputs and registers, matching
the ``docs/baseline_sv`` golden shape.  FSMs using constructs not yet mapped —
user structs/enums, struct instances, array fields, cycle-counter
(``WAIT_CYCLES``) states, or ``FSMMemRequest`` operations — raise
:class:`FsmSVUnsupported`; the caller falls back to the legacy generator for
those until the remaining shapes are migrated (U-2 follow-ons).
"""
from __future__ import annotations

from typing import Any, List

import zuspec.ir.core as ir

from zuspec.be.sv.ir import stmt as s
from zuspec.be.sv.ir.rtl import (
    PortDirection,
    RTLAlways,
    RTLModule,
    RTLWire,
    RTLPort,
)
from zuspec.be.sv.ir.sv import SVTypedefEnum


class FsmSVUnsupported(Exception):
    """Raised when an FSM uses a construct this (core-shape) builder can't map."""


# --------------------------------------------------------------------------- #
# expression / operation coercion
# --------------------------------------------------------------------------- #

def _expr(v: Any):
    """Coerce an FSM operation value/condition to a core Expr for SVExprEmitter.

    FSM op values are a heterogeneous mix (int, bool, str, or already a core
    ``ir`` Expr).  Strings are emitted verbatim (they are pre-rendered signal or
    sub-expression text); ints/bools become constants; core Exprs pass through.
    """
    if isinstance(v, bool):
        return ir.ExprConstant(value=int(v))
    if isinstance(v, int):
        return ir.ExprConstant(value=v)
    if isinstance(v, str):
        return ir.ExprRefLocal(name=v)
    return v


def _walk_ops(ops: List[Any]):
    """Yield every operation in *ops*, recursing into ``FSMCond`` branches."""
    for op in ops:
        yield op
        if type(op).__name__ == "FSMCond":
            yield from _walk_ops(getattr(op, "then_ops", []) or [])
            yield from _walk_ops(getattr(op, "else_ops", []) or [])


def _port_prefix(op: Any) -> str:
    return f"{op.port_name}_{op.method_name}"


def _ops_to_svstmts(ops: List[Any]) -> List[s.SVStmt]:
    out: List[s.SVStmt] = []
    for op in ops:
        cn = type(op).__name__
        if cn == "FSMAssign":
            if not isinstance(op.target, str):
                raise FsmSVUnsupported(f"non-scalar FSMAssign target {op.target!r}")
            arrow = "<=" if getattr(op, "is_nonblocking", True) else "="
            out.append(s.SVStmtAssign(
                lhs=ir.ExprRefLocal(name=op.target), rhs=_expr(op.value), op=arrow))
        elif cn == "FSMCond":
            out.append(s.SVStmtIf(
                cond=_expr(op.condition),
                then_body=_ops_to_svstmts(op.then_ops),
                else_body=_ops_to_svstmts(op.else_ops)))
        elif cn in ("FSMPortCall", "FSMPortOutput"):
            # Protocol-port handshake: assert valid, drive args, and (for an
            # awaited FSMPortCall with a result) latch rdata on ack.  Mirrors
            # sv_codegen._generate_port_call / _generate_port_output.
            prefix = _port_prefix(op)
            # ``valid`` is a 1-bit strobe → emit ``1'b1`` (bool constant renders
            # width-1 in SVExprEmitter), matching idiomatic handshake SV.
            out.append(s.SVStmtAssign(
                lhs=ir.ExprRefLocal(name=f"{prefix}_valid"),
                rhs=ir.ExprConstant(value=True), op="<="))
            for i, arg in enumerate(op.arg_exprs):
                out.append(s.SVStmtAssign(
                    lhs=ir.ExprRefLocal(name=f"{prefix}_arg{i}"),
                    rhs=_expr(arg), op="<="))
            if cn == "FSMPortCall" and getattr(op, "result_var", ""):
                out.append(s.SVStmtIf(
                    cond=ir.ExprRefLocal(name=f"{prefix}_ack"),
                    then_body=[s.SVStmtAssign(
                        lhs=ir.ExprRefLocal(name=op.result_var),
                        rhs=ir.ExprRefLocal(name=f"{prefix}_rdata"), op="<=")]))
        else:
            raise FsmSVUnsupported(f"operation {cn} not mapped by the core builder")
    return out


def _infer_ports_and_regs(fsm: Any):
    """Derive handshake ports/result registers from FSMPortCall/FSMPortOutput ops.

    Returns ``(extra_ports, extra_regs)`` where each port is
    ``(name, direction, width)`` and each reg is ``(name, width)``.  Names
    already declared on *fsm* are skipped (idempotent).  Mirrors the port
    inference in ``sv_codegen.generate`` (lines ~231-251).
    """
    known_ports = {p.name for p in fsm.ports}
    known_regs = {r.name for r in fsm.registers}
    extra_ports: List[tuple] = []
    extra_regs: List[tuple] = []

    def add_port(name, direction, width):
        if name not in known_ports:
            known_ports.add(name)
            extra_ports.append((name, direction, width))

    for st in fsm.states:
        for op in _walk_ops(st.operations):
            cn = type(op).__name__
            if cn not in ("FSMPortCall", "FSMPortOutput"):
                continue
            prefix = _port_prefix(op)
            add_port(f"{prefix}_valid", "output", 1)
            for i in range(len(op.arg_exprs)):
                add_port(f"{prefix}_arg{i}", "output", 32)
            if cn == "FSMPortCall":
                add_port(f"{prefix}_ack", "input", 1)
                if getattr(op, "result_var", ""):
                    add_port(f"{prefix}_rdata", "input", 32)
                    if op.result_var not in known_regs:
                        known_regs.add(op.result_var)
                        extra_regs.append((op.result_var, 32))
    return extra_ports, extra_regs


def _requires_fallback(fsm: Any) -> bool:
    """True if *fsm* uses a construct outside the core builder's envelope."""
    for attr in ("user_enums", "user_structs", "struct_instances", "array_fields"):
        if getattr(fsm, attr, None):
            return True
    return False


def _has_wait_counter(fsm: Any) -> bool:
    """True if any state needs a multi-cycle wait counter (``WAIT_CYCLES``).

    The legacy generator emits a dedicated cycle-counter ``always_ff`` (load on
    entry, decrement while in-state) for these states.  The core builder does
    not yet emit that counter logic, so such an FSM must fall back to legacy —
    otherwise the generated SV would be functionally incomplete (missing the
    counter) even though it lints clean.
    """
    for st in fsm.states:
        kind = getattr(st, "kind", None)
        if kind is not None and getattr(kind, "name", "") == "WAIT_CYCLES" \
                and getattr(st, "wait_cycles", 1) > 1:
            return True
    return False


def _state_member(state: Any) -> str:
    """SV enum member name for a state (upper-snake of its name)."""
    return (state.name or f"S{state.id}").upper().replace(" ", "_")


# --------------------------------------------------------------------------- #
# module construction
# --------------------------------------------------------------------------- #

def _ports(fsm: Any, clk: str, rst: str, extra_ports: List[tuple]) -> List[RTLPort]:
    ports = [RTLPort(name=clk, direction=PortDirection.INPUT),
             RTLPort(name=rst, direction=PortDirection.INPUT)]

    def add(name, direction, width):
        if name in (clk, rst):
            return
        is_out = direction == "output"
        d = PortDirection.OUTPUT if is_out else PortDirection.INPUT
        # Outputs are driven procedurally in the output always_ff → declare `logic`.
        ports.append(RTLPort(name=name, width=width, direction=d,
                             net="logic" if is_out else "wire"))

    for p in fsm.ports:
        add(p.name, p.direction, p.width)
    for name, direction, width in extra_ports:
        add(name, direction, width)
    return ports


def _reset_cond(fsm: Any, rst: str):
    return ir.ExprRefLocal(name=(f"!{rst}" if fsm.reset_active_low else rst))


def _reset_value(width: int):
    """Zero reset expression for an output/register of the given width."""
    return ir.ExprConstant(value=0)


def build_fsm_sv(fsm: Any) -> List[Any]:
    """Map one ``FSMModule`` to an ordered list of be.sv IR nodes.

    Returns ``[SVTypedefEnum, RTLModule]`` for a multi-state FSM, or
    ``[RTLModule]`` for a single-state FSM.  Raises :class:`FsmSVUnsupported`
    for shapes outside the core envelope.
    """
    if _requires_fallback(fsm):
        raise FsmSVUnsupported("FSM uses structs/arrays not yet mapped")
    if _has_wait_counter(fsm):
        raise FsmSVUnsupported(
            "FSM has a WAIT_CYCLES state needing cycle-counter logic (not yet mapped)")

    clk = fsm.clock_signal or "clk"
    rst = fsm.reset_signal or "rst_n"
    if getattr(fsm, "reset_async", False):
        raise FsmSVUnsupported("async reset not yet mapped by the core builder")

    # Derive handshake ports / result registers from FSMPortCall/FSMPortOutput.
    extra_ports, extra_regs = _infer_ports_and_regs(fsm)
    ports = _ports(fsm, clk, rst, extra_ports)
    # Registers are procedurally assigned → `logic`; guarded against
    # UNUSEDSIGNAL like the legacy internal-register block (a result register
    # may be write-only in a given configuration).
    reg_specs = [(r.name, r.width) for r in fsm.registers] + list(extra_regs)
    wires: List[RTLWire] = [RTLWire(name=n, width=w, net="logic",
                                    lint_off=["UNUSEDSIGNAL"])
                            for (n, w) in reg_specs]
    # Output ports (excluding clk/rst) + registers are zeroed in the output ff reset.
    reset_targets = [(p.name, p.width) for p in ports
                     if p.direction == PortDirection.OUTPUT] + list(reg_specs)
    always: List[RTLAlways] = []
    nodes: List[Any] = []

    single = getattr(fsm, "single_state", False)
    if not single and fsm.states:
        # State type + registers.
        enc = getattr(fsm, "state_encoding", {}) or {}
        members = [(_state_member(st), enc.get(st.id, i))
                   for i, st in enumerate(fsm.states)]
        nodes.append(SVTypedefEnum(name="state_t", members=members))
        wires.insert(0, RTLWire(name="state", dtype="state_t"))
        wires.insert(1, RTLWire(name="next_state", dtype="state_t"))

        init_member = _state_member(
            next((st for st in fsm.states if st.id == fsm.initial_state), fsm.states[0]))

        # State register: if (rst) state <= INIT; else state <= next_state;
        always.append(RTLAlways(sensitivity=[f"posedge {clk}"], body=[
            s.SVStmtIf(
                cond=_reset_cond(fsm, rst),
                then_body=[s.SVStmtAssign(lhs=ir.ExprRefLocal(name="state"),
                                          rhs=ir.ExprRefLocal(name=init_member), op="<=")],
                else_body=[s.SVStmtAssign(lhs=ir.ExprRefLocal(name="state"),
                                          rhs=ir.ExprRefLocal(name="next_state"), op="<=")]),
        ]))

        # Next-state comb: next_state = state; case(state) … endcase
        ns_items = []
        for st in fsm.states:
            body: List[s.SVStmt] = []
            for tr in st.transitions:
                tgt = _state_member(
                    next((x for x in fsm.states if x.id == tr.target_state), st))
                assign = s.SVStmtAssign(lhs=ir.ExprRefLocal(name="next_state"),
                                        rhs=ir.ExprRefLocal(name=tgt), op="=")
                if tr.condition is not None:
                    body.append(s.SVStmtIf(cond=_expr(tr.condition), then_body=[assign]))
                else:
                    body.append(assign)
            ns_items.append(s.SVCaseItem(labels=[ir.ExprRefLocal(name=_state_member(st))],
                                         body=body))
        ns_items.append(s.SVCaseItem(labels=[], body=[]))  # default: (complete case)
        always.append(RTLAlways(sensitivity=[], body=[
            s.SVStmtAssign(lhs=ir.ExprRefLocal(name="next_state"),
                           rhs=ir.ExprRefLocal(name="state"), op="="),
            s.SVStmtCase(subject=ir.ExprRefLocal(name="state"), items=ns_items),
        ]))

        # Output / datapath ff (registered), with a reset block that zeroes the
        # outputs and registers:
        #   if (rst) begin <targets> <= 0; end else begin case(state) … endcase end
        out_items = []
        for st in fsm.states:
            ops = _ops_to_svstmts(st.operations)
            if ops:
                out_items.append(s.SVCaseItem(
                    labels=[ir.ExprRefLocal(name=_state_member(st))], body=ops))
        if out_items or reset_targets:
            out_items.append(s.SVCaseItem(labels=[], body=[]))  # default: (complete case)
            reset_body = [
                s.SVStmtAssign(lhs=ir.ExprRefLocal(name=name),
                               rhs=_reset_value(w), op="<=")
                for (name, w) in reset_targets]
            out_case = s.SVStmtCase(subject=ir.ExprRefLocal(name="state"), items=out_items)
            always.append(RTLAlways(sensitivity=[f"posedge {clk}"], body=[
                s.SVStmtIf(cond=_reset_cond(fsm, rst),
                           then_body=reset_body,
                           else_body=[out_case]),
            ]))

    nodes.append(RTLModule(name=fsm.name, ports=ports, wires=wires, always_blocks=always))
    return nodes

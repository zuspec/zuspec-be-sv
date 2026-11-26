"""A transactor's shell and interface from its lowered core (fw-hdl
xtor-shell-plan.md T3; crossing semantics: fw-hdl-spec
semantics/processes.md X3-X8).

The core is a flat component whose crossings ``sync_xing_lower`` turned
into ready/valid ports and recorded in ``sync_xings``. Two texts:

- the **interface** ``<name>_if(input logic clock)``: the task side of each
  crossing, one slot (depth 1) and one task per process-side method
  (``<x>_put`` for a ``fw_to_sync``, ``<x>_get`` for a ``fw_from_sync``),
  over the core's ports;
- the **shell** module ``<name>``: the core (``u_core``) and the interface
  (``u_if``), joined on the crossing ports; the shell's ports are the
  core's other ports. The interface's clock is the net the core's step
  blocks run on, else (a core with no state) a ``clk`` input the shell
  adds.

Synthesizable form (emulation): the tasks wait only on ``@(posedge
clock)``; every variable has one writer (the tasks or the clock); a slot
is full when its two toggles differ. Tasks are called at an edge or wake
at one, and run before the edge's ``<=`` updates land, so they decide
from this edge's handshake (``valid && ready``), not from the registers;
they write with ``<=`` only, so nothing a task does at an edge changes
what that edge samples (X4).

Limits: depth 1 only; one caller per crossing at a time (two threads
calling ``put`` on one crossing at one edge both see the slot as it was;
the class model serializes them).
"""
from __future__ import annotations

from typing import Dict, List, Optional

import zuspec.ir.core as ir

from .structural import ValueTypes, _type, sv_module_name

__all__ = ["XtorShellEmitter"]


class XtorShellEmitter:
    """The interface and shell of transactor ``name`` around ``core`` (a
    lowered, flat component; its module is ``sv_module_name(core.name)``).
    ``types`` names the packed types (the generator's ``ValueTypes``)."""

    def __init__(self, core: ir.DataTypeComponent, name: str,
                 types: Optional[ValueTypes] = None):
        self.core = core
        self.name = name
        self.types = types
        self.xings = [x for x in core.sync_xings if len(x.path) == 1]
        if not self.xings:
            raise ValueError(f"'{core.name}' has no crossings of its own")
        for x in self.xings:
            if x.depth != 1:
                raise NotImplementedError(
                    f"crossing '{x.path[0]}' of '{core.name}': depth {x.depth} "
                    "(the interface has depth-1 slots only)")
        if getattr(core, "template_params", None):
            raise NotImplementedError(f"'{core.name}': a parameterized core")
        self.ports = [f for f in core.fields if isinstance(f, ir.FieldInOut)]
        xp = {p for x in self.xings for p in x.ports.values()}
        self.pins = [f for f in self.ports if f.name not in xp]
        clocks = {b.clock_net for b in core.proc_blocks
                  if b.kind == ir.ProcKind.STEP and b.clock_net}
        if len(clocks) > 1:
            raise NotImplementedError(f"'{core.name}': step blocks on clocks "
                                      f"{sorted(clocks)}")
        self.clock = next(iter(clocks), None)
        names = {f.name for f in self.pins}
        if self.clock is not None and self.clock not in names:
            raise ValueError(f"'{core.name}': clock '{self.clock}' is not a port")
        self.shell_clock = self.clock
        if self.shell_clock is None:
            self.shell_clock = "clk"
            while self.shell_clock in names:
                self.shell_clock += "_"
        # interface names: the core's crossing ports and the slots' own
        own = ("clock",)
        used: Dict[str, str] = {}
        for x in self.xings:
            n = x.path[0]
            for v in self._locals(x) + list(x.ports.values()) + [self._task(x)]:
                if v in used or v in own:
                    raise ValueError(f"'{core.name}': interface name '{v}' of crossing "
                                     f"'{n}' is taken by '{used.get(v, v)}'")
                used[v] = n

    # -- names -----------------------------------------------------------------

    @staticmethod
    def _to(x: ir.SyncXingRecord) -> bool:
        return x.direction == ir.SyncXingDir.TO_SYNC

    def _task(self, x: ir.SyncXingRecord) -> str:
        return f"{x.path[0]}_{'put' if self._to(x) else 'get'}"

    def _locals(self, x: ir.SyncXingRecord) -> List[str]:
        n = x.path[0]
        if self._to(x):
            return [f"{n}_put_t", f"{n}_took"]
        return [f"{n}_gave", f"{n}_got", f"{n}_full", f"{n}_q"] + \
            ([] if "wait" in x.ports else [f"{n}_wait"])

    def _t(self, x: ir.SyncXingRecord) -> str:
        return _type(x.element_type, self.types)

    # -- the interface ---------------------------------------------------------

    def interface(self) -> str:
        lines = [f"// The task side of {self.core.name}'s crossings "
                 "(zuspec-be-sv xtor_shell).",
                 f"interface {self.name}_if(input logic clock);"]
        init: List[str] = []
        for x in self.xings:
            lines.append("")
            lines.extend(self._put(x, init) if self._to(x) else self._get(x, init))
        lines.append("")
        lines.append("    initial begin")
        lines.extend(f"        {v} = 0;" for v in init)
        lines.append("    end")
        lines.append("endinterface")
        return "\n".join(lines) + "\n"

    def _put(self, x: ir.SyncXingRecord, init: List[str]) -> List[str]:
        n, t, p = x.path[0], self._t(x), x.ports
        v, r, d = p["valid"], p["ready"], p["data"]
        init += [f"{n}_put_t", f"{n}_took"]
        return [
            f"    // {n}: task side -> core (fw_to_sync, depth 1). Full from",
            f"    // {n}_put until the edge that takes its item.",
            f"    logic {n}_put_t, {n}_took;",
            f"    logic {v}, {r};",
            f"    {t} {d};",
            f"    assign {v} = {n}_put_t ^ {n}_took;",
            f"    always @(posedge clock) if ({v} && {r}) {n}_took <= ~{n}_took;",
            "",
            "    // put: waits for room (the slot empty, or its item taken at this",
            "    // edge), fills the slot, returns at the edge that takes it (X6).",
            f"    task automatic {n}_put(input {t} item);",
            f"        while ({v} && !{r}) @(posedge clock);",
            f"        {d} <= item;",
            f"        {n}_put_t <= ~{n}_put_t;",
            f"        do @(posedge clock); while (!{r});",
            "    endtask",
        ]

    def _get(self, x: ir.SyncXingRecord, init: List[str]) -> List[str]:
        n, t, p = x.path[0], self._t(x), x.ports
        v, r, d = p["valid"], p["ready"], p["data"]
        w = p.get("wait", f"{n}_wait")
        init += [f"{n}_gave", f"{n}_got", w]
        return [
            f"    // {n}: core -> task side (fw_from_sync, depth 1). The slot",
            f"    // fills at a giving edge that no {n}_get takes.",
            f"    logic {n}_gave, {n}_got, {w};",
            f"    logic {v}, {r}, {n}_full;",
            f"    {t} {d}, {n}_q;",
            f"    assign {n}_full = {n}_gave ^ {n}_got;",
            f"    assign {r} = !{n}_full;",
            f"    always @(posedge clock) if ({v} && {r} && !{w}) begin",
            f"        {n}_gave <= ~{n}_gave;",
            f"        {n}_q <= {d};",
            "    end",
            "",
            "    // get: the slot's item; else one given at this edge with no get",
            "    // waiting for it; else wait for the edge that gives one (it goes to",
            "    // the waiter, not the slot) (X5).",
            f"    task automatic {n}_get(output {t} item);",
            f"        if ({n}_full) begin",
            f"            item = {n}_q;",
            f"            {n}_got <= ~{n}_got;",
            f"        end else if ({v} && {r} && !{w}) begin",
            f"            item = {d};",
            f"            {n}_got <= ~{n}_got;",
            "        end else begin",
            f"            {w} <= 1;",
            f"            do @(posedge clock); while (!({v} && {r}));",
            f"            item = {d};",
            f"            {w} <= 0;",
            "        end",
            "    endtask",
        ]

    # -- the shell -------------------------------------------------------------

    def shell(self) -> str:
        ports = []
        if self.clock is None:
            ports.append(f"    input logic {self.shell_clock}")
        for f in self.pins:
            d = "inout" if f.is_inout else ("output" if f.is_out else "input")
            ports.append(f"    {d} {_type(f.datatype, self.types)} {f.name}")
        conns = [f".{f.name}({f.name})" for f in self.pins]
        conns += [f".{p}(u_if.{p})" for x in self.xings for p in x.ports.values()]
        lines = [f"// {self.name}: {self.core.name} and the task side of its crossings "
                 "(zuspec-be-sv xtor_shell).",
                 f"module {self.name}(",
                 ",\n".join(ports),
                 ");",
                 f"    {self.name}_if u_if(.clock({self.shell_clock}));",
                 "",
                 f"    {sv_module_name(self.core.name)} u_core(",
                 ",\n".join(f"        {c}" for c in conns),
                 "    );",
                 "endmodule"]
        return "\n".join(lines) + "\n"

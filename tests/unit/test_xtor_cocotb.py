"""xtor_cocotb: a transactor's cocotb bridge from its lowered core's IR
(fw-hdl xtor-shell-plan.md T7, D9). The bridges' behavior on a simulator
is fw-hdl's examples/wb_xtor/cocotb bench."""
import pytest

import zuspec.ir.core as ir
from zuspec.be.sv.xtor_cocotb import XtorCocotbEmitter

from test_xtor_bridge import _call, _ir


def _lines(t, start):
    """The stripped lines of the method starting ``start``."""
    out = []
    for l in t[t.index(start):].splitlines():
        if out and (l.startswith(("    def ", "    async def ", "    #")) or not l.startswith(" ")) \
                and l.strip():
            break
        if l.strip():
            out.append(l.strip())
    return out


def test_initiator():
    # implements its export's API; the API method's outputs are returned
    core, tm = _ir(body=[_call("reqs", "put", "v"), _call("rsps", "get", "r")])
    core.api_endpoints = [e for e in core.api_endpoints if e.kind == "export"]
    em = XtorCocotbEmitter(core, "xt_bridge", tm)
    t = em.module_text()
    compile(t, "xt_bridge.py", "exec")
    assert "from my_pkg import put_if" in t, t
    assert "class xt_bridge(put_if):" in t, t
    assert _lines(t, "async def put(") == [
        "async def put(self, v):",
        "\"\"\"xt_core::put (API 'put_if', export 't').\"\"\"",
        "r = 0", "await self._reqs_put(v)", "r = await self._rsps_get()", "return r"], t
    assert _lines(t, "async def run(") == [
        "async def run(self):",
        "\"\"\"Nothing to run: the API's calls drive the crossings.\"\"\""], t
    # the task side waits on events: a put raises valid, waits (settled) for
    # ready, then for the edge that takes the item
    assert _lines(t, "async def _reqs_put(") == [
        "async def _reqs_put(self, item):", "c = self._core",
        "async with self._reqs_lock:",
        "c.reqs_data.value = item & 0xff", "c.reqs_valid.value = 1",
        "while True:", "await ReadOnly()", "if c.reqs_ready.value:", "break",
        "await RisingEdge(c.reqs_ready)",
        "await RisingEdge(self._clock)", "c.reqs_valid.value = 0"], t
    # a get: the slot's item, else the one this edge takes, else wait
    get = _lines(t, "async def _rsps_get(")
    assert get[1:3] == ["if self._rsps_full:", "item = self._rsps_q"], t
    assert "if self._rsps_next is not None and not self._rsps_waiters:" in get, t
    # the watcher sleeps on valid while the slot has room
    recv = _lines(t, "async def _rsps_recv(")
    assert recv[5:9] == ["await ReadOnly()", "if not c.rsps_valid.value:",
                         "await RisingEdge(c.rsps_valid)", "continue"], t
    assert "start_soon(self._rsps_recv())" in t and "_clocked" not in t, t
    api = em.api_modules()
    assert list(api) == ["my_pkg"]
    compile(api["my_pkg"], "my_pkg.py", "exec")
    assert "class put_if(Protocol):\n    async def put(self, v: int) -> int: ..." \
        in api["my_pkg"], api


def test_initiator_edge():
    # wait="edge": the SV interface's code, with a sample at every edge
    core, tm = _ir(body=[_call("reqs", "put", "v"), _call("rsps", "get", "r")])
    core.api_endpoints = [e for e in core.api_endpoints if e.kind == "export"]
    t = XtorCocotbEmitter(core, "xt_bridge", tm, wait="edge").module_text()
    compile(t, "xt_bridge.py", "exec")
    for l in ["c.reqs_valid.value = self._reqs_valid()",
              "c.reqs_data.value = self._reqs_data & 0xff",
              "c.rsps_ready.value = int(not self._rsps_full())",
              'self._smp = {"reqs_ready": int(c.reqs_ready.value), '
              '"rsps_valid": int(c.rsps_valid.value), "rsps_data": int(c.rsps_data.value)}',
              "start_soon(self._clocked())"]:
        assert l in t, t
    with pytest.raises(ValueError, match="wait 'poll'"):
        XtorCocotbEmitter(core, "xt_bridge", tm, wait="poll")


def test_target():
    # holds its port, given by set_p(); run() checks it, then runs the task
    # code; a port call's outputs are assigned from its result
    core, tm = _ir()
    core.api_endpoints = [e for e in core.api_endpoints if e.kind == "port"]
    core.task_blocks[0].name = "run"
    core.task_blocks[0].args = []
    core.task_blocks[0].body = [ir.StmtWhile(test=ir.ExprConstant(value=True), body=[
        _call("rsps", "get", "r"), _call("p", "note", "r")])]
    em = XtorCocotbEmitter(core, "xt_bridge", tm)
    t = em.module_text()
    compile(t, "xt_bridge.py", "exec")
    assert "class xt_bridge:" in t, t
    assert _lines(t, "def set_p(") == [
        "def set_p(self, imp):",
        "\"\"\"Gives port 'p' its implementation of log_if; before run().\"\"\"",
        "self._p = imp"], t
    assert _lines(t, "async def run(") == [
        "async def run(self):", '"""xt_core::run."""',
        "if self._p is None:",
        "raise RuntimeError(\"xt_bridge: run() with port 'p' unset (call set_p())\")",
        "while True:", "r = await self._rsps_get()", "await self._p.note(r)"], t
    assert "async def note(self, v: int) -> None: ..." in em.api_modules()["my_pkg"]


def test_struct_items():
    # a packed struct crossing item: the first field is the most significant;
    # an item type no API declares gets its own dataclass
    st = ir.DataTypeStruct(name="item_t", super=None, fields=[
        ir.Field(name="a", datatype=ir.DataTypeInt(bits=4, signed=False)),
        ir.Field(name="b", datatype=ir.DataTypeInt(bits=1, signed=False))])
    core, tm = _ir(body=[_call("reqs", "put", "v"), _call("rsps", "get", "r")])
    core.api_endpoints = [e for e in core.api_endpoints if e.kind == "export"]
    for x in core.sync_xings:
        x.element_type = ir.DataTypeRef(ref_name="xt_core::item_t")
    tm["xt_core::item_t"] = st
    t = XtorCocotbEmitter(core, "xt_bridge", tm).module_text()
    compile(t, "xt_bridge.py", "exec")
    assert "@dataclass\nclass item_t:\n    a: int = 0\n    b: int = 0" in t, t
    assert "def _pack_item_t(v):\n    return ((v.a & 0xf) << 1) | (v.b & 0x1)" in t, t
    assert "def _unpack_item_t(x):\n    return item_t(a=(x >> 1) & 0xf, b=x & 0x1)" in t, t
    assert "c.reqs_data.value = _pack_item_t(item)" in t, t
    assert "self._rsps_next = _unpack_item_t(int(c.rsps_data.value))" in t, t


def test_refuses():
    # only the blocking put/get of each crossing
    core, tm = _ir(body=[_call("reqs", "try_put", "v", wait=False)])
    with pytest.raises(NotImplementedError, match="reqs_put"):
        XtorCocotbEmitter(core, "b", tm).module_text()
    # depth 1 only
    core, tm = _ir()
    core.sync_xings[0].depth = 2
    with pytest.raises(NotImplementedError, match="depth 2"):
        XtorCocotbEmitter(core, "b", tm)
    # ~x needs a width Python ints do not have
    core, tm = _ir(body=[ir.StmtAssign(
        targets=[ir.ExprRefLocal(name="r")],
        value=ir.ExprUnary(op=ir.UnaryOp.Invert, operand=ir.ExprRefLocal(name="v")))])
    with pytest.raises(NotImplementedError, match="width"):
        XtorCocotbEmitter(core, "b", tm).module_text()

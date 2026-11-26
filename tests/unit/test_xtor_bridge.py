"""xtor_bridge: a transactor's bridge class from its lowered core's IR
(fw-hdl xtor-shell-plan.md T4)."""
import pytest

import zuspec.ir.core as ir
from zuspec.be.sv.xtor_bridge import XtorBridgeEmitter

U8 = ir.DataTypeInt(bits=8, signed=False, four_state=False)   # SV bit [7:0]


def _h(n):
    return ir.ExprHierarchical(elements=[ir.ExprHierarchicalElem(name=n)])


def _call(recv, m, *args, wait=True):
    c = ir.ExprCall(func=ir.ExprAttribute(value=_h(recv), attr=m),
                    args=[ir.ExprRefLocal(name=a) for a in args])
    return ir.StmtExpr(expr=ir.ExprAwait(value=c) if wait else c)


def _arg(n, d):
    return ir.Arg(arg=n, datatype=U8, direction=d)


def _xing(n, d):
    return ir.SyncXingRecord(path=[n], direction=d, element_type=U8, depth=1,
                             ports={r: f"{n}_{r}" for r in ("valid", "ready", "data")})


def _ir(body=None, methods=None):
    """xt: exports API ``put_if`` (it implements ``put``, a request and
    response over its crossings) and holds port ``p`` (``log_if``, called
    from ``put``)."""
    IN, OUT = ir.SignalDirection.INPUT, ir.SignalDirection.OUTPUT
    put = ir.Function(name="put", is_async=True,
                      args=ir.Arguments(args=[_arg("v", IN), _arg("r", OUT)]))
    api = ir.DataTypeProtocol(name="put_if", methods=methods if methods is not None else [put])
    log = ir.DataTypeProtocol(name="log_if", methods=[
        ir.Function(name="note", is_async=True, args=ir.Arguments(args=[_arg("v", IN)]))])
    core = ir.DataTypeComponent(name="xt_core", super=None)
    core.sync_xings = [_xing("reqs", ir.SyncXingDir.TO_SYNC),
                       _xing("rsps", ir.SyncXingDir.FROM_SYNC)]
    core.api_endpoints = [
        ir.ApiEndpoint(name="t", kind="export", api="put_if", api_scope="my_pkg"),
        ir.ApiEndpoint(name="p", kind="port", api="log_if", api_scope="my_pkg",
                       api_params={"W": ir.ExprConstant(value=8)})]
    core.task_blocks = [ir.ProcBlock(
        name="put", kind=ir.ProcKind.TASK, args=[_arg("v", IN), _arg("r", OUT)],
        body=body if body is not None else [
            _call("p", "note", "v"), _call("reqs", "put", "v"), _call("rsps", "get", "r")])]
    return core, {"put_if": api, "log_if": log}


def test_bridge_class():
    core, tm = _ir()
    t = XtorBridgeEmitter(core, "xt_bridge", "xt_if", tm).class_text()
    assert "class xt_bridge extends fw_component implements my_pkg::put_if;" in t, t
    assert "fw_export #(my_pkg::put_if) t;" in t, t
    assert "fw_port #(my_pkg::log_if #(8)) p;" in t, t
    assert 'p = fw_port #(my_pkg::log_if #(8))::create("p", this);' in t, t
    body = t[t.index("virtual task put("):t.index("endtask")]
    assert [l.strip() for l in body.splitlines()] == [
        "virtual task put(input bit [7:0] v, output bit [7:0] r);",
        "my_pkg::log_if #(8) p_if = p.get_if();",
        "p_if.note(v);", "vif.reqs_put(v);", "vif.rsps_get(r);", ""], t
    p = XtorBridgeEmitter(core, "xt_bridge", "xt_if", tm).package_text()
    assert "package xt_bridge_pkg;" in p and '`include "xt_bridge.svh"' in p, p


def test_plain_target():
    # D10: no fw-hdl; built with its vif; the port is the API handle, given
    # by set_p(); run() checks it, then runs the task code
    core, tm = _ir()
    core.api_endpoints = [e for e in core.api_endpoints if e.kind == "port"]
    core.task_blocks[0].name = "run"
    core.task_blocks[0].args = []
    core.task_blocks[0].body = [_call("rsps", "get", "r"), _call("p", "note", "r")]
    br = XtorBridgeEmitter(core, "xt_bridge", "xt_if", tm, flavor="plain")
    t = br.class_text()
    assert "fw_" not in t, t
    assert t.splitlines()[2] == "class xt_bridge;", t
    for l in ["local my_pkg::log_if #(8) p;", "local virtual xt_if vif;",
              "function new(virtual xt_if u_if);", "function void set_p(my_pkg::log_if #(8) imp);"]:
        assert l in t, t
    body = t[t.index("virtual task run("):t.index("endtask")]
    assert [l.strip() for l in body.splitlines()] == [
        "virtual task run();",
        "if (p == null) $fatal(1, \"xt_bridge: run() with port 'p' unset (call set_p())\");",
        "vif.rsps_get(r);", "p.note(r);", ""], t
    p = br.package_text()
    assert "fw_hdl" not in p and "package xt_bridge_pkg;" in p, p


def test_plain_initiator():
    # an initiator's run() returns at once; its API method is the task code
    core, tm = _ir(body=[_call("reqs", "put", "v"), _call("rsps", "get", "r")])
    core.api_endpoints = [e for e in core.api_endpoints if e.kind == "export"]
    t = XtorBridgeEmitter(core, "xt_bridge", "xt_if", tm, flavor="plain").class_text()
    assert "fw_" not in t, t
    assert "class xt_bridge implements my_pkg::put_if;" in t, t
    assert "virtual task put(input bit [7:0] v, output bit [7:0] r);" in t, t
    run = t[t.index("virtual task run("):]
    assert run.splitlines()[1].strip() == "endtask", t


def test_refuses():
    # the interface has only the blocking put/get of each crossing
    core, tm = _ir(body=[_call("reqs", "try_put", "v", wait=False)])
    with pytest.raises(NotImplementedError, match="reqs_put"):
        XtorBridgeEmitter(core, "b", "i", tm).class_text()
    # wrong direction
    core, tm = _ir(body=[_call("reqs", "get", "r")])
    with pytest.raises(NotImplementedError, match="reqs"):
        XtorBridgeEmitter(core, "b", "i", tm).class_text()
    # an API method with no task code
    core, tm = _ir()
    tm["put_if"].methods.append(ir.Function(name="flush", is_async=True,
                                            args=ir.Arguments()))
    with pytest.raises(ValueError, match="'flush'.*no task code"):
        XtorBridgeEmitter(core, "b", "i", tm)
    # task code the front end could not map (FW-W-TASK-MAP)
    core, tm = _ir(body=[])
    with pytest.raises(ValueError, match="FW-W-TASK-MAP"):
        XtorBridgeEmitter(core, "b", "i", tm)
    with pytest.raises(ValueError, match="flavor 'uvm'"):
        XtorBridgeEmitter(*_ir()[:1], "b", "i", _ir()[1], flavor="uvm")

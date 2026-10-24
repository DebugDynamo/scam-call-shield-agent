import asyncio
from datetime import timedelta

import httpx
import pytest
from conftest import client_for, make_settings, new_session, say

from app.domain import now
from app.main import build_container
from app.tools.agent_proxy import fetch_remote_specs
from app.tools.builtin import safe_eval
from app.tools.registry import ToolFailure, ToolSpec


@pytest.fixture
async def c():
    cont = await build_container(make_settings())
    sid = (await cont.sessions.create(__import__("app.contracts.models", fromlist=["x"]).SessionCreate(mode="text"))).id
    cont.sid = sid
    return cont


async def run(c, name, args, token=None):
    return await c.sessions.executor.execute(c.sid, name, args, approval_token=token)


def test_calculator_safety():
    assert safe_eval("2 + 3 * 4") == 14
    assert safe_eval("sqrt(16) + abs(-2)") == 6
    for bad in ["__import__('os').system('x')", "1/0", "2**1000", "a+1", "open('x')", "(1).real", "1" * 300]:
        with pytest.raises(ToolFailure):
            safe_eval(bad)


async def test_schema_validation_and_unknown_tool(c):
    o = await run(c, "create_task", {})
    assert o.status == "error" and o.result["code"] == "INVALID_INPUT"
    o = await run(c, "create_task", {"title": 5})
    assert o.status == "error" and o.result["path"] == "title"
    o = await run(c, "create_task", {"title": "x", "evil": True})
    assert o.status == "error"
    o = await run(c, "calculator", {"expression": "1/0"})
    assert o.status == "error" and o.result["code"] == "INVALID_EXPRESSION"
    o = await run(c, "nope", {})
    assert o.result["code"] == "UNKNOWN_TOOL"
    o = await run(c, "get_task", {"id": "not-a-uuid"})
    assert o.result["code"] == "INVALID_INPUT"
    o = await run(c, "get_task", {"id": "6f1c2f0e-3b7a-4a55-9d3e-0a1b2c3d4e5f"})
    assert o.result["code"] == "NOT_FOUND"
    traces = await c.repo.list_traces(c.sid)
    assert len(traces) == 7  # every attempt is traced, including failures


async def test_task_crud(c):
    created = (await run(c, "create_task", {"title": "A", "priority": "high"})).result["task"]
    upd = await run(c, "update_task", {"id": created["id"], "status": "done"})
    assert upd.result["task"]["status"] == "done"
    assert (await run(c, "update_task", {"id": created["id"]})).status == "error"
    listed = await run(c, "list_tasks", {"status": "done"})
    assert any(t["id"] == created["id"] for t in listed.result["tasks"])


async def test_permissions_enforced():
    cont = await build_container(make_settings(tool_permissions="calc:use"))
    from app.contracts.models import SessionCreate

    sid = (await cont.sessions.create(SessionCreate(mode="text"))).id
    o = await cont.sessions.executor.execute(sid, "create_task", {"title": "x"})
    assert o.status == "forbidden" and "tasks:write" in o.result["message"]
    assert (await cont.sessions.executor.execute(sid, "calculator", {"expression": "1+1"})).status == "ok"


async def test_timeout(c):
    async def slow(ctx, args):
        await asyncio.sleep(1)
        return {}

    c.registry.register(ToolSpec("slow", "x", {"type": "object", "properties": {}}, slow, timeout_ms=50))
    o = await run(c, "slow", {})
    assert o.status == "error" and o.result["code"] == "TIMEOUT"


async def test_handler_crash_does_not_leak_internals(c):
    async def boom(ctx, args):
        raise RuntimeError("secret internal detail")

    c.registry.register(ToolSpec("boom", "x", {"type": "object", "properties": {}}, boom))
    o = await run(c, "boom", {})
    assert o.result["code"] == "INTERNAL" and "secret" not in str(o.result)


async def test_search_unavailable_when_unconfigured(c):
    o = await run(c, "web_search", {"query": "x"})
    assert o.status == "unavailable" and "SEARCH_API_URL" in o.result["message"]


async def test_search_configured_uses_backend():
    handler = httpx.MockTransport(lambda req: httpx.Response(200, json={"results": [{"title": "t"}]}))
    cont = await build_container(make_settings(search_api_url="http://search.test/q"), http_transport=handler)
    from app.contracts.models import SessionCreate

    sid = (await cont.sessions.create(SessionCreate(mode="text"))).id
    o = await cont.sessions.executor.execute(sid, "web_search", {"query": "x"})
    assert o.status == "ok" and o.result["results"] == [{"title": "t"}]


def test_approval_flow_via_api():
    with client_for() as client:
        s = new_session(client)
        task = say(client, s["id"], "create task doomed")["toolCalls"][0]["result"]["task"]
        r = say(client, s["id"], f"delete task {task['id']}")
        assert r["toolCalls"][0]["status"] == "pending_approval"
        detail = client.get(f"/api/v1/sessions/{s['id']}").json()
        pending = [a for a in detail["approvals"] if a["status"] == "pending"]
        assert len(pending) == 1
        # not deleted yet
        assert say(client, s["id"], f"get task {task['id']}")["toolCalls"][0]["status"] == "ok"
        res = client.post(f"/api/v1/sessions/{s['id']}/approvals/{pending[0]['id']}", json={"decision": "approve"}).json()
        assert res["toolResult"]["status"] == "ok" and res["approval"]["status"] == "consumed"
        assert say(client, s["id"], f"get task {task['id']}")["toolCalls"][0]["status"] == "error"
        # cannot decide twice
        again = client.post(f"/api/v1/sessions/{s['id']}/approvals/{pending[0]['id']}", json={"decision": "approve"})
        assert again.status_code == 409


def test_reject_and_foreign_session_and_human_approval_tool():
    with client_for() as client:
        s1, s2 = new_session(client), new_session(client)
        t = say(client, s1["id"], "create task keepme")["toolCalls"][0]["result"]["task"]
        say(client, s1["id"], f"delete task {t['id']}")
        aid = client.get(f"/api/v1/sessions/{s1['id']}").json()["approvals"][0]["id"]
        # another session cannot resolve it
        assert client.post(f"/api/v1/sessions/{s2['id']}/approvals/{aid}", json={"decision": "approve"}).status_code == 404
        assert client.post(f"/api/v1/sessions/{s1['id']}/approvals/{aid}", json={"decision": "reject"}).json()["approval"]["status"] == "rejected"
        assert say(client, s1["id"], f"get task {t['id']}")["toolCalls"][0]["status"] == "ok"
        # generic approval request tool
        r = say(client, s1["id"], "ask a human to wire money")
        assert r["toolCalls"][0]["tool"] == "request_human_approval" and r["toolCalls"][0]["status"] == "ok"
        assert r["toolCalls"][0]["result"]["status"] == "pending"
        bad = client.post(f"/api/v1/sessions/{s1['id']}/approvals/{aid}", json={"decision": "maybe"})
        assert bad.status_code == 400


async def test_token_is_single_use_bound_and_expires(c):
    t = (await run(c, "create_task", {"title": "T"})).result["task"]
    args = {"id": t["id"]}
    pend = await run(c, "delete_task", args)
    aid = pend.result["approvalId"]
    # a made-up token never works
    assert (await run(c, "delete_task", args, token="forged")).status == "pending_approval"
    _, token = await c.sessions.approvals.resolve(c.sid, aid, "approve")
    # token bound to the exact arguments
    other = (await run(c, "create_task", {"title": "T2"})).result["task"]
    assert (await run(c, "delete_task", {"id": other["id"]}, token=token)).status == "pending_approval"
    ok = await run(c, "delete_task", args, token=token)
    assert ok.status == "ok"
    # replay fails
    assert (await run(c, "delete_task", args, token=token)).status == "pending_approval"
    # expiry
    pend = await run(c, "delete_task", {"id": other["id"]})
    await c.repo.update_approval(pend.result["approvalId"], status="pending")
    c.repo._approvals[pend.result["approvalId"]].expires_at = now() - timedelta(seconds=1)
    from app.errors import AppError

    with pytest.raises(AppError) as e:
        await c.sessions.approvals.resolve(c.sid, pend.result["approvalId"], "approve")
    assert e.value.code == "CONFLICT"


async def test_agent_proxy_discovery_and_execute():
    calls = []

    def handler(req: httpx.Request):
        calls.append((req.method, req.url.path, req.headers.get("x-api-key")))
        if req.url.path == "/api/v1/tools" and req.method == "GET":
            return httpx.Response(200, json=[{"name": "echo", "description": "d", "inputSchema": {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}, "risk": "low", "timeoutMs": 1000, "requiresApproval": False}])
        if req.url.path == "/api/v1/tools/echo/execute":
            return httpx.Response(200, json={"echo": "pong"})
        return httpx.Response(404, json={"error": {"message": "nope"}})

    transport = httpx.MockTransport(handler)
    s = make_settings(agent_api_url="http://agent.test", agent_api_key="k")
    cont = await build_container(s, http_transport=transport)
    cont.registry.replace_source("agent-proxy", await fetch_remote_specs(s, transport))
    assert cont.registry.get("agent_echo") is not None
    from app.contracts.models import SessionCreate

    sid = (await cont.sessions.create(SessionCreate(mode="text"))).id
    o = await cont.sessions.executor.execute(sid, "agent_echo", {"x": "ping"})
    assert o.status == "ok" and o.result == {"echo": "pong"}
    assert calls[-1] == ("POST", "/api/v1/tools/echo/execute", "k")
    # downstream failure surfaces as unavailable, never faked
    down = httpx.MockTransport(lambda r: httpx.Response(503))
    cont.executor = cont.sessions.executor
    cont.sessions.executor.http_transport = down
    o = await cont.sessions.executor.execute(sid, "agent_echo", {"x": "ping"})
    assert o.status == "unavailable"


def test_tool_registry_endpoint_matches_contract(client):
    tools = client.get("/api/v1/tools").json()
    names = {t["name"] for t in tools}
    assert {"calculator", "web_search", "create_task", "get_task", "update_task", "request_human_approval"} <= names
    for t in tools:
        assert set(t) == {"name", "description", "inputSchema", "risk", "permissions", "timeoutMs", "requiresApproval"}
    assert next(t for t in tools if t["name"] == "delete_task")["requiresApproval"] is True

"""Postgres persistence against the docker DB `garbathon_voice` (skipped clearly when unreachable)."""
import subprocess
import sys
import uuid

import psycopg
import pytest
from conftest import make_settings

from app.contracts.models import SessionCreate
from app.domain import ApprovalRecord, MessageRecord, SessionRecord, TaskRecord, TraceRecord, now
from app.errors import AppError
from app.main import build_container


def _settings():
    return make_settings(data_mode="postgres")  # defaults: localhost:5432 postgres/postgres garbathon_voice


@pytest.fixture(scope="module", autouse=True)
def db():
    s = _settings()
    try:
        psycopg.connect(s.dsn, connect_timeout=3).close()
    except Exception as e:  # pragma: no cover
        pytest.skip(f"Postgres garbathon_voice unreachable at {s.db_host}:{s.db_port} ({type(e).__name__}); start docker compose postgres")
    r = subprocess.run([sys.executable, "-m", "app.db", "migrate"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    subprocess.run([sys.executable, "-m", "app.db", "seed"], check=True, capture_output=True)


async def test_repository_roundtrip_and_restart_persistence():
    c = await build_container(_settings())
    repo = c.repo
    s = await repo.create_session(SessionRecord(mode="text", transport="text", providers={"llm": "mock"}, simulated=True, metadata={"k": "v"}))
    await repo.add_message(MessageRecord(session_id=s.id, role="user", text="hello"))
    await repo.add_message(MessageRecord(session_id=s.id, role="tool", text="x", tool_call={"name": "calculator", "result": {"value": 1}}))
    await repo.add_trace(TraceRecord(session_id=s.id, tool="calculator", arguments={"expression": "1"}, result={"value": 1}, status="ok", latency_ms=2))
    t = await repo.create_task(TaskRecord(title=f"pg-{uuid.uuid4()}"))
    a = await repo.create_approval(ApprovalRecord(session_id=s.id, tool="delete_task", arguments={"id": t.id}, expires_at=now()))
    await c.repo.close()

    c2 = await build_container(_settings())  # "restart"
    got = await c2.repo.get_session(s.id)
    assert got and got.metadata == {"k": "v"} and got.simulated is True and got.providers == {"llm": "mock"}
    assert [m.text for m in await c2.repo.list_messages(s.id)] == ["hello", "x"]
    assert (await c2.repo.list_messages(s.id))[1].tool_call["result"] == {"value": 1}
    tr = (await c2.repo.list_traces(s.id))[0]
    assert tr.arguments == {"expression": "1"} and tr.latency_ms == 2
    assert (await c2.repo.get_task(t.id)).title == t.title
    assert (await c2.repo.get_approval(a.id)).arguments == {"id": t.id}
    upd = await c2.repo.update_task(t.id, status="done")
    assert upd.status == "done" and upd.updated_at >= t.updated_at
    assert await c2.repo.delete_task(t.id) is True and await c2.repo.get_task(t.id) is None
    items, total = await c2.repo.list_sessions(1, 5)
    assert total >= 1 and len(items) <= 5
    # startup cleanup of orphans
    assert await c2.repo.end_orphaned_sessions("ended: server restarted") >= 1
    assert (await c2.repo.get_session(s.id)).status == "ended"
    await c2.repo.close()


async def test_full_text_flow_persists_in_postgres():
    c = await build_container(_settings())
    sess = await c.sessions.create(SessionCreate(mode="text"))
    reply = await c.sessions.send_message(sess.id, "calculate 6*7")
    assert "42" in reply.reply.text
    await c.sessions.end(sess.id)
    await c.repo.close()
    c2 = await build_container(_settings())
    d = await c2.sessions.get(sess.id)
    assert d.status == "ended" and [m.role for m in d.messages] == ["user", "tool", "assistant"] and d.tool_trace[0].tool == "calculator"
    await c2.repo.close()


async def test_seed_is_idempotent_and_unavailable_db_is_explicit():
    r = subprocess.run([sys.executable, "-m", "app.db", "seed"], capture_output=True, text=True)
    assert "0 inserted" in r.stdout
    bad = make_settings(data_mode="postgres", db_port=5999)
    with pytest.raises(AppError) as e:
        await build_container(bad)
    assert e.value.code == "DATABASE_UNAVAILABLE"
    # ready endpoint reports down (never silently falls back to memory)
    from app.repositories.postgres import PostgresRepository

    repo = PostgresRepository(bad.dsn)
    with pytest.raises(AppError):
        await repo.open()


def test_reset_requires_explicit_opt_in():
    r = subprocess.run([sys.executable, "-m", "app.db", "reset"], capture_output=True, text=True)
    assert r.returncode == 3 and "CONFIRM_RESET=yes" in r.stderr

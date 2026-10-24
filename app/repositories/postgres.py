from __future__ import annotations

import functools
import time
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool, PoolTimeout

from ..domain import ApprovalRecord, MessageRecord, SessionRecord, TaskRecord, TraceRecord, now
from ..errors import AppError

_SESSION_COLS = (
    "id mode status transport providers simulated error agent metadata created_at ended_at last_activity_at"
).split()
_JSON_COLS = {"providers", "agent", "metadata", "tool_call", "arguments", "result"}
_UPDATABLE = {
    "sessions": {"status", "transport", "providers", "simulated", "error", "agent", "metadata", "ended_at", "last_activity_at"},
    "tasks": {"title", "description", "status", "priority"},
    "approvals": {"status", "token_hash", "resolved_at"},
}


def _db_errors(fn):
    @functools.wraps(fn)
    async def wrapper(*a, **kw):
        try:
            return await fn(*a, **kw)
        except (psycopg.OperationalError, PoolTimeout) as e:
            raise AppError("DATABASE_UNAVAILABLE", f"Database unavailable: {type(e).__name__}") from e

    return wrapper


def _adapt(values: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in values.items():
        if k in _JSON_COLS and v is not None:
            v = Jsonb(v)
        out[k] = v
    return out


class PostgresRepository:
    mode = "postgres"

    def __init__(self, dsn: str, max_size: int = 5) -> None:
        self._pool = AsyncConnectionPool(
            dsn, min_size=1, max_size=max_size, open=False, timeout=5, kwargs={"row_factory": dict_row, "connect_timeout": 5}
        )

    async def open(self) -> None:
        try:
            await self._pool.open(wait=True, timeout=6)
        except Exception as e:
            raise AppError("DATABASE_UNAVAILABLE", f"Cannot connect to Postgres: {type(e).__name__}") from e

    async def close(self) -> None:
        await self._pool.close()

    async def _fetch(self, sql: str, params: Any = None, *, one: bool = False):
        async with self._pool.connection() as conn:
            cur = await conn.execute(sql, params)
            if cur.description is None:
                return None
            return await (cur.fetchone() if one else cur.fetchall())

    async def _exec(self, sql: str, params: Any = None) -> int:
        async with self._pool.connection() as conn:
            cur = await conn.execute(sql, params)
            return cur.rowcount

    @_db_errors
    async def ping(self) -> int:
        t = time.perf_counter()
        await self._fetch("SELECT 1", one=True)
        return int((time.perf_counter() - t) * 1000)

    @_db_errors
    async def migrations_applied(self) -> list[str]:
        try:
            rows = await self._fetch("SELECT version FROM schema_migrations ORDER BY version")
        except psycopg.errors.UndefinedTable:
            return []
        return [r["version"] for r in rows]

    # ---- generic helpers ----
    async def _insert(self, table: str, rec: dict[str, Any]) -> None:
        cols = list(rec)
        sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('%(' + c + ')s' for c in cols)})"
        await self._exec(sql, _adapt(rec))

    async def _update(self, table: str, row_id: str, fields: dict[str, Any]) -> dict | None:
        bad = set(fields) - _UPDATABLE[table]
        if bad:
            raise ValueError(f"cannot update columns {bad}")
        if table == "tasks":
            fields = {**fields, "updated_at": now()}
        if not fields:
            return await self._fetch(f"SELECT * FROM {table} WHERE id=%s", (row_id,), one=True)
        sets = ", ".join(f"{c}=%({c})s" for c in fields)
        params = _adapt(fields) | {"_id": row_id}
        return await self._fetch(f"UPDATE {table} SET {sets} WHERE id=%(_id)s RETURNING *", params, one=True)

    # ---- sessions ----
    @_db_errors
    async def create_session(self, s: SessionRecord) -> SessionRecord:
        await self._insert("sessions", s.model_dump())
        return s

    @_db_errors
    async def get_session(self, session_id: str) -> SessionRecord | None:
        r = await self._fetch("SELECT * FROM sessions WHERE id=%s", (session_id,), one=True)
        return self._session(r)

    @staticmethod
    def _session(r: dict | None) -> SessionRecord | None:
        if not r:
            return None
        r = dict(r)
        r["id"] = str(r["id"])
        return SessionRecord(**{k: r[k] for k in _SESSION_COLS})

    @_db_errors
    async def list_sessions(self, page: int, page_size: int, status: str | None = None):
        where, params = ("WHERE status=%s", [status]) if status else ("", [])
        total = (await self._fetch(f"SELECT count(*) AS n FROM sessions {where}", params, one=True))["n"]
        rows = await self._fetch(
            f"SELECT * FROM sessions {where} ORDER BY created_at DESC LIMIT %s OFFSET %s",
            [*params, page_size, (page - 1) * page_size],
        )
        return [self._session(r) for r in rows], total

    @_db_errors
    async def update_session(self, session_id: str, **fields: Any) -> SessionRecord | None:
        return self._session(await self._update("sessions", session_id, fields))

    @_db_errors
    async def find_expired_sessions(self, idle_before: datetime) -> list[str]:
        rows = await self._fetch(
            "SELECT id FROM sessions WHERE status IN ('created','active') AND last_activity_at < %s", (idle_before,)
        )
        return [str(r["id"]) for r in rows]

    @_db_errors
    async def end_orphaned_sessions(self, reason: str) -> int:
        return await self._exec(
            "UPDATE sessions SET status='ended', ended_at=%s, error=%s WHERE status IN ('created','active')",
            (now(), reason),
        )

    # ---- messages / traces ----
    @_db_errors
    async def add_message(self, m: MessageRecord) -> None:
        await self._insert("messages", m.model_dump())

    @_db_errors
    async def list_messages(self, session_id: str) -> list[MessageRecord]:
        rows = await self._fetch("SELECT * FROM messages WHERE session_id=%s ORDER BY seq", (session_id,))
        out = []
        for r in rows:
            r.pop("seq", None)
            out.append(MessageRecord(**{**r, "id": str(r["id"]), "session_id": str(r["session_id"])}))
        return out

    @_db_errors
    async def add_trace(self, t: TraceRecord) -> None:
        await self._insert("tool_traces", t.model_dump())

    @_db_errors
    async def list_traces(self, session_id: str) -> list[TraceRecord]:
        rows = await self._fetch("SELECT * FROM tool_traces WHERE session_id=%s ORDER BY seq", (session_id,))
        out = []
        for r in rows:
            r.pop("seq", None)
            out.append(TraceRecord(**{**r, "id": str(r["id"]), "session_id": str(r["session_id"])}))
        return out

    # ---- tasks ----
    @staticmethod
    def _task(r: dict | None) -> TaskRecord | None:
        return TaskRecord(**{**r, "id": str(r["id"])}) if r else None

    @_db_errors
    async def create_task(self, t: TaskRecord) -> TaskRecord:
        await self._insert("tasks", t.model_dump())
        return t

    @_db_errors
    async def get_task(self, task_id: str) -> TaskRecord | None:
        return self._task(await self._fetch("SELECT * FROM tasks WHERE id=%s", (task_id,), one=True))

    @_db_errors
    async def list_tasks(self, status: str | None = None, limit: int = 50) -> list[TaskRecord]:
        where, params = ("WHERE status=%s", [status]) if status else ("", [])
        rows = await self._fetch(f"SELECT * FROM tasks {where} ORDER BY created_at, title LIMIT %s", [*params, limit])
        return [self._task(r) for r in rows]

    @_db_errors
    async def update_task(self, task_id: str, **fields: Any) -> TaskRecord | None:
        return self._task(await self._update("tasks", task_id, fields))

    @_db_errors
    async def delete_task(self, task_id: str) -> bool:
        return (await self._exec("DELETE FROM tasks WHERE id=%s", (task_id,))) > 0

    # ---- approvals ----
    @staticmethod
    def _approval(r: dict | None) -> ApprovalRecord | None:
        return ApprovalRecord(**{**r, "id": str(r["id"]), "session_id": str(r["session_id"])}) if r else None

    @_db_errors
    async def create_approval(self, a: ApprovalRecord) -> ApprovalRecord:
        await self._insert("approvals", a.model_dump())
        return a

    @_db_errors
    async def get_approval(self, approval_id: str) -> ApprovalRecord | None:
        return self._approval(await self._fetch("SELECT * FROM approvals WHERE id=%s", (approval_id,), one=True))

    @_db_errors
    async def list_approvals(self, session_id: str) -> list[ApprovalRecord]:
        rows = await self._fetch("SELECT * FROM approvals WHERE session_id=%s ORDER BY created_at", (session_id,))
        return [self._approval(r) for r in rows]

    @_db_errors
    async def update_approval(self, approval_id: str, **fields: Any) -> ApprovalRecord | None:
        return self._approval(await self._update("approvals", approval_id, fields))

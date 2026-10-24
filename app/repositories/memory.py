from __future__ import annotations

from datetime import datetime
from typing import Any

from ..domain import ApprovalRecord, MessageRecord, SessionRecord, TaskRecord, TraceRecord, now
from ..seed_data import seed_tasks


class MemoryRepository:
    """Real in-memory implementation behind the Repository port. Data is lost on restart (documented)."""

    mode = "mock"

    def __init__(self) -> None:
        self._sessions: dict[str, SessionRecord] = {}
        self._messages: dict[str, list[MessageRecord]] = {}
        self._traces: dict[str, list[TraceRecord]] = {}
        self._tasks: dict[str, TaskRecord] = {t.id: t for t in seed_tasks()}
        self._approvals: dict[str, ApprovalRecord] = {}

    async def ping(self) -> int:
        return 0

    async def close(self) -> None:
        return None

    # sessions
    async def create_session(self, s: SessionRecord) -> SessionRecord:
        self._sessions[s.id] = s
        return s

    async def get_session(self, session_id: str) -> SessionRecord | None:
        return self._sessions.get(session_id)

    async def list_sessions(self, page: int, page_size: int, status: str | None = None):
        items = sorted(self._sessions.values(), key=lambda x: x.created_at, reverse=True)
        if status:
            items = [i for i in items if i.status == status]
        start = (page - 1) * page_size
        return items[start : start + page_size], len(items)

    async def update_session(self, session_id: str, **fields: Any) -> SessionRecord | None:
        s = self._sessions.get(session_id)
        if not s:
            return None
        for k, v in fields.items():
            setattr(s, k, v)
        return s

    async def find_expired_sessions(self, idle_before: datetime) -> list[str]:
        return [
            s.id for s in self._sessions.values()
            if s.status in ("created", "active") and s.last_activity_at < idle_before
        ]

    async def end_orphaned_sessions(self, reason: str) -> int:
        n = 0
        for s in self._sessions.values():
            if s.status in ("created", "active"):
                s.status, s.ended_at, s.error = "ended", now(), reason
                n += 1
        return n

    # messages / traces
    async def add_message(self, m: MessageRecord) -> None:
        self._messages.setdefault(m.session_id, []).append(m)

    async def list_messages(self, session_id: str) -> list[MessageRecord]:
        return list(self._messages.get(session_id, []))

    async def add_trace(self, t: TraceRecord) -> None:
        self._traces.setdefault(t.session_id, []).append(t)

    async def list_traces(self, session_id: str) -> list[TraceRecord]:
        return list(self._traces.get(session_id, []))

    # tasks
    async def create_task(self, t: TaskRecord) -> TaskRecord:
        self._tasks[t.id] = t
        return t

    async def get_task(self, task_id: str) -> TaskRecord | None:
        return self._tasks.get(task_id)

    async def list_tasks(self, status: str | None = None, limit: int = 50) -> list[TaskRecord]:
        items = sorted(self._tasks.values(), key=lambda x: (x.created_at, x.title))
        if status:
            items = [i for i in items if i.status == status]
        return items[:limit]

    async def update_task(self, task_id: str, **fields: Any) -> TaskRecord | None:
        t = self._tasks.get(task_id)
        if not t:
            return None
        for k, v in fields.items():
            setattr(t, k, v)
        t.updated_at = now()
        return t

    async def delete_task(self, task_id: str) -> bool:
        return self._tasks.pop(task_id, None) is not None

    # approvals
    async def create_approval(self, a: ApprovalRecord) -> ApprovalRecord:
        self._approvals[a.id] = a
        return a

    async def get_approval(self, approval_id: str) -> ApprovalRecord | None:
        return self._approvals.get(approval_id)

    async def list_approvals(self, session_id: str) -> list[ApprovalRecord]:
        return sorted((a for a in self._approvals.values() if a.session_id == session_id), key=lambda a: a.created_at)

    async def update_approval(self, approval_id: str, **fields: Any) -> ApprovalRecord | None:
        a = self._approvals.get(approval_id)
        if not a:
            return None
        for k, v in fields.items():
            setattr(a, k, v)
        return a

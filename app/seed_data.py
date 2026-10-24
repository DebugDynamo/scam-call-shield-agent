"""Deterministic demo tasks, used by both the memory repository and `python -m app.db.seed`."""

import uuid
from datetime import UTC, datetime

from .domain import TaskRecord

_NS = uuid.UUID("6f1c2f0e-3b7a-4a55-9d3e-0a1b2c3d4e5f")
_T0 = datetime(2026, 1, 1, tzinfo=UTC)

_RAW = [
    ("Draft the project kickoff agenda", "Neutral demo task", "todo", "medium"),
    ("Review open pull requests", "Neutral demo task", "in_progress", "high"),
    ("Update the onboarding checklist", "Neutral demo task", "done", "low"),
]


def seed_tasks() -> list[TaskRecord]:
    return [
        TaskRecord(
            id=str(uuid.uuid5(_NS, title)), title=title, description=desc, status=status, priority=prio,
            created_at=_T0, updated_at=_T0,
        )
        for title, desc, status, prio in _RAW
    ]

"""Internal records shared by repositories and services (snake_case, storage-oriented)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


def now() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid.uuid4())


class SessionRecord(BaseModel):
    id: str = Field(default_factory=new_id)
    mode: str
    status: str = "created"  # created | active | ended | failed
    transport: str
    providers: dict[str, str | None] = Field(default_factory=dict)
    simulated: bool = False
    error: str | None = None
    agent: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=now)
    ended_at: datetime | None = None
    last_activity_at: datetime = Field(default_factory=now)


class MessageRecord(BaseModel):
    id: str = Field(default_factory=new_id)
    session_id: str
    role: str  # user | assistant | tool
    text: str
    tool_call: dict[str, Any] | None = None
    at: datetime = Field(default_factory=now)


class TraceRecord(BaseModel):
    id: str = Field(default_factory=new_id)
    session_id: str
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    status: str  # ok | error | pending_approval | rejected | forbidden | unavailable
    latency_ms: int = 0
    at: datetime = Field(default_factory=now)


class TaskRecord(BaseModel):
    id: str = Field(default_factory=new_id)
    title: str
    description: str | None = None
    status: str = "todo"  # todo | in_progress | done
    priority: str = "medium"  # low | medium | high
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)


class ApprovalRecord(BaseModel):
    id: str = Field(default_factory=new_id)
    session_id: str
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    status: str = "pending"  # pending | approved | rejected | consumed | expired
    token_hash: str | None = None
    created_at: datetime = Field(default_factory=now)
    expires_at: datetime
    resolved_at: datetime | None = None

"""Pydantic models for every shape in docs/API_CONTRACTS.md (voice service) + documented extensions (marked EXT)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

SessionMode = Literal["text", "browser", "phone", "room"]
SessionStatusName = Literal["created", "active", "ended", "failed"]
TransportName = Literal["text", "smallwebrtc", "plivo", "room"]


class Camel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


# ---- common ----
class ErrorDetail(Camel):
    path: str
    message: str


class ErrorBody(Camel):
    code: str
    message: str
    details: list[ErrorDetail] = []
    request_id: str
    timestamp: str


class ErrorResponse(Camel):
    error: ErrorBody


class Page(Camel):
    items: list[Any]
    page: int
    page_size: int
    total: int


class Health(Camel):
    status: Literal["ok"] = "ok"
    service: str
    version: str
    data_mode: Literal["mock", "postgres"]
    time: str


class DbCheck(Camel):
    status: Literal["up", "down", "skipped"]
    latency_ms: int | None = None


class ProviderCheck(Camel):
    status: Literal["configured", "not_configured", "mock"]
    name: str | None = None
    model: str | None = None
    missing: list[str] = []  # EXT: names of missing env vars (never values)


class ReadyChecks(Camel):
    database: DbCheck
    provider: ProviderCheck
    stt: ProviderCheck | None = None  # EXT
    tts: ProviderCheck | None = None  # EXT


class Ready(Camel):
    status: Literal["ready", "not_ready"]
    service: str
    data_mode: Literal["mock", "postgres"]
    checks: ReadyChecks
    simulated: bool = False  # EXT: true when the LLM provider is mock


# ---- sessions ----
class AgentOverride(Camel):
    name: str = Field("assistant", min_length=1, max_length=80)
    system_prompt: str | None = Field(None, max_length=8000)


class SessionCreate(Camel):
    mode: SessionMode = "text"
    agent: AgentOverride | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolCallInfo(Camel):
    name: str | None = None
    arguments: dict[str, Any] = {}
    result: dict[str, Any] | None = None
    status: str | None = None


class MessageOut(Camel):
    role: Literal["user", "assistant", "tool"]
    text: str
    at: datetime
    tool_call: dict[str, Any] | None = None


class ToolTraceOut(Camel):
    tool: str
    arguments: dict[str, Any]
    result: dict[str, Any]
    status: str
    at: datetime
    latency_ms: int = 0  # EXT


class ProvidersUsed(Camel):
    llm: str | None
    stt: str | None
    tts: str | None


class PendingApproval(Camel):  # EXT
    id: str
    tool: str
    arguments: dict[str, Any]
    reason: str
    status: str
    expires_at: datetime


class SessionStatus(Camel):
    id: str
    mode: SessionMode
    status: SessionStatusName
    transport: TransportName
    providers: ProvidersUsed
    messages: list[MessageOut] = []
    tool_trace: list[ToolTraceOut] = []
    simulated: bool
    error: str | None = None
    created_at: datetime
    ended_at: datetime | None = None
    approvals: list[PendingApproval] = []  # EXT
    metadata: dict[str, Any] = {}  # EXT
    transcripts_persisted: bool = True  # EXT


class SessionList(Camel):
    items: list[SessionStatus]
    page: int
    page_size: int
    total: int


class MessageCreate(Camel):
    text: str = Field(..., min_length=1, max_length=4000)


class MessageReply(Camel):
    session_id: str
    reply: MessageOut
    messages: list[MessageOut]  # all messages produced this turn (user, tool..., assistant)
    tool_calls: list[ToolTraceOut]
    simulated: bool
    provider: dict[str, str | None]


class IceCandidateIn(Camel):
    candidate: str
    sdp_mid: str | None = None
    sdp_mline_index: int | None = None


class OfferRequest(Camel):
    sdp: str
    type: Literal["offer"] = "offer"
    pc_id: str | None = None
    restart_pc: bool | None = None
    request_data: Any | None = None


class OfferResponse(Camel):
    sdp: str
    type: str
    pc_id: str


class IcePatch(Camel):
    pc_id: str
    candidates: list[IceCandidateIn]


class ApprovalDecision(Camel):  # EXT
    decision: Literal["approve", "reject"]


class ApprovalResult(Camel):  # EXT
    approval: PendingApproval
    tool_result: dict[str, Any] | None = None


# ---- tools ----
class ToolContract(Camel):
    name: str
    description: str
    input_schema: dict[str, Any]
    risk: Literal["low", "medium", "high"]
    permissions: list[str]
    timeout_ms: int
    requires_approval: bool


# ---- telephony ----
class OutboundCallRequest(Camel):
    to: str = Field(..., pattern=r"^\+[1-9]\d{6,14}$", description="E.164 destination")
    confirm: Literal[True] = Field(..., description="Must be literally true: calls are never automatic")
    agent: AgentOverride | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class OutboundCallResponse(Camel):
    session_id: str
    call_uuid: str | None
    status: str


# ---- providers ----
class ProviderInfo(Camel):
    kind: Literal["llm", "stt", "tts"]
    name: str
    status: Literal["configured", "not_configured", "mock"]
    model: str | None = None
    missing: list[str] = []
    simulated: bool = False


class ProvidersResponse(Camel):
    llm: ProviderInfo
    stt: ProviderInfo
    tts: ProviderInfo
    telephony: dict[str, Any]
    room: dict[str, Any]
    persist_transcripts: bool
    data_mode: Literal["mock", "postgres"]

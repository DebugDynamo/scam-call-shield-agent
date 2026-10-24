from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from loguru import logger

from ..agent.core import VoiceAgent
from ..agent.llm import GeminiLLM, MockLLM, TextLLM
from ..config import Settings
from ..contracts import models as C
from ..domain import ApprovalRecord, SessionRecord, now
from ..errors import AppError, not_found
from ..providers import status as pstatus
from ..tools.executor import ApprovalService, ToolExecutor
from ..tools.registry import ToolRegistry

TRANSPORT_BY_MODE = {"text": "text", "browser": "smallwebrtc", "phone": "plivo", "room": "room"}


@dataclass
class Runtime:
    agent: VoiceAgent
    llm: TextLLM | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    pc_id: str | None = None
    worker: Any = None
    runner_task: asyncio.Task | None = None
    notify: Any = None  # async (text) -> None, set by voice pipelines
    diagnostics: dict[str, Any] = field(default_factory=dict)
    closing: bool = False


def _uuid_or_404(session_id: str) -> str:
    try:
        return str(uuid.UUID(session_id))
    except ValueError as e:
        raise not_found("Session", session_id) from e


class SessionService:
    def __init__(self, settings: Settings, repo, registry: ToolRegistry, executor: ToolExecutor, approvals: ApprovalService):
        self.s, self.repo, self.registry, self.executor, self.approvals = settings, repo, registry, executor, approvals
        self.runtimes: dict[str, Runtime] = {}
        self._text_llm: TextLLM | None = None

    # ---------- provider access ----------
    def text_llm(self) -> TextLLM:
        pstatus.require(pstatus.llm_state(self.s))
        if self._text_llm is None:
            if self.s.llm_provider == "mock":
                self._text_llm = MockLLM()
            else:
                self._text_llm = GeminiLLM(self.s.gemini_api_key, self.s.gemini_model, self.s.llm_timeout_seconds)  # type: ignore[arg-type]
        return self._text_llm

    # ---------- lifecycle ----------
    async def create(self, req: C.SessionCreate) -> C.SessionStatus:
        if req.mode == "room":
            raise AppError("NOT_IMPLEMENTED", "Room mode is an adapter interface only; no room provider is implemented (see docs/PROVIDERS.md)")
        states = pstatus.require_for_mode(self.s, req.mode)
        simulated = any(st.simulated for st in states.values())
        agent_cfg = (req.agent or C.AgentOverride()).model_dump(by_alias=False)
        rec = SessionRecord(
            mode=req.mode, transport=TRANSPORT_BY_MODE[req.mode], simulated=simulated,
            providers={"llm": states["llm"].name, "stt": states["stt"].name if "stt" in states else None,
                       "tts": states["tts"].name if "tts" in states else None},
            agent=agent_cfg, metadata=req.metadata,
        )
        await self.repo.create_session(rec)
        self.attach_runtime(rec)
        logger.info("session {} created mode={} simulated={}", rec.id, rec.mode, simulated)
        return await self.to_status(rec)

    def attach_runtime(self, rec: SessionRecord) -> Runtime:
        prompt = rec.agent.get("system_prompt") or self.s.system_prompt
        agent = VoiceAgent(rec.id, prompt, self.registry, self.executor, self.repo,
                           persist_transcripts=self.s.persist_transcripts, max_steps=self.s.max_tool_steps)
        rt = Runtime(agent=agent, llm=self.text_llm() if rec.mode == "text" else None)
        self.runtimes[rec.id] = rt
        return rt

    async def _get_record(self, session_id: str) -> SessionRecord:
        rec = await self.repo.get_session(_uuid_or_404(session_id))
        if not rec:
            raise not_found("Session", session_id)
        return rec

    async def get(self, session_id: str) -> C.SessionStatus:
        return await self.to_status(await self._get_record(session_id))

    async def list(self, page: int, page_size: int, status: str | None) -> C.SessionList:
        items, total = await self.repo.list_sessions(page, page_size, status)
        return C.SessionList(items=[await self.to_status(i, with_details=False) for i in items], page=page, page_size=page_size, total=total)

    async def require_runtime(self, session_id: str, *, mode: str | None = None, allow_ended: bool = False) -> tuple[SessionRecord, Runtime]:
        rec = await self._get_record(session_id)
        if mode and rec.mode != mode:
            raise AppError("CONFLICT", f"Session {rec.id} has mode '{rec.mode}', expected '{mode}'")
        if rec.status in ("ended", "failed") and not allow_ended:
            raise AppError("CONFLICT", f"Session {rec.id} is {rec.status}")
        rt = self.runtimes.get(rec.id)
        if rt is None:
            rt = self.attach_runtime(rec)
        return rec, rt

    async def touch(self, session_id: str, status: str | None = None) -> None:
        fields: dict[str, Any] = {"last_activity_at": now()}
        if status:
            fields["status"] = status
        await self.repo.update_session(session_id, **fields)

    async def send_message(self, session_id: str, text: str) -> C.MessageReply:
        rec, rt = await self.require_runtime(session_id, mode="text")
        assert rt.llm is not None
        async with rt.lock:
            try:
                turn = await rt.agent.text_turn(rt.llm, text)
            except AppError:
                await self.touch(rec.id)
                raise
            await self.touch(rec.id, "active")
        traces = [C.ToolTraceOut(tool=o.tool, arguments={}, result=o.result, status=o.status, at=now(), latency_ms=o.latency_ms)
                  for o in turn.tool_outcomes]
        # attach arguments from the tool messages (same order)
        tool_msgs = [m for m in turn.messages if m.role == "tool"]
        for tr, m in zip(traces, tool_msgs, strict=False):
            tr.arguments = (m.tool_call or {}).get("arguments", {})
        msgs = [self._msg(m) for m in turn.messages]
        return C.MessageReply(session_id=rec.id, reply=msgs[-1], messages=msgs, tool_calls=traces,
                              simulated=rt.llm.simulated, provider={"name": rt.llm.name, "model": rt.llm.model})

    async def end(self, session_id: str, reason: str | None = None, *, failed: bool = False) -> C.SessionStatus:
        rec = await self._get_record(session_id)
        if rec.status in ("ended", "failed"):
            return await self.to_status(rec)
        await self._teardown(rec.id)
        rec = await self.repo.update_session(rec.id, status="failed" if failed else "ended", ended_at=now(),
                                             error=reason if failed or reason else None)
        logger.info("session {} {}", rec.id, rec.status)
        return await self.to_status(rec)

    async def _teardown(self, session_id: str) -> None:
        rt = self.runtimes.pop(session_id, None)
        if rt is None:
            return
        rt.closing = True
        if rt.worker is not None:
            try:
                await asyncio.wait_for(rt.worker.cancel(reason="session ended"), timeout=5)
            except Exception as e:  # teardown must never raise
                logger.warning("worker cancel failed for {}: {}", session_id, type(e).__name__)
        if rt.runner_task is not None and not rt.runner_task.done():
            try:
                await asyncio.wait_for(rt.runner_task, timeout=5)
            except (TimeoutError, asyncio.CancelledError, Exception):
                rt.runner_task.cancel()

    async def fail(self, session_id: str, error: str) -> None:
        try:
            await self.end(session_id, error, failed=True)
        except AppError:
            logger.warning("could not mark session {} failed", session_id)

    # ---------- approvals ----------
    async def resolve_approval(self, session_id: str, approval_id: str, decision: str) -> C.ApprovalResult:
        rec, rt = await self.require_runtime(session_id, allow_ended=False)
        approval, token = await self.approvals.resolve(rec.id, approval_id, decision)
        tool_result: dict[str, Any] | None = None
        if decision == "reject":
            text = f"Human rejected {approval.tool}; it was NOT executed."
            tc = {"name": approval.tool, "arguments": approval.arguments, "result": {"approvalId": approval.id}, "status": "rejected"}
            await rt.agent.note_external_result(text, tc)
        elif approval.tool == "request_human_approval":
            await self.approvals.repo.update_approval(approval.id, status="consumed")
            text = f"Human approved: {approval.arguments.get('action')}. You may proceed."
            tc = {"name": approval.tool, "arguments": approval.arguments, "result": {"approvalId": approval.id}, "status": "ok"}
            await rt.agent.note_external_result(text, tc)
        else:
            outcome = await self.executor.execute(rec.id, approval.tool, approval.arguments, approval_token=token)
            tool_result = outcome.for_llm()
            tc = {"name": approval.tool, "arguments": approval.arguments, "result": outcome.result, "status": outcome.status}
            await rt.agent.note_external_result(f"Human approved; {approval.tool} -> {outcome.status}", tc)
        if rt.notify:
            try:
                await rt.notify(f"The human {decision}d the pending {approval.tool} request.")
            except Exception as e:
                logger.warning("voice notify failed: {}", type(e).__name__)
        approval = await self.repo.get_approval(approval.id) or approval
        return C.ApprovalResult(approval=self._approval(approval), tool_result=tool_result)

    # ---------- cleanup ----------
    async def cleanup_expired(self) -> int:
        cutoff = now() - timedelta(seconds=self.s.session_ttl_seconds)
        ids = await self.repo.find_expired_sessions(cutoff)
        for sid in ids:
            try:
                await self.end(sid, "expired (session TTL)")
            except Exception as e:
                logger.warning("cleanup of {} failed: {}", sid, type(e).__name__)
        if ids:
            logger.info("cleaned up {} expired sessions", len(ids))
        return len(ids)

    async def shutdown(self) -> None:
        for sid in list(self.runtimes):
            await self._teardown(sid)

    # ---------- mapping ----------
    @staticmethod
    def _msg(m) -> C.MessageOut:
        return C.MessageOut(role=m.role, text=m.text, at=m.at, tool_call=m.tool_call)

    @staticmethod
    def _approval(a: ApprovalRecord) -> C.PendingApproval:
        return C.PendingApproval(id=a.id, tool=a.tool, arguments=a.arguments, reason=a.reason, status=a.status, expires_at=a.expires_at)

    async def to_status(self, rec: SessionRecord, with_details: bool = True) -> C.SessionStatus:
        msgs, traces, approvals = [], [], []
        if with_details:
            msgs = [self._msg(m) for m in await self.repo.list_messages(rec.id)]
            traces = [C.ToolTraceOut(tool=t.tool, arguments=t.arguments, result=t.result, status=t.status, at=t.at, latency_ms=t.latency_ms)
                      for t in await self.repo.list_traces(rec.id)]
            approvals = [self._approval(a) for a in await self.repo.list_approvals(rec.id)]
        return C.SessionStatus(
            id=rec.id, mode=rec.mode, status=rec.status, transport=rec.transport,  # type: ignore[arg-type]
            providers=C.ProvidersUsed(**{k: rec.providers.get(k) for k in ("llm", "stt", "tts")}),
            messages=msgs, tool_trace=traces, simulated=rec.simulated, error=rec.error, created_at=rec.created_at,
            ended_at=rec.ended_at, approvals=approvals, metadata=rec.metadata,
            transcripts_persisted=self.s.persist_transcripts,
        )

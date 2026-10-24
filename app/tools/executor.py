"""Guarded tool execution: schema validation, permission check, approval gate, timeout, trace."""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import jsonschema
from loguru import logger

from ..domain import ApprovalRecord, TraceRecord, now
from ..errors import AppError, not_found
from .registry import ToolContext, ToolFailure, ToolRegistry, ToolUnavailable


@dataclass
class ToolOutcome:
    tool: str
    status: str  # ok | error | pending_approval | rejected | forbidden | unavailable
    result: dict[str, Any] = field(default_factory=dict)
    latency_ms: int = 0

    def for_llm(self) -> dict[str, Any]:
        """The payload the LLM sees (and speaks from). Always explicit about status."""
        return {"status": self.status, **self.result}


def _args_hash(tool: str, args: dict) -> str:
    return hashlib.sha256(json.dumps([tool, args], sort_keys=True, default=str).encode()).hexdigest()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class ApprovalService:
    def __init__(self, repo, ttl_seconds: int):
        self.repo = repo
        self.ttl = ttl_seconds

    async def create_pending(self, session_id: str, tool: str, args: dict, reason: str) -> ApprovalRecord:
        a = ApprovalRecord(session_id=session_id, tool=tool, arguments=args, reason=reason,
                           expires_at=now() + timedelta(seconds=self.ttl))
        return await self.repo.create_approval(a)

    async def resolve(self, session_id: str, approval_id: str, decision: str) -> tuple[ApprovalRecord, str | None]:
        """Human decision. Returns (record, plaintext token when approved). Token is single-use."""
        a = await self.repo.get_approval(approval_id)
        if not a or a.session_id != session_id:
            raise not_found("Approval", approval_id)
        if a.status != "pending":
            raise AppError("CONFLICT", f"Approval is already {a.status}")
        if a.expires_at < now():
            await self.repo.update_approval(a.id, status="expired", resolved_at=now())
            raise AppError("CONFLICT", "Approval has expired")
        if decision == "reject":
            a = await self.repo.update_approval(a.id, status="rejected", resolved_at=now())
            return a, None
        token = secrets.token_urlsafe(24)
        a = await self.repo.update_approval(a.id, status="approved", token_hash=_token_hash(token), resolved_at=now())
        return a, token

    async def consume(self, session_id: str, tool: str, args: dict, token: str) -> bool:
        """Validate+burn a token bound to (session, tool, args)."""
        th = _token_hash(token)
        for a in await self.repo.list_approvals(session_id):
            if a.status == "approved" and a.token_hash == th:
                if a.tool != tool or _args_hash(a.tool, a.arguments) != _args_hash(tool, args) or a.expires_at < now():
                    return False
                await self.repo.update_approval(a.id, status="consumed")
                return True
        return False


class ToolExecutor:
    def __init__(self, registry: ToolRegistry, repo, settings, approvals: ApprovalService, http_transport=None):
        self.registry, self.repo, self.settings, self.approvals = registry, repo, settings, approvals
        self.http_transport = http_transport

    def _ctx(self, session_id: str) -> ToolContext:
        return ToolContext(session_id=session_id, repo=self.repo, settings=self.settings,
                           approvals=self.approvals, http=self.http_transport)

    async def execute(self, session_id: str, name: str, arguments: Any, approval_token: str | None = None,
                      *, record: bool = True) -> ToolOutcome:
        t0 = time.perf_counter()
        outcome = await self._run(session_id, name, arguments, approval_token)
        outcome.latency_ms = int((time.perf_counter() - t0) * 1000)
        if record:
            args = arguments if isinstance(arguments, dict) else {"_raw": str(arguments)}
            result = outcome.result
            if not self.settings.persist_transcripts:  # PERSIST_TRANSCRIPTS=false: keep status/timing, drop content
                args, result = {}, {"redacted": True}
            try:
                await self.repo.add_trace(TraceRecord(session_id=session_id, tool=name, arguments=args, result=result,
                                                      status=outcome.status, latency_ms=outcome.latency_ms))
            except AppError:
                logger.warning("could not persist tool trace for {}", name)
        return outcome

    async def _run(self, session_id: str, name: str, arguments: Any, token: str | None) -> ToolOutcome:
        spec = self.registry.get(name)
        if spec is None:
            return ToolOutcome(name, "error", {"code": "UNKNOWN_TOOL", "message": f"No tool named '{name}'"})
        if not isinstance(arguments, dict):
            return ToolOutcome(name, "error", {"code": "INVALID_INPUT", "message": "arguments must be an object"})
        try:
            jsonschema.validate(arguments, spec.input_schema)
        except jsonschema.ValidationError as e:
            path = ".".join(str(p) for p in e.absolute_path)
            return ToolOutcome(name, "error", {"code": "INVALID_INPUT", "message": e.message, "path": path})
        missing = [p for p in spec.permissions if p not in self.settings.granted_permissions]
        if missing:
            return ToolOutcome(name, "forbidden", {"code": "FORBIDDEN", "message": f"Missing permissions: {', '.join(missing)}"})
        if spec.requires_approval:
            if not token or not await self.approvals.consume(session_id, name, arguments, token):
                a = await self.approvals.create_pending(
                    session_id, name, arguments, f"{name} is a {spec.risk}-risk action that requires human approval")
                return ToolOutcome(name, "pending_approval", {
                    "approvalId": a.id, "expiresAt": a.expires_at.isoformat(),
                    "message": "A human must approve this action. Tell the user it is pending; do not say it was done."})
        try:
            result = await asyncio.wait_for(spec.handler(self._ctx(session_id), arguments), timeout=spec.timeout_ms / 1000)
            return ToolOutcome(name, "ok", result)
        except ToolFailure as e:
            return ToolOutcome(name, "error", {"code": e.code, "message": str(e)})
        except ToolUnavailable as e:
            return ToolOutcome(name, "unavailable", {"code": "UNAVAILABLE", "message": str(e)})
        except TimeoutError:
            return ToolOutcome(name, "error", {"code": "TIMEOUT", "message": f"Tool exceeded {spec.timeout_ms} ms"})
        except AppError as e:
            return ToolOutcome(name, "error", {"code": e.code, "message": e.message})
        except Exception as e:  # never leak internals to the model
            logger.exception("tool {} crashed", name)
            return ToolOutcome(name, "error", {"code": "INTERNAL", "message": f"Tool failed unexpectedly ({type(e).__name__})"})

"""VoiceAgent: the transport-independent core (system prompt, tool registry access, conversation history).

Text (HTTP), browser (SmallWebRTC) and phone (Plivo) transports all go through one VoiceAgent per session so
tool guardrails, history and persistence behave identically.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from ..domain import MessageRecord
from ..errors import AppError
from ..tools.executor import ToolExecutor, ToolOutcome
from ..tools.registry import ToolRegistry
from .llm import ChatItem, TextLLM


@dataclass
class TurnResult:
    messages: list[MessageRecord] = field(default_factory=list)
    tool_outcomes: list[ToolOutcome] = field(default_factory=list)


class VoiceAgent:
    def __init__(self, session_id: str, system_prompt: str, registry: ToolRegistry, executor: ToolExecutor, repo,
                 *, persist_transcripts: bool, max_steps: int = 5):
        self.session_id, self.system_prompt = session_id, system_prompt
        self.registry, self.executor, self.repo = registry, executor, repo
        self.persist_transcripts, self.max_steps = persist_transcripts, max_steps
        self.history: list[ChatItem] = []

    async def add_message(self, role: str, text: str, tool_call: dict[str, Any] | None = None) -> MessageRecord:
        rec = MessageRecord(session_id=self.session_id, role=role, text=text, tool_call=tool_call)
        if self.persist_transcripts:
            await self.repo.add_message(rec)
        return rec

    async def call_tool(self, name: str, arguments: dict[str, Any], *, add_history: bool = True,
                        thought_signature: bytes | None = None) -> tuple[ToolOutcome, MessageRecord]:
        outcome = await self.executor.execute(self.session_id, name, arguments)
        tc = {"name": name, "arguments": arguments, "result": outcome.result, "status": outcome.status}
        if not self.persist_transcripts:
            tc = {"name": name, "status": outcome.status}
        msg = await self.add_message("tool", f"{name} -> {outcome.status}", tc)
        if add_history:
            self.history.append(ChatItem("tool", msg.text, {"name": name, "arguments": arguments,
                                                           "result": outcome.result, "status": outcome.status,
                                                           "thought_signature": thought_signature}))
        return outcome, msg

    async def note_external_result(self, text: str, tool_call: dict[str, Any]) -> MessageRecord:
        """Record something resolved outside the LLM loop (e.g. a human approval) so the model sees it next turn."""
        self.history.append(ChatItem("tool", text, tool_call))
        return await self.add_message("tool", text, tool_call)

    async def text_turn(self, llm: TextLLM, user_text: str) -> TurnResult:
        res = TurnResult()
        self.history.append(ChatItem("user", user_text))
        res.messages.append(await self.add_message("user", user_text))
        tools = self.registry.all()
        for _ in range(self.max_steps):
            reply = await llm.generate(self.system_prompt, self.history, tools)
            if reply.tool_calls:
                for tc in reply.tool_calls:
                    outcome, msg = await self.call_tool(tc.name, tc.arguments, thought_signature=tc.thought_signature)
                    res.messages.append(msg)
                    res.tool_outcomes.append(outcome)
                continue
            text = (reply.text or "").strip()
            if not text:
                raise AppError("PROVIDER_ERROR", f"{llm.name} returned an empty response")
            self.history.append(ChatItem("assistant", text))
            res.messages.append(await self.add_message("assistant", text))
            return res
        logger.warning("session {} hit max tool steps ({})", self.session_id, self.max_steps)
        raise AppError("PROVIDER_ERROR", f"Model kept calling tools for {self.max_steps} steps without answering")

"""Text-path LLM port: Gemini via the official Google Gen AI SDK, plus an explicit deterministic mock."""

from __future__ import annotations

import asyncio
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..errors import AppError
from ..tools.registry import ToolSpec


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    # Gemini 3.x returns an opaque thought_signature with a function call and requires it echoed back verbatim.
    thought_signature: bytes | None = None


@dataclass
class LLMReply:
    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)


@dataclass
class ChatItem:
    """Internal history item. role: user | assistant | tool (tool = call + result)."""
    role: str
    text: str = ""
    tool_call: dict[str, Any] | None = None  # {id,name,arguments,result,status}


class TextLLM(Protocol):
    name: str
    model: str
    simulated: bool

    async def generate(self, system_prompt: str, history: list[ChatItem], tools: list[ToolSpec]) -> LLMReply: ...


# ---------------- mock ----------------
_MATH = re.compile(r"(?:calculate|compute|what is|what's|whats)\s+(?P<e>[\d\s\.\+\-\*/%\(\)\^a-z,]+?)\s*\??$", re.I)
_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"


class MockLLM:
    """Deterministic rule-based stand-in. Only used when LLM_PROVIDER=mock; responses are flagged simulated."""

    name, model, simulated = "mock", "mock-rules-v1", True

    async def generate(self, system_prompt: str, history: list[ChatItem], tools: list[ToolSpec]) -> LLMReply:
        names = {t.name for t in tools}
        last = history[-1] if history else ChatItem("user", "")
        if last.role == "tool" and last.tool_call:
            return LLMReply(text=self._summarize(last.tool_call))
        text = last.text.strip()
        low = text.lower()

        def call(name: str, **args: Any) -> LLMReply:
            if name not in names:
                return LLMReply(text=f"[mock] The {name} tool is not available.")
            return LLMReply(tool_calls=[ToolCall(name, args)])

        if m := re.search(rf"\bdelete task\s+({_UUID})", text, re.I):
            return call("delete_task", id=m.group(1))
        if m := re.search(rf"\b(?:complete|finish) task\s+({_UUID})", text, re.I):
            return call("update_task", id=m.group(1), status="done")
        if m := re.search(r"\bget task\s+(\S+)", text, re.I):
            return call("get_task", id=m.group(1))
        if "list tasks" in low or "show tasks" in low or "my tasks" in low:
            return call("list_tasks")
        if m := re.search(r"\bcreate (?:a )?task(?: called| named| to|:)?\s+(.+)", text, re.I):
            return call("create_task", title=m.group(1).strip().rstrip("."))
        if m := re.search(r"\b(?:search|look up|google)(?: for)?\s+(.+)", text, re.I):
            return call("web_search", query=m.group(1).strip())
        if m := re.search(r"\bask (?:a human|for approval)(?: to| for)?\s*(.*)", text, re.I):
            return call("request_human_approval", action=m.group(1).strip() or "unspecified action")
        if (m := _MATH.search(text)) and re.search(r"\d", m.group("e")):
            return call("calculator", expression=m.group("e").replace("^", "**"))
        if re.fullmatch(r"[\d\s\.\+\-\*/%\(\)]+", text) and re.search(r"\d", text):
            return call("calculator", expression=text)
        return LLMReply(text=f"[mock] I heard: {text}. Try 'calculate 2+2' or 'create task buy milk'.")

    @staticmethod
    def _summarize(tc: dict[str, Any]) -> str:
        status, res, name = tc.get("status"), tc.get("result") or {}, tc.get("name")
        if status == "ok":
            if name == "calculator":
                return f"[mock] The result is {res.get('value')}."
            if name == "create_task":
                t = res.get("task", {})
                return f"[mock] Created task '{t.get('title')}' with id {t.get('id')}."
            if name == "list_tasks":
                return f"[mock] You have {res.get('count', 0)} tasks."
            if name == "web_search":
                return f"[mock] Search returned {len(res.get('results', []))} results."
            return f"[mock] {name} completed."
        if status == "pending_approval":
            return "[mock] That needs human approval. It is pending and has not been done."
        if status == "unavailable":
            return f"[mock] {name} is unavailable: {res.get('message')}"
        return f"[mock] {name} failed: {res.get('message', status)}"


# ---------------- gemini (official SDK) ----------------
def _clean_schema(schema: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in schema.items() if k != "additionalProperties"}
    if "properties" in out:
        out["properties"] = {k: _clean_schema(v) for k, v in out["properties"].items()}
    return out


class GeminiLLM:
    name, simulated = "gemini", False

    def __init__(self, api_key: str, model: str, timeout_s: float):
        from google import genai

        self.model, self._timeout = model, timeout_s
        self._client = genai.Client(api_key=api_key)

    def _contents(self, history: list[ChatItem]):
        from google.genai import types

        out = []
        for h in history:
            if h.role == "user":
                out.append(types.Content(role="user", parts=[types.Part(text=h.text)]))
            elif h.role == "assistant":
                out.append(types.Content(role="model", parts=[types.Part(text=h.text)]))
            elif h.role == "tool" and h.tool_call:
                tc = h.tool_call
                sig = tc.get("thought_signature") or b"skip_thought_signature_validator"  # documented fallback when none was captured
                out.append(types.Content(role="model", parts=[types.Part(
                    function_call=types.FunctionCall(name=tc["name"], args=tc["arguments"]), thought_signature=sig)]))
                out.append(types.Content(role="user", parts=[types.Part.from_function_response(
                    name=tc["name"], response={"status": tc.get("status"), **(tc.get("result") or {})})]))
        return out

    async def generate(self, system_prompt: str, history: list[ChatItem], tools: list[ToolSpec]) -> LLMReply:
        from google.genai import errors, types

        decls = [types.FunctionDeclaration(name=t.name, description=t.description,
                                           parameters_json_schema=_clean_schema(t.input_schema)) for t in tools]
        cfg = types.GenerateContentConfig(
            system_instruction=system_prompt,
            tools=[types.Tool(function_declarations=decls)] if decls else None,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        try:
            resp = await asyncio.wait_for(
                self._client.aio.models.generate_content(model=self.model, contents=self._contents(history), config=cfg),
                timeout=self._timeout,
            )
        except TimeoutError as e:
            raise AppError("TIMEOUT", f"Gemini did not respond within {self._timeout:.0f}s") from e
        except errors.APIError as e:
            code = getattr(e, "code", 0)
            if code == 429:
                raise AppError("PROVIDER_RATE_LIMITED", "Gemini rate limit hit") from e
            if code in (401, 403):
                raise AppError("PROVIDER_ERROR", f"Gemini rejected the credentials (HTTP {code}); check GEMINI_API_KEY") from e
            raise AppError("PROVIDER_ERROR", f"Gemini error (HTTP {code}): {getattr(e, 'message', 'unknown')}"[:300]) from e
        calls = []
        for cand in (resp.candidates or [])[:1]:
            for part in (cand.content.parts if cand.content and cand.content.parts else []):
                if part.function_call:
                    calls.append(ToolCall(part.function_call.name, dict(part.function_call.args or {}),
                                          thought_signature=part.thought_signature))
        text = None
        try:
            text = resp.text
        except Exception:  # no text parts
            text = None
        return LLMReply(text=text, tool_calls=calls)

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

ToolHandler = Callable[["ToolContext", dict[str, Any]], Awaitable[dict[str, Any]]]


class ToolUnavailable(Exception):
    """Tool is registered but its backing integration is not configured / reachable."""


class ToolFailure(Exception):
    """Expected, user-presentable tool failure (bad input semantics, not found, ...)."""

    def __init__(self, message: str, code: str = "TOOL_ERROR"):
        super().__init__(message)
        self.code = code


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler
    risk: str = "low"  # low | medium | high
    permissions: list[str] = field(default_factory=list)
    timeout_ms: int = 5000
    requires_approval: bool = False
    source: str = "local"  # local | agent-proxy

    def contract(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
            "risk": self.risk,
            "permissions": self.permissions,
            "timeoutMs": self.timeout_ms,
            "requiresApproval": self.requires_approval,
        }


@dataclass
class ToolContext:
    session_id: str
    repo: Any
    settings: Any
    approvals: Any  # ApprovalService
    http: Any = None  # httpx.AsyncClient factory result (optional override for tests)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"duplicate tool {spec.name}")
        self._tools[spec.name] = spec

    def replace_source(self, source: str, specs: list[ToolSpec]) -> None:
        for n in [n for n, t in self._tools.items() if t.source == source]:
            del self._tools[n]
        for s in specs:
            self._tools[s.name] = s

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def all(self) -> list[ToolSpec]:
        return list(self._tools.values())

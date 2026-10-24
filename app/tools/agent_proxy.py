"""Optional proxy to repo-agent's tool registry (`AGENT_API_URL`). Untested against a live repo-agent."""

from __future__ import annotations

import httpx
from loguru import logger

from ..errors import request_id_var
from .registry import ToolContext, ToolFailure, ToolSpec, ToolUnavailable

PREFIX = "agent_"


def _headers(settings) -> dict[str, str]:
    h = {"X-Request-Id": request_id_var.get()}
    if settings.agent_api_key:
        h["X-Api-Key"] = settings.agent_api_key
    return h


async def fetch_remote_specs(settings, transport=None) -> list[ToolSpec]:
    if not settings.agent_api_url:
        return []
    base = settings.agent_api_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=settings.tool_http_timeout_seconds, transport=transport) as c:
            r = await c.get(f"{base}/api/v1/tools", headers=_headers(settings))
            r.raise_for_status()
            items = r.json()
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("AGENT_API_URL tool discovery failed: {}", type(e).__name__)
        return []
    if isinstance(items, dict):
        items = items.get("items", [])
    specs = []
    for t in items:
        remote = t["name"]

        async def handler(ctx: ToolContext, args: dict, _remote=remote):
            return await _execute_remote(ctx, _remote, args)

        specs.append(ToolSpec(
            name=PREFIX + remote, description=f"[repo-agent] {t.get('description', '')}",
            input_schema=t.get("inputSchema") or {"type": "object", "properties": {}},
            handler=handler, risk=t.get("risk", "medium"), permissions=["agent:invoke"],
            timeout_ms=int(t.get("timeoutMs", 10000)) + 2000,
            requires_approval=bool(t.get("requiresApproval", False)), source="agent-proxy",
        ))
    return specs


async def _execute_remote(ctx: ToolContext, name: str, args: dict) -> dict:
    s = ctx.settings
    if not s.agent_api_url:
        raise ToolUnavailable("AGENT_API_URL is not configured")
    try:
        async with httpx.AsyncClient(timeout=s.tool_http_timeout_seconds, transport=ctx.http) as c:
            r = await c.post(f"{s.agent_api_url.rstrip('/')}/api/v1/tools/{name}/execute",
                             json={"arguments": args}, headers=_headers(s))
    except httpx.HTTPError as e:
        raise ToolUnavailable(f"repo-agent unreachable: {type(e).__name__}") from e
    if r.status_code >= 500:
        raise ToolUnavailable(f"repo-agent error {r.status_code}")
    if r.status_code >= 400:
        try:
            msg = r.json().get("error", {}).get("message", r.text[:200])
        except ValueError:
            msg = r.text[:200]
        raise ToolFailure(f"repo-agent rejected the call: {msg}", f"AGENT_{r.status_code}")
    data = r.json()
    return data if isinstance(data, dict) else {"result": data}

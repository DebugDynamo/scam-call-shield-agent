"""Domain-neutral built-in tools. Add a tool = write a handler + a ToolSpec in `build_registry`."""

from __future__ import annotations

import ast
import math
import operator
from typing import Any

import httpx

from ..domain import TaskRecord
from ..services.scam_shield import (
    SCENARIOS,
    ScamAnalysisResult,
    analyze_deterministic_mock,
    build_emergency_sms,
)
from .registry import ToolContext, ToolFailure, ToolRegistry, ToolSpec, ToolUnavailable

# ---------- calculator (safe AST evaluator, no eval) ----------
_BIN = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow,
}
_UN = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCS = {"sqrt": math.sqrt, "abs": abs, "round": round, "min": min, "max": max}
_CONSTS = {"pi": math.pi, "e": math.e}


def safe_eval(expr: str) -> float | int:
    if len(expr) > 200:
        raise ToolFailure("Expression too long", "INVALID_EXPRESSION")

    def ev(n: ast.AST) -> Any:
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, int | float) and not isinstance(n.value, bool):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _BIN:
            left, right = ev(n.left), ev(n.right)
            if isinstance(n.op, ast.Pow) and abs(right) > 100:
                raise ToolFailure("Exponent too large", "INVALID_EXPRESSION")
            return _BIN[type(n.op)](left, right)
        if isinstance(n, ast.UnaryOp) and type(n.op) in _UN:
            return _UN[type(n.op)](ev(n.operand))
        if isinstance(n, ast.Name) and n.id in _CONSTS:
            return _CONSTS[n.id]
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _FUNCS and not n.keywords:
            return _FUNCS[n.func.id](*[ev(a) for a in n.args])
        raise ToolFailure("Unsupported expression; use numbers, + - * / // % **, parentheses, sqrt/abs/round/min/max", "INVALID_EXPRESSION")

    try:
        return ev(ast.parse(expr.strip(), mode="eval"))
    except ZeroDivisionError as e:
        raise ToolFailure("Division by zero", "INVALID_EXPRESSION") from e
    except (SyntaxError, ValueError, OverflowError, TypeError) as e:
        raise ToolFailure(f"Cannot evaluate expression: {e}", "INVALID_EXPRESSION") from e


async def calculator(ctx: ToolContext, args: dict) -> dict:
    value = safe_eval(args["expression"])
    return {"expression": args["expression"], "value": value}


# ---------- search adapter ----------
async def web_search(ctx: ToolContext, args: dict) -> dict:
    s = ctx.settings
    if not s.search_api_url:
        raise ToolUnavailable("Search is not configured: set SEARCH_API_URL (and optionally SEARCH_API_KEY)")
    headers = {"Authorization": f"Bearer {s.search_api_key}"} if s.search_api_key else {}
    try:
        async with httpx.AsyncClient(timeout=s.tool_http_timeout_seconds, transport=ctx.http) as c:
            r = await c.get(s.search_api_url, params={"q": args["query"]}, headers=headers)
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPError as e:
        raise ToolUnavailable(f"Search backend failed: {type(e).__name__}") from e
    return {"query": args["query"], "results": data if isinstance(data, list) else data.get("results", data)}


# ---------- tasks ----------
def _task_view(t: TaskRecord) -> dict:
    return {"id": t.id, "title": t.title, "description": t.description, "status": t.status, "priority": t.priority}


async def create_task(ctx: ToolContext, args: dict) -> dict:
    t = await ctx.repo.create_task(
        TaskRecord(title=args["title"].strip(), description=args.get("description"), priority=args.get("priority", "medium"))
    )
    return {"task": _task_view(t)}


async def get_task(ctx: ToolContext, args: dict) -> dict:
    t = await _find_task(ctx, args["id"])
    return {"task": _task_view(t)}


async def list_tasks(ctx: ToolContext, args: dict) -> dict:
    items = await ctx.repo.list_tasks(status=args.get("status"), limit=args.get("limit", 20))
    return {"tasks": [_task_view(t) for t in items], "count": len(items)}


async def update_task(ctx: ToolContext, args: dict) -> dict:
    await _find_task(ctx, args["id"])
    fields = {k: args[k] for k in ("title", "description", "status", "priority") if k in args}
    if not fields:
        raise ToolFailure("Provide at least one field to update", "INVALID_INPUT")
    t = await ctx.repo.update_task(args["id"], **fields)
    return {"task": _task_view(t)}


async def delete_task(ctx: ToolContext, args: dict) -> dict:
    t = await _find_task(ctx, args["id"])
    await ctx.repo.delete_task(t.id)
    return {"deleted": _task_view(t)}


async def _find_task(ctx: ToolContext, task_id: str) -> TaskRecord:
    import uuid

    try:
        uuid.UUID(task_id)
    except ValueError as e:
        raise ToolFailure(f"'{task_id}' is not a valid task id (expected UUID)", "INVALID_INPUT") from e
    t = await ctx.repo.get_task(task_id)
    if not t:
        raise ToolFailure(f"Task {task_id} not found", "NOT_FOUND")
    return t


async def request_human_approval(ctx: ToolContext, args: dict) -> dict:
    a = await ctx.approvals.create_pending(
        ctx.session_id, "request_human_approval", {"action": args["action"]}, args.get("reason", "")
    )
    return {"approvalId": a.id, "status": "pending", "message": "Waiting for a human to approve or reject. Do not proceed until told."}


async def analyze_call_scam(ctx: ToolContext, args: dict) -> dict:
    scenario_id = args.get("scenario_id")
    transcript = (args.get("transcript") or "").strip()
    if scenario_id and scenario_id in SCENARIOS and not transcript:
        transcript = SCENARIOS[scenario_id].transcript
    if not transcript and not scenario_id:
        raise ToolFailure("Provide a transcript or valid scenario_id", "INVALID_INPUT")
    res: ScamAnalysisResult = analyze_deterministic_mock(transcript, scenario_id=scenario_id)
    sms = build_emergency_sms(res, recipient=args.get("recipient", "Son (Rahul)"))
    return {
        **res.model_dump(),
        "emergency_dispatch": sms.model_dump() if sms else None,
    }


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or [], "additionalProperties": False}


def build_registry() -> ToolRegistry:
    r = ToolRegistry()
    r.register(ToolSpec(
        "analyze_call_scam",
        "Analyze live call transcript or scenario for Indian phone scams (Digital Arrest, Fake Police/Cyber Cell, Bank KYC OTP fraud) and trigger emergency SMS alert if confidence >= 0.70.",
        _obj({
            "transcript": {"type": "string", "minLength": 1, "maxLength": 4000},
            "scenario_id": {
                "type": "string",
                "enum": ["gujarati_digital_arrest", "hindi_digital_arrest", "english_bank_kyc", "genuine_delivery_control"],
            },
            "recipient": {"type": "string", "maxLength": 100},
        }),
        analyze_call_scam, risk="low", permissions=["scam:analyze"], timeout_ms=10000,
    ))
    r.register(ToolSpec(
        "calculator", "Evaluate an arithmetic expression exactly. Use for any math.",
        _obj({"expression": {"type": "string", "description": "e.g. (12.5 + 3) * 4", "minLength": 1, "maxLength": 200}}, ["expression"]),
        calculator, permissions=["calc:use"], timeout_ms=1000,
    ))
    r.register(ToolSpec(
        "web_search", "Search the web for current information. May report that search is unavailable.",
        _obj({"query": {"type": "string", "minLength": 1, "maxLength": 300}}, ["query"]),
        web_search, risk="low", permissions=["search:read"], timeout_ms=12000,
    ))
    r.register(ToolSpec(
        "create_task", "Create a task in the task store.",
        _obj({
            "title": {"type": "string", "minLength": 1, "maxLength": 200},
            "description": {"type": "string", "maxLength": 2000},
            "priority": {"type": "string", "enum": ["low", "medium", "high"]},
        }, ["title"]),
        create_task, risk="medium", permissions=["tasks:write"],
    ))
    r.register(ToolSpec(
        "get_task", "Get one task by id.", _obj({"id": {"type": "string"}}, ["id"]),
        get_task, permissions=["tasks:read"],
    ))
    r.register(ToolSpec(
        "list_tasks", "List tasks, optionally filtered by status.",
        _obj({"status": {"type": "string", "enum": ["todo", "in_progress", "done"]}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}}),
        list_tasks, permissions=["tasks:read"],
    ))
    r.register(ToolSpec(
        "update_task", "Update a task's title, description, status or priority.",
        _obj({
            "id": {"type": "string"},
            "title": {"type": "string", "minLength": 1, "maxLength": 200},
            "description": {"type": "string", "maxLength": 2000},
            "status": {"type": "string", "enum": ["todo", "in_progress", "done"]},
            "priority": {"type": "string", "enum": ["low", "medium", "high"]},
        }, ["id"]),
        update_task, risk="medium", permissions=["tasks:write"],
    ))
    r.register(ToolSpec(
        "delete_task", "Permanently delete a task. Consequential: requires human approval.",
        _obj({"id": {"type": "string"}}, ["id"]),
        delete_task, risk="high", permissions=["tasks:write"], requires_approval=True,
    ))
    r.register(ToolSpec(
        "request_human_approval",
        "Ask a human to approve some other consequential action before you do it. Returns a pending approval id.",
        _obj({"action": {"type": "string", "minLength": 1, "maxLength": 500}, "reason": {"type": "string", "maxLength": 500}}, ["action"]),
        request_human_approval, risk="low", permissions=["approvals:request"],
    ))
    return r

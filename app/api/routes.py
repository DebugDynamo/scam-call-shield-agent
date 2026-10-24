from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile, WebSocket
from fastapi.responses import HTMLResponse

from ..contracts import models as C
from ..errors import AppError  # noqa: F401
from ..providers import status as pstatus
from ..services.scam_shield import SCENARIOS, AnalyzeChunkResponse, analyze_call_chunk_with_settings
from ..tools.agent_proxy import fetch_remote_specs
from .deps import container, require_api_key

ERR = {400: {"model": C.ErrorResponse}, 404: {"model": C.ErrorResponse}, 409: {"model": C.ErrorResponse},
       503: {"model": C.ErrorResponse}}

root = APIRouter()
v1 = APIRouter(prefix="/api/v1")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _info(st: pstatus.ProviderState) -> C.ProviderInfo:
    return C.ProviderInfo(kind=st.kind, name=st.name, status=st.status, model=st.model, missing=st.missing, simulated=st.simulated)  # type: ignore[arg-type]


# ---------------- health ----------------
@root.get("/health", response_model=C.Health, tags=["health"])
async def health(request: Request):
    s = request.app.state.c.settings
    return C.Health(service=s.service_name, version=s.version, data_mode=s.data_mode, time=_now())


@root.get("/ready", response_model=C.Ready, responses={503: {"model": C.Ready}}, tags=["health"])
async def ready(request: Request, response: Response):
    c = request.app.state.c
    s = c.settings
    if s.data_mode == "postgres":
        try:
            db = C.DbCheck(status="up", latency_ms=await c.repo.ping())
        except AppError:
            db = C.DbCheck(status="down")
    else:
        db = C.DbCheck(status="skipped")
    llm, stt, tts = pstatus.llm_state(s), pstatus.stt_state(s), pstatus.tts_state(s)

    def chk(st):
        return C.ProviderCheck(status=st.status, name=st.name, model=st.model, missing=st.missing)  # type: ignore[arg-type]

    ok = db.status != "down" and llm.status != "not_configured"
    response.status_code = 200 if ok else 503
    return C.Ready(status="ready" if ok else "not_ready", service=s.service_name, data_mode=s.data_mode,
                   checks=C.ReadyChecks(database=db, provider=chk(llm), stt=chk(stt), tts=chk(tts)), simulated=llm.simulated)


# ---------------- sessions ----------------
@v1.post("/sessions", response_model=C.SessionStatus, status_code=201, responses=ERR, tags=["sessions"])
async def create_session(body: C.SessionCreate, request: Request):
    return await request.app.state.c.sessions.create(body)


@v1.get("/sessions", response_model=C.SessionList, tags=["sessions"])
async def list_sessions(request: Request, page: Annotated[int, Query(ge=1)] = 1,
                        page_size: Annotated[int, Query(ge=1, le=100, alias="pageSize")] = 20, status: str | None = None):
    return await request.app.state.c.sessions.list(page, page_size, status)


@v1.get("/sessions/{session_id}", response_model=C.SessionStatus, responses=ERR, tags=["sessions"])
async def get_session(session_id: str, request: Request):
    return await request.app.state.c.sessions.get(session_id)


@v1.post("/sessions/{session_id}/messages", response_model=C.MessageReply, responses=ERR, tags=["sessions"])
async def post_message(session_id: str, body: C.MessageCreate, request: Request):
    return await request.app.state.c.sessions.send_message(session_id, body.text)


@v1.post("/sessions/{session_id}/end", response_model=C.SessionStatus, responses=ERR, tags=["sessions"])
async def end_session(session_id: str, request: Request):
    return await request.app.state.c.sessions.end(session_id)


@v1.post("/sessions/{session_id}/approvals/{approval_id}", response_model=C.ApprovalResult, responses=ERR, tags=["sessions"],
         summary="(extension) approve or reject a pending consequential action")
async def decide_approval(session_id: str, approval_id: str, body: C.ApprovalDecision, request: Request):
    return await request.app.state.c.sessions.resolve_approval(session_id, approval_id, body.decision)


@v1.post("/sessions/{session_id}/offer", response_model=C.OfferResponse, responses=ERR, tags=["browser voice"])
async def offer(session_id: str, body: C.OfferRequest, request: Request):
    return await request.app.state.c.browser.offer(session_id, body)


@v1.patch("/sessions/{session_id}/ice", status_code=204, responses=ERR, tags=["browser voice"])
@v1.patch("/sessions/{session_id}/offer", status_code=204, include_in_schema=False)  # @pipecat-ai/client-js PATCHes the offer URL
async def ice(session_id: str, body: C.IcePatch, request: Request):
    await request.app.state.c.browser.ice(session_id, body)
    return Response(status_code=204)


# ---------------- tools / providers ----------------
@v1.get("/tools", response_model=list[C.ToolContract], tags=["tools"])
async def list_tools(request: Request):
    c = request.app.state.c
    if c.settings.agent_api_url:  # refresh proxied tools lazily (best-effort)
        specs = await fetch_remote_specs(c.settings, c.http_transport)
        if specs:
            c.registry.replace_source("agent-proxy", specs)
    return [C.ToolContract(**t.contract()) for t in c.registry.all()]


@v1.get("/providers", response_model=C.ProvidersResponse, tags=["providers"])
async def providers(request: Request):
    c = request.app.state.c
    s = c.settings
    return C.ProvidersResponse(
        llm=_info(pstatus.llm_state(s)), stt=_info(pstatus.stt_state(s)), tts=_info(pstatus.tts_state(s)),
        telephony={"provider": "plivo", **c.plivo.status()},
        room={"status": "not_implemented", "note": "interface only; no Meet/Daily/LiveKit integration"},
        persist_transcripts=s.persist_transcripts, data_mode=s.data_mode)


# ---------------- telephony ----------------
@v1.post("/telephony/outbound", response_model=C.OutboundCallResponse, dependencies=[Depends(require_api_key)],
         responses=ERR, tags=["telephony"])
async def outbound(body: C.OutboundCallRequest, request: Request):
    c = request.app.state.c
    c.plivo.require_configured(outbound=True)
    pstatus.require_for_mode(c.settings, "phone")
    sess = await c.sessions.create(C.SessionCreate(mode="phone", agent=body.agent, metadata={**body.metadata, "direction": "outbound"}))
    try:
        uuid_ = await c.plivo.place_call(body.to, sess.id)
    except AppError as e:
        await c.sessions.fail(sess.id, e.message)
        raise
    return C.OutboundCallResponse(session_id=sess.id, call_uuid=uuid_, status="queued")


async def _form(request: Request) -> dict:
    try:
        form = await request.form()
        return {k: str(v) for k, v in form.items()}
    except Exception:
        return {}


def _signed_url(request: Request, c) -> str:
    q = request.url.query
    return c.plivo.public_url(request.url.path + (f"?{q}" if q else ""))


@v1.post("/telephony/plivo/answer", tags=["telephony"], response_class=Response,
         responses={200: {"content": {"application/xml": {}}}})
async def plivo_answer(request: Request):
    c = request.app.state.c
    c.plivo.require_configured()
    c.plivo.verify_signature(_signed_url(request, c), request.headers.get("x-plivo-signature-v2-nonce"),
                             request.headers.get("x-plivo-signature-v2"))
    form = await _form(request)
    sid = request.query_params.get("session_id")
    if sid:
        await c.sessions.require_runtime(sid, mode="phone")
    elif c.settings.plivo_allow_inbound:
        pstatus.require_for_mode(c.settings, "phone")
        sess = await c.sessions.create(C.SessionCreate(mode="phone", metadata={"direction": "inbound", "from": form.get("From"), "to": form.get("To")}))
        sid = sess.id
    else:
        return Response(c.plivo.reject_xml(), media_type="application/xml")
    c.plivo.remember_call(form.get("CallUUID"), sid)
    return Response(c.plivo.answer_xml(sid), media_type="application/xml")


@v1.post("/telephony/plivo/hangup", tags=["telephony"])
async def plivo_hangup(request: Request):
    c = request.app.state.c
    c.plivo.require_configured()
    c.plivo.verify_signature(_signed_url(request, c), request.headers.get("x-plivo-signature-v2-nonce"),
                             request.headers.get("x-plivo-signature-v2"))
    form = await _form(request)
    sid = request.query_params.get("session_id") or c.plivo.session_for_call(form.get("CallUUID"))
    if sid:
        try:
            await c.sessions.end(sid, None)
        except AppError:
            pass
    return {"received": True}


ws_router = APIRouter()


@ws_router.websocket("/api/v1/telephony/plivo/stream/{session_id}/{token}")
async def plivo_stream(ws: WebSocket, session_id: str, token: str):
    await ws.app.state.c.plivo.run_stream(ws, session_id, token)


# ---------------- admin ----------------
@v1.post("/admin/cleanup", dependencies=[Depends(require_api_key)], tags=["admin"],
         summary="(extension) run the session TTL cleanup now")
async def admin_cleanup(request: Request):
    return {"ended": await request.app.state.c.sessions.cleanup_expired()}


# ---------------- scam call shield ----------------
_REPO_HTML = Path(__file__).resolve().parents[2] / "index.html"
_STANDALONE_HTML = _REPO_HTML if _REPO_HTML.exists() else (Path(__file__).resolve().parents[3] / "scam-call-shield" / "index.html")


@root.get("/scam-shield", response_class=HTMLResponse, include_in_schema=False)
async def serve_scam_shield_dashboard():
    if _STANDALONE_HTML.exists():
        return HTMLResponse(_STANDALONE_HTML.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Scam Call Shield index.html not found</h1>", status_code=404)


@root.get("/api/mode", tags=["scam-shield"])
@v1.get("/scam-shield/mode", tags=["scam-shield"])
async def get_scam_shield_mode(request: Request):
    s = request.app.state.c.settings
    return {
        "mode": s.llm_provider,
        "simulated": s.llm_provider == "mock",
        "model": s.gemini_model,
        "geminiKeyConfigured": bool(s.gemini_api_key),
    }


@root.post("/api/mode", tags=["scam-shield"])
@v1.post("/scam-shield/mode", tags=["scam-shield"])
async def set_scam_shield_mode(request: Request, body: dict):
    s = request.app.state.c.settings
    target = str(body.get("mode") or "").strip().lower()
    if target not in ("gemini", "mock"):
        raise AppError("VALIDATION_FAILED", "mode must be 'gemini' or 'mock'")
    s.llm_provider = target
    if body.get("model"):
        s.gemini_model = str(body["model"]).strip()
    return {
        "mode": s.llm_provider,
        "simulated": s.llm_provider == "mock",
        "model": s.gemini_model,
        "geminiKeyConfigured": bool(s.gemini_api_key),
    }


@root.get("/api/scenarios", tags=["scam-shield"])
@v1.get("/scam-shield/scenarios", tags=["scam-shield"])
async def list_scam_scenarios():
    return {
        "items": [s.model_dump() for s in SCENARIOS.values()],
        "total": len(SCENARIOS),
    }


@root.post("/api/analyze-chunk", response_model=AnalyzeChunkResponse, responses=ERR, tags=["scam-shield"])
@v1.post("/scam-shield/analyze-chunk", response_model=AnalyzeChunkResponse, responses=ERR, tags=["scam-shield"])
async def analyze_scam_chunk(
    request: Request,
    audio: Annotated[UploadFile | None, File(description="3-5s call audio chunk (WAV/WebM/OGG/MP3)")] = None,
    scenario_id: Annotated[str | None, Form(description="Optional pre-loaded scenario key")] = None,
    transcript: Annotated[str | None, Form(description="Optional caller transcript")] = None,
    recipient: Annotated[str, Form(description="Emergency contact name")] = "Son (Rahul)",
    mode: Annotated[str | None, Form(description="Optional override mode: 'gemini' or 'mock'")] = None,
) -> AnalyzeChunkResponse:
    audio_bytes: bytes | None = None
    mime_type = "audio/wav"
    if audio is not None:
        audio_bytes = await audio.read()
        if audio.content_type:
            mime_type = audio.content_type
    return await analyze_call_chunk_with_settings(
        request.app.state.c.settings,
        audio_bytes=audio_bytes if (audio_bytes and len(audio_bytes) > 0) else None,
        mime_type=mime_type,
        transcript=transcript,
        scenario_id=scenario_id,
        recipient=recipient,
        mode_override=mode,
    )


__all__ = ["container", "root", "v1", "ws_router"]

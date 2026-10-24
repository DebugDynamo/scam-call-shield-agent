"""Scam Call Shield — FastAPI Production Server.

Endpoints:
- `GET /`: Serves the interactive Tailwind CSS Demo Dashboard (`index.html`).
- `POST /api/analyze-chunk`: Receives 3-5s audio binary chunks via `UploadFile` (plus optional
  `scenario_id` / `transcript` form fields), executes the Gemini multimodal fraud analysis with
  strict JSON schema output, and attaches a mock emergency SMS/WhatsApp payload (`recipient: "Son (Rahul)"`,
  `status: "MOCK_SMS_SENT"`) when `is_scam=True` and `confidence_score >= 0.70`.
- `WS /ws/stream`: Real-time WebSocket chunk handler for live 3-5s audio chunk streaming.
- `GET /api/scenarios`: Lists the 4 pre-loaded test scenarios (Gujarati, Hindi, English KYC, Genuine Swiggy).
- `GET /health` & `GET /ready`: Garbathon v1 health & readiness contract endpoints.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from engine import (
    SCENARIOS,
    AnalyzeChunkResponse,
    ScamShieldEngine,
    ScamShieldEngineError,
    load_dotenv_files,
)

load_dotenv_files()

BASE_DIR = Path(__file__).resolve().parent
INDEX_HTML_PATH = BASE_DIR / "index.html"
SERVICE_NAME = "scam-call-shield"
SERVICE_VERSION = "1.0.0"

# Runtime-switchable state (allows switching between Real-Time Gemini and Demo Mode from the UI)
_RUNTIME_STATE: dict[str, str | None] = {
    "mode": None,   # "gemini" | "mock" | None (use env default)
    "model": None,  # e.g. "gemini-2.5-flash"
}


class ModeSwitchRequest(BaseModel):
    mode: str  # "gemini" | "mock"
    model: str | None = None


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def get_engine(override_mode: str | None = None) -> ScamShieldEngine:
    active_mode = (override_mode or _RUNTIME_STATE.get("mode") or os.getenv("LLM_PROVIDER") or "").strip().lower() or None
    active_model = (_RUNTIME_STATE.get("model") or os.getenv("GEMINI_MODEL") or "gemini-2.5-flash").strip()
    return ScamShieldEngine(
        provider=active_mode,
        api_key=os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"),
        model=active_model,
        timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "25.0")),
    )


def create_app() -> FastAPI:
    _RUNTIME_STATE["mode"] = None
    _RUNTIME_STATE["model"] = None
    app = FastAPI(
        title="Scam Call Shield API",
        version=SERVICE_VERSION,
        description=(
            "Active anti-fraud voice guardian for India. Processes live 3-5s call audio chunks "
            "using Google Gemini multimodal reasoning to intercept Digital Arrest, Fake Police/Cyber Cell, "
            "and Bank KYC scams in real time."
        ),
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    cors_env = os.getenv(
        "CORS_ORIGINS",
        "http://localhost:8080,http://127.0.0.1:8080,http://localhost:8003,http://localhost:5173,http://localhost:5170",
    )
    allowed_origins = [o.strip() for o in cors_env.split(",") if o.strip() and o.strip() != "*"]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-Api-Key", "X-Request-Id"],
        expose_headers=["X-Request-Id"],
    )

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        req_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        request.state.request_id = req_id
        response: Response = await call_next(request)
        response.headers["X-Request-Id"] = req_id
        return response

    @app.exception_handler(ScamShieldEngineError)
    async def handle_engine_error(request: Request, exc: ScamShieldEngineError):
        req_id = getattr(request.state, "request_id", uuid.uuid4().hex[:12])
        return JSONResponse(
            status_code=exc.status_code,
            headers={"X-Request-Id": req_id},
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "requestId": req_id,
                    "timestamp": _utc_now(),
                }
            },
        )

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def serve_dashboard():
        if not INDEX_HTML_PATH.exists():
            return HTMLResponse("<h1>index.html not found</h1>", status_code=404)
        return HTMLResponse(INDEX_HTML_PATH.read_text(encoding="utf-8"))

    @app.get("/health", tags=["health"])
    async def health():
        engine = get_engine()
        return {
            "status": "ok",
            "service": SERVICE_NAME,
            "version": SERVICE_VERSION,
            "dataMode": "mock" if engine.simulated else "live",
            "time": _utc_now(),
        }

    @app.get("/ready", tags=["health"])
    async def ready(response: Response):
        engine = get_engine()
        ok = engine.is_configured
        response.status_code = 200 if ok else 503
        missing = [] if ok else ["GEMINI_API_KEY"]
        return {
            "status": "ready" if ok else "not_ready",
            "service": SERVICE_NAME,
            "dataMode": "mock" if engine.simulated else "live",
            "simulated": engine.simulated,
            "checks": {
                "database": {"status": "skipped"},
                "provider": {
                    "status": "mock" if engine.simulated else ("configured" if ok else "not_configured"),
                    "name": engine.provider,
                    "model": engine.model,
                    "missing": missing,
                },
            },
        }

    @app.get("/api/mode", tags=["scam-shield"])
    async def get_mode():
        engine = get_engine()
        return {
            "mode": engine.provider,
            "simulated": engine.simulated,
            "model": engine.model,
            "geminiKeyConfigured": bool(engine.api_key),
        }

    @app.post("/api/mode", tags=["scam-shield"])
    async def set_mode(body: ModeSwitchRequest):
        target = body.mode.strip().lower()
        if target not in ("gemini", "mock"):
            raise ScamShieldEngineError("VALIDATION_FAILED", "mode must be 'gemini' or 'mock'", status_code=400)
        _RUNTIME_STATE["mode"] = target
        if body.model and body.model.strip():
            _RUNTIME_STATE["model"] = body.model.strip()
        engine = get_engine()
        return {
            "mode": engine.provider,
            "simulated": engine.simulated,
            "model": engine.model,
            "geminiKeyConfigured": bool(engine.api_key),
        }

    @app.get("/api/scenarios", tags=["scam-shield"])
    async def list_scenarios():
        return {
            "items": [s.model_dump() for s in SCENARIOS.values()],
            "total": len(SCENARIOS),
        }

    @app.post("/api/analyze-chunk", response_model=AnalyzeChunkResponse, tags=["scam-shield"])
    async def analyze_chunk(
        audio: Annotated[UploadFile | None, File(description="3-5s call audio chunk (WAV/WebM/OGG/MP3)")] = None,
        scenario_id: Annotated[str | None, Form(description="Optional pre-loaded scenario key")] = None,
        transcript: Annotated[str | None, Form(description="Optional live STT or simulated caller transcript")] = None,
        recipient: Annotated[str, Form(description="Emergency contact name")] = "Son (Rahul)",
        mode: Annotated[str | None, Form(description="Optional override mode: 'gemini' or 'mock'")] = None,
    ) -> AnalyzeChunkResponse:
        audio_bytes: bytes | None = None
        mime_type = "audio/wav"

        if audio is not None:
            audio_bytes = await audio.read()
            if audio.content_type:
                mime_type = audio.content_type

        engine = get_engine(override_mode=mode)
        return await engine.analyze_chunk(
            audio_bytes=audio_bytes if (audio_bytes and len(audio_bytes) > 0) else None,
            mime_type=mime_type,
            transcript=transcript,
            scenario_id=scenario_id,
            recipient=recipient,
        )

    @app.websocket("/ws/stream")
    async def websocket_audio_stream(ws: WebSocket):
        """Async WebSocket handler for continuous 3-5s audio chunk or JSON frame analysis."""
        await ws.accept()
        engine = get_engine()
        try:
            while True:
                message = await ws.receive()
                if message.get("type") == "websocket.disconnect":
                    break

                if "bytes" in message and message["bytes"]:
                    result = await engine.analyze_chunk(
                        audio_bytes=message["bytes"],
                        mime_type="audio/wav",
                    )
                    await ws.send_json(result.model_dump())
                elif "text" in message and message["text"]:
                    payload = json.loads(message["text"])
                    result = await engine.analyze_chunk(
                        transcript=payload.get("transcript"),
                        scenario_id=payload.get("scenario_id"),
                        recipient=payload.get("recipient", "Son (Rahul)"),
                    )
                    await ws.send_json(result.model_dump())
        except WebSocketDisconnect:
            pass
        except ScamShieldEngineError as exc:
            await ws.send_json({"error": {"code": exc.code, "message": exc.message}})
            await ws.close(code=1011)

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8080"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)

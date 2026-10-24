from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from .api.routes import root, v1, ws_router
from .config import Settings, get_settings
from .errors import AppError, RequestIdMiddleware, install_error_handlers
from .providers import status as pstatus
from .repositories import MemoryRepository
from .services.sessions import SessionService
from .tools.agent_proxy import fetch_remote_specs
from .tools.builtin import build_registry
from .tools.executor import ApprovalService, ToolExecutor
from .voice.browser import BrowserVoice
from .voice.plivo import PlivoAdapter


@dataclass
class Container:
    settings: Settings
    repo: Any
    registry: Any
    sessions: SessionService
    browser: BrowserVoice
    plivo: PlivoAdapter
    http_transport: Any = None


async def build_container(settings: Settings, repo=None, http_transport=None) -> Container:
    if repo is None:
        if settings.data_mode == "postgres":
            from .repositories.postgres import PostgresRepository

            repo = PostgresRepository(settings.dsn)
            await repo.open()
            applied = await repo.migrations_applied()
            if not applied:
                await repo.close()
                raise AppError("DATABASE_UNAVAILABLE", "Database has no migrations applied: run `uv run python -m app.db migrate`")
        else:
            repo = MemoryRepository()
    registry = build_registry()
    approvals = ApprovalService(repo, settings.approval_ttl_seconds)
    executor = ToolExecutor(registry, repo, settings, approvals, http_transport)
    sessions = SessionService(settings, repo, registry, executor, approvals)
    return Container(settings, repo, registry, sessions, BrowserVoice(settings, sessions), PlivoAdapter(settings, sessions, http_transport), http_transport)


def _log_startup(s: Settings) -> None:
    llm, stt, tts = pstatus.llm_state(s), pstatus.stt_state(s), pstatus.tts_state(s)
    logger.info("{} v{} dataMode={} persistTranscripts={}", s.service_name, s.version, s.data_mode, s.persist_transcripts)
    for st in (llm, stt, tts):
        extra = f" MISSING {st.missing}" if st.missing else ""
        sim = " (SIMULATED - mock provider explicitly selected)" if st.simulated else ""
        logger.info("provider {}: {} [{}]{}{}", st.kind, st.name, st.status, sim, extra)
    if s.data_mode == "mock":
        logger.warning("DATA_MODE=mock: in-memory storage, all data is lost on restart")
    if not s.internal_api_key:
        logger.warning("INTERNAL_API_KEY is not set: admin/outbound routes are OPEN (development only)")


def create_app(settings: Settings | None = None, repo=None, http_transport=None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        _log_startup(settings)
        try:
            c = await build_container(settings, repo, http_transport)
        except AppError as e:
            logger.error("STARTUP FAILED [{}]: {}", e.code, e.message)
            raise
        app.state.c = c
        orphans = await c.repo.end_orphaned_sessions("ended: server restarted")
        if orphans:
            logger.info("marked {} orphaned sessions from a previous run as ended", orphans)
        if settings.agent_api_url:
            specs = await fetch_remote_specs(settings, http_transport)
            c.registry.replace_source("agent-proxy", specs)
            logger.info("repo-agent proxy: {} remote tools registered", len(specs))

        async def sweeper():
            while True:
                await asyncio.sleep(settings.cleanup_interval_seconds)
                try:
                    await c.sessions.cleanup_expired()
                except Exception as e:
                    logger.warning("cleanup sweep failed: {}", type(e).__name__)

        task = asyncio.create_task(sweeper(), name="session-sweeper")
        try:
            yield
        finally:
            task.cancel()
            await c.sessions.shutdown()
            await c.browser.close()
            await c.repo.close()

    app = FastAPI(title="repo-voice-ai", version=settings.version, lifespan=lifespan,
                  description="Real-time voice AI platform: text, browser (SmallWebRTC) and phone (Plivo) transports sharing one VoiceAgent.",
                  docs_url="/docs", openapi_url="/openapi.json")
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_credentials=False,
                       allow_methods=["GET", "POST", "PATCH", "OPTIONS"], allow_headers=["Content-Type", "X-Api-Key", "X-Request-Id"],
                       expose_headers=["X-Request-Id"])
    app.add_middleware(RequestIdMiddleware)
    install_error_handlers(app)
    app.include_router(root)
    app.include_router(v1)
    app.include_router(ws_router)

    @app.get("/docs-json", include_in_schema=False)
    async def docs_json():
        return app.openapi()

    return app

"""Browser voice via Pipecat's SmallWebRTC transport + request handler (RTVI events go to @pipecat-ai/client-js)."""

from __future__ import annotations

import asyncio

from loguru import logger
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import IceServer, SmallWebRTCConnection
from pipecat.transports.smallwebrtc.request_handler import (
    IceCandidate,
    SmallWebRTCPatchRequest,
    SmallWebRTCRequest,
    SmallWebRTCRequestHandler,
)
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport

from ..contracts import models as C
from ..errors import AppError, validation_failed
from ..providers import status as pstatus
from .pipeline import build_voice_worker, spawn_runner


class BrowserVoice:
    def __init__(self, settings, svc):
        self.s, self.svc = settings, svc
        self.handler = SmallWebRTCRequestHandler(ice_servers=[IceServer(urls=u) for u in settings.ice_server_list] or None)

    async def offer(self, session_id: str, body: C.OfferRequest) -> C.OfferResponse:
        rec, rt = await self.svc.require_runtime(session_id, mode="browser")
        if not body.sdp.lstrip().startswith("v=0") or "m=audio" not in body.sdp:
            raise validation_failed("sdp is not a valid WebRTC offer with an audio section", "sdp")
        pstatus.require_for_mode(self.s, "browser")
        if rt.pc_id and body.pc_id != rt.pc_id:
            raise AppError("CONFLICT", "This session already has a peer connection; pass its pcId to renegotiate")

        async def on_connection(conn: SmallWebRTCConnection) -> None:
            transport = SmallWebRTCTransport(conn, TransportParams(audio_in_enabled=True, audio_out_enabled=True))
            build = build_voice_worker(self.s, self.svc, rec, rt, transport, telephony=False)

            @transport.event_handler("on_client_connected")
            async def _connected(_t, _client):
                logger.info("browser client connected session={}", rec.id)
                await self.svc.touch(rec.id, "active")
                await rt.diagnostics["greet"]()

            @transport.event_handler("on_client_disconnected")
            async def _disconnected(_t, _client):
                logger.info("browser client disconnected session={}", rec.id)
                if not rt.closing:
                    asyncio.create_task(self.svc.end(rec.id, None))  # decouple from the pipeline task

            spawn_runner(self.svc, rec.id, rt, build.worker)

        req = SmallWebRTCRequest(sdp=body.sdp, type=body.type, pc_id=body.pc_id, restart_pc=body.restart_pc, request_data=body.request_data)
        try:
            answer = await self.handler.handle_web_request(req, on_connection)
        except AppError:
            raise
        except Exception as e:
            await self.svc.fail(rec.id, f"WebRTC negotiation failed: {type(e).__name__}")
            raise AppError("PROVIDER_ERROR", f"WebRTC negotiation failed: {type(e).__name__}: {e}"[:300]) from e
        if not answer:
            raise AppError("PROVIDER_ERROR", "WebRTC produced no answer")
        rt.pc_id = answer["pc_id"]
        await self.svc.touch(rec.id, "active")
        return C.OfferResponse(sdp=answer["sdp"], type=answer["type"], pc_id=answer["pc_id"])

    async def ice(self, session_id: str, body: C.IcePatch) -> None:
        _rec, rt = await self.svc.require_runtime(session_id, mode="browser")
        if rt.pc_id != body.pc_id:
            raise AppError("NOT_FOUND", f"No peer connection {body.pc_id} for this session")
        cands = [IceCandidate(candidate=c.candidate, sdp_mid=c.sdp_mid or "", sdp_mline_index=c.sdp_mline_index or 0) for c in body.candidates]
        await self.handler.handle_patch_request(SmallWebRTCPatchRequest(pc_id=body.pc_id, candidates=cands))

    async def close(self) -> None:
        await self.handler.close()

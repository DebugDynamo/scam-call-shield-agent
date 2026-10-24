"""Plivo phone adapter (pattern reused from the reference: answer XML `<Stream bidirectional>` + wss bridge,
`parse_telephony_websocket` + `PlivoFrameSerializer`, hangup webhook).

Never places a call on its own: `place_call` is only reachable from the API-key-protected outbound route.
Verified locally only with simulated Plivo messages / mocked HTTP; NOT verified against live Plivo.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from xml.sax.saxutils import escape

import httpx
from fastapi import WebSocket
from loguru import logger
from pipecat.runner.utils import parse_telephony_websocket
from pipecat.serializers.plivo import PlivoFrameSerializer
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams, FastAPIWebsocketTransport

from ..errors import AppError, provider_not_configured, request_id_var
from .pipeline import build_voice_worker, spawn_runner

PLIVO_API = "https://api.plivo.com/v1"


class PlivoAdapter:
    def __init__(self, settings, svc, http_transport=None):
        self.s, self.svc = settings, svc
        self._ws_secret = secrets.token_bytes(32)  # per-process; websocket URLs are only valid for this process
        self._call_to_session: dict[str, str] = {}
        self._http_transport = http_transport

    # ---- config ----
    def require_configured(self, *, outbound: bool = False) -> None:
        for var, val in (("PLIVO_AUTH_ID", self.s.plivo_auth_id), ("PLIVO_AUTH_TOKEN", self.s.plivo_auth_token),
                         ("PUBLIC_BASE_URL", self.s.public_base_url)):
            if not val:
                raise provider_not_configured("plivo", var)
        if outbound and not self.s.plivo_phone_number:
            raise provider_not_configured("plivo", "PLIVO_PHONE_NUMBER")

    def status(self) -> dict:
        missing = [v for v, x in (("PLIVO_AUTH_ID", self.s.plivo_auth_id), ("PLIVO_AUTH_TOKEN", self.s.plivo_auth_token),
                                  ("PLIVO_PHONE_NUMBER", self.s.plivo_phone_number), ("PUBLIC_BASE_URL", self.s.public_base_url)) if not x]
        return {"status": "not_configured" if missing else "configured", "missing": missing,
                "signatureVerification": self.s.plivo_verify_signature, "inboundAllowed": self.s.plivo_allow_inbound,
                "verifiedAgainstLivePlivo": False}

    # ---- signature (Plivo V2: base64(HMAC-SHA256(auth_token, url + nonce))) ----
    @staticmethod
    def compute_v2(auth_token: str, url: str, nonce: str) -> str:
        return base64.b64encode(hmac.new(auth_token.encode(), (url + nonce).encode(), hashlib.sha256).digest()).decode()

    def verify_signature(self, url: str, nonce: str | None, signature: str | None) -> None:
        if not self.s.plivo_verify_signature:
            logger.warning("Plivo signature verification is DISABLED (PLIVO_VERIFY_SIGNATURE=false)")
            return
        if not (nonce and signature):
            raise AppError("UNAUTHORIZED", "Missing X-Plivo-Signature-V2 / X-Plivo-Signature-V2-Nonce headers")
        expected = self.compute_v2(self.s.plivo_auth_token or "", url, nonce)
        if not hmac.compare_digest(expected, signature):
            raise AppError("UNAUTHORIZED", "Invalid Plivo signature")

    def public_url(self, path_and_query: str) -> str:
        return f"{self.s.public_base_url}{path_and_query}"

    # ---- websocket token / urls ----
    def stream_token(self, session_id: str) -> str:
        return hmac.new(self._ws_secret, session_id.encode(), hashlib.sha256).hexdigest()[:32]

    def check_stream_token(self, session_id: str, token: str) -> bool:
        return hmac.compare_digest(self.stream_token(session_id), token)

    def stream_url(self, session_id: str) -> str:
        base = self.s.public_base_url or ""
        ws_base = "wss://" + base[len("https://"):] if base.startswith("https://") else "ws://" + base[len("http://"):]
        return f"{ws_base}/api/v1/telephony/plivo/stream/{session_id}/{self.stream_token(session_id)}"

    def answer_xml(self, session_id: str) -> str:
        # No <Record>: raw call audio is never recorded or stored by this service.
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n<Response>\n'
            '  <Stream bidirectional="true" keepCallAlive="true" contentType="audio/x-mulaw;rate=8000">'
            f"{escape(self.stream_url(session_id))}</Stream>\n</Response>"
        )

    @staticmethod
    def reject_xml() -> str:
        return '<?xml version="1.0" encoding="UTF-8"?>\n<Response><Hangup reason="rejected"/></Response>'

    def remember_call(self, call_uuid: str | None, session_id: str) -> None:
        if call_uuid:
            self._call_to_session[call_uuid] = session_id

    def session_for_call(self, call_uuid: str | None) -> str | None:
        return self._call_to_session.get(call_uuid or "")

    # ---- outbound ----
    async def place_call(self, to: str, session_id: str) -> str | None:
        self.require_configured(outbound=True)
        body = {
            "from": self.s.plivo_phone_number, "to": to,
            "answer_url": self.public_url(f"/api/v1/telephony/plivo/answer?session_id={session_id}"), "answer_method": "POST",
            "hangup_url": self.public_url(f"/api/v1/telephony/plivo/hangup?session_id={session_id}"), "hangup_method": "POST",
        }
        try:
            async with httpx.AsyncClient(timeout=15, transport=self._http_transport) as c:
                r = await c.post(f"{PLIVO_API}/Account/{self.s.plivo_auth_id}/Call/", json=body,
                                 auth=(self.s.plivo_auth_id or "", self.s.plivo_auth_token or ""),
                                 headers={"X-Request-Id": request_id_var.get()})
        except httpx.TimeoutException as e:
            raise AppError("TIMEOUT", "Plivo API timed out") from e
        except httpx.HTTPError as e:
            raise AppError("PROVIDER_ERROR", f"Plivo API unreachable: {type(e).__name__}") from e
        if r.status_code == 429:
            raise AppError("PROVIDER_RATE_LIMITED", "Plivo rate limit")
        if r.status_code >= 400:
            raise AppError("PROVIDER_ERROR", f"Plivo rejected the call (HTTP {r.status_code})")
        try:
            return r.json().get("request_uuid")
        except ValueError:
            return None

    # ---- websocket pipeline ----
    async def run_stream(self, ws: WebSocket, session_id: str, token: str) -> None:
        if not self.check_stream_token(session_id, token):
            await ws.close(code=4003)
            return
        try:
            rec, rt = await self.svc.require_runtime(session_id, mode="phone")
        except AppError:
            await ws.close(code=4004)
            return
        await ws.accept()
        try:
            _type, call_data = await parse_telephony_websocket(ws)
            serializer = PlivoFrameSerializer(
                stream_id=call_data["stream_id"], call_id=call_data["call_id"],
                auth_id=self.s.plivo_auth_id, auth_token=self.s.plivo_auth_token,
            )
            self.remember_call(call_data["call_id"], rec.id)
            transport = FastAPIWebsocketTransport(ws, FastAPIWebsocketParams(
                audio_in_enabled=True, audio_out_enabled=True, add_wav_header=False, serializer=serializer))
            build = build_voice_worker(self.s, self.svc, rec, rt, transport, telephony=True)

            @transport.event_handler("on_client_connected")
            async def _connected(_t, _c):
                await self.svc.touch(rec.id, "active")
                await rt.diagnostics["greet"]()

            @transport.event_handler("on_client_disconnected")
            async def _disconnected(_t, _c):
                import asyncio

                if not rt.closing:
                    asyncio.create_task(self.svc.end(rec.id, None))

            task = spawn_runner(self.svc, rec.id, rt, build.worker)
            await task
        except Exception as e:
            logger.warning("plivo stream for {} failed: {}", session_id, type(e).__name__)
            await self.svc.fail(session_id, f"phone stream error: {type(e).__name__}")

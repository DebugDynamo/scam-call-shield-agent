import base64
import json
import re

import httpx
import pytest
from conftest import make_settings
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import create_app
from app.voice.plivo import PlivoAdapter

PLIVO = dict(plivo_auth_id="MAXXXXXXXXXXXXXXXXXX", plivo_auth_token="tok-secret", plivo_phone_number="+15550001111",
             public_base_url="https://voice.example.test")


def signed(path_q: str, nonce="n1"):
    sig = PlivoAdapter.compute_v2("tok-secret", PLIVO["public_base_url"] + path_q, nonce)
    return {"X-Plivo-Signature-V2": sig, "X-Plivo-Signature-V2-Nonce": nonce}


def test_unconfigured_telephony_is_503():
    with TestClient(create_app(make_settings())) as c:
        r = c.post("/api/v1/telephony/plivo/answer")
        assert r.status_code == 503 and "PLIVO_AUTH_ID" in r.json()["error"]["message"]
        r = c.post("/api/v1/telephony/outbound", json={"to": "+15551234567", "confirm": True})
        assert r.status_code == 503 and r.json()["error"]["code"] == "PROVIDER_NOT_CONFIGURED"


def test_outbound_requires_explicit_confirm_and_valid_number():
    with TestClient(create_app(make_settings(**PLIVO))) as c:
        assert c.post("/api/v1/telephony/outbound", json={"to": "+15551234567"}).status_code == 400
        assert c.post("/api/v1/telephony/outbound", json={"to": "+15551234567", "confirm": False}).status_code == 400
        assert c.post("/api/v1/telephony/outbound", json={"to": "5551234567", "confirm": True}).status_code == 400
        assert c.get("/api/v1/sessions").json()["total"] == 0  # nothing created, nothing dialled


def test_outbound_calls_plivo_api_and_creates_phone_session():
    seen = {}

    def handler(req: httpx.Request):
        seen["url"], seen["body"], seen["auth"] = str(req.url), json.loads(req.content), req.headers["authorization"]
        return httpx.Response(201, json={"request_uuid": "req-123", "message": "call fired"})

    with TestClient(create_app(make_settings(**PLIVO), http_transport=httpx.MockTransport(handler))) as c:
        r = c.post("/api/v1/telephony/outbound", json={"to": "+15551234567", "confirm": True})
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["callUuid"] == "req-123" and j["status"] == "queued"
        assert seen["url"] == "https://api.plivo.com/v1/Account/MAXXXXXXXXXXXXXXXXXX/Call/"
        assert seen["body"]["to"] == "+15551234567" and seen["body"]["from"] == "+15550001111"
        assert seen["body"]["answer_url"] == f"https://voice.example.test/api/v1/telephony/plivo/answer?session_id={j['sessionId']}"
        s = c.get(f"/api/v1/sessions/{j['sessionId']}").json()
        assert s["mode"] == "phone" and s["transport"] == "plivo"


def test_outbound_provider_failure_marks_session_failed():
    with TestClient(create_app(make_settings(**PLIVO), http_transport=httpx.MockTransport(lambda r: httpx.Response(401)))) as c:
        r = c.post("/api/v1/telephony/outbound", json={"to": "+15551234567", "confirm": True})
        assert r.status_code == 502 and r.json()["error"]["code"] == "PROVIDER_ERROR"
        item = c.get("/api/v1/sessions").json()["items"][0]
        assert item["status"] == "failed"


def test_answer_webhook_signature_and_xml():
    with TestClient(create_app(make_settings(**PLIVO))) as c:
        sid = c.post("/api/v1/sessions", json={"mode": "phone"}).json()["id"]
        path = f"/api/v1/telephony/plivo/answer?session_id={sid}"
        assert c.post(path).status_code == 401  # unsigned
        bad = c.post(path, headers={"X-Plivo-Signature-V2": "AAAA", "X-Plivo-Signature-V2-Nonce": "n"})
        assert bad.status_code == 401
        ok = c.post(path, headers=signed(path), data={"CallUUID": "uuid-1"})
        assert ok.status_code == 200 and ok.headers["content-type"].startswith("application/xml")
        assert '<Stream bidirectional="true"' in ok.text and "audio/x-mulaw;rate=8000" in ok.text
        m = re.search(r">(wss://[^<]+)<", ok.text)
        assert m and m.group(1).startswith(f"wss://voice.example.test/api/v1/telephony/plivo/stream/{sid}/")
        assert "<Record" not in ok.text  # raw audio is never recorded


def test_signature_check_can_be_disabled_explicitly():
    with TestClient(create_app(make_settings(**PLIVO, plivo_verify_signature=False))) as c:
        sid = c.post("/api/v1/sessions", json={"mode": "phone"}).json()["id"]
        assert c.post(f"/api/v1/telephony/plivo/answer?session_id={sid}").status_code == 200


def test_inbound_rejected_by_default_and_allowed_when_enabled():
    with TestClient(create_app(make_settings(**PLIVO))) as c:
        path = "/api/v1/telephony/plivo/answer"
        r = c.post(path, headers=signed(path), data={"CallUUID": "u"})
        assert "<Hangup" in r.text and c.get("/api/v1/sessions").json()["total"] == 0
    with TestClient(create_app(make_settings(**PLIVO, plivo_allow_inbound=True))) as c:
        r = c.post(path, headers=signed(path), data={"CallUUID": "u", "From": "+1555"})
        assert "<Stream" in r.text and c.get("/api/v1/sessions").json()["total"] == 1


def test_hangup_ends_session():
    with TestClient(create_app(make_settings(**PLIVO))) as c:
        sid = c.post("/api/v1/sessions", json={"mode": "phone"}).json()["id"]
        path = f"/api/v1/telephony/plivo/hangup?session_id={sid}"
        assert c.post(path).status_code == 401
        assert c.post(path, headers=signed(path), data={"CallUUID": "x", "CallStatus": "completed"}).json() == {"received": True}
        assert c.get(f"/api/v1/sessions/{sid}").json()["status"] == "ended"


def test_stream_websocket_rejects_bad_token():
    with TestClient(create_app(make_settings(**PLIVO))) as c:
        sid = c.post("/api/v1/sessions", json={"mode": "phone"}).json()["id"]
        with pytest.raises(WebSocketDisconnect) as e, c.websocket_connect(f"/api/v1/telephony/plivo/stream/{sid}/badtoken"):
            pass
        assert e.value.code == 4003


def test_stream_websocket_runs_pipeline_with_simulated_plivo_messages():
    """Pipeline over the Plivo serializer with mock providers; the 'phone' is a scripted websocket client."""
    with TestClient(create_app(make_settings(**PLIVO))) as c:
        sid = c.post("/api/v1/sessions", json={"mode": "phone"}).json()["id"]
        path = f"/api/v1/telephony/plivo/answer?session_id={sid}"
        xml = c.post(path, headers=signed(path), data={"CallUUID": "call-1"}).text
        url = re.search(r">wss://[^/]+(/[^<]+)<", xml).group(1)
        with c.websocket_connect(url) as ws:
            ws.send_text(json.dumps({"event": "start", "start": {"streamId": "stream-1", "callId": "call-1"}}))
            silence = base64.b64encode(b"\xff" * 160).decode()  # 20 ms of mu-law silence
            ws.send_text(json.dumps({"event": "media", "streamId": "stream-1",
                                     "media": {"track": "inbound", "timestamp": "0", "chunk": 1, "payload": silence}}))
            got = None
            for _ in range(40):
                msg = json.loads(ws.receive_text())
                if msg.get("event") == "playAudio":
                    got = msg
                    break
            assert got is not None, "bot greeting audio (mock TTS) was never sent back over the Plivo stream"
            assert got["media"]["contentType"] == "audio/x-mulaw"
            assert c.get(f"/api/v1/sessions/{sid}").json()["status"] == "active"
        # phone hangs up -> session cleaned up
        import time

        for _ in range(30):
            if c.get(f"/api/v1/sessions/{sid}").json()["status"] == "ended":
                break
            time.sleep(0.2)
        assert c.get(f"/api/v1/sessions/{sid}").json()["status"] == "ended"

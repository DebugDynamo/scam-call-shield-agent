"""Real aiortc client <-> in-process server handshake using mock providers (no cloud credentials)."""
import asyncio
import json

import httpx
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import AudioStreamTrack
from conftest import make_settings

from app.main import create_app


async def test_browser_voice_lifecycle_with_real_webrtc_client():
    app = create_app(make_settings())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as http:
            sid = (await http.post("/api/v1/sessions", json={"mode": "browser"})).json()["id"]
            pc = RTCPeerConnection()
            pc.addTrack(AudioStreamTrack())
            dc = pc.createDataChannel("chat")
            types: set[str] = set()
            frames = {"n": 0}
            audio = asyncio.Event()

            @dc.on("open")
            def _open():
                dc.send(json.dumps({"label": "rtvi-ai", "type": "client-ready", "id": "1", "data": {"version": "1.2.0", "about": {}}}))

            @dc.on("message")
            def _msg(m):
                try:
                    types.add(json.loads(m).get("type"))
                except Exception:
                    pass

            @pc.on("track")
            def _track(track):
                async def drain():
                    while True:
                        try:
                            await track.recv()
                        except Exception:
                            return
                        frames["n"] += 1
                        if frames["n"] > 10:
                            audio.set()

                asyncio.ensure_future(drain())

            await pc.setLocalDescription(await pc.createOffer())
            r = await http.post(f"/api/v1/sessions/{sid}/offer", json={"sdp": pc.localDescription.sdp, "type": "offer"})
            assert r.status_code == 200, r.text
            ans = r.json()
            assert ans["pcId"] and ans["type"] == "answer"
            await pc.setRemoteDescription(RTCSessionDescription(sdp=ans["sdp"], type=ans["type"]))
            await asyncio.wait_for(audio.wait(), 20)
            for _ in range(50):
                if "bot-ready" in types:
                    break
                await asyncio.sleep(0.1)
            assert "bot-ready" in types
            assert (await http.get(f"/api/v1/sessions/{sid}")).json()["status"] == "active"
            # a second offer for a different peer on the same session is refused
            dup = await http.post(f"/api/v1/sessions/{sid}/offer", json={"sdp": pc.localDescription.sdp, "type": "offer"})
            assert dup.status_code == 409
            await pc.close()
            for _ in range(50):
                if (await http.get(f"/api/v1/sessions/{sid}")).json()["status"] == "ended":
                    break
                await asyncio.sleep(0.1)
            assert (await http.get(f"/api/v1/sessions/{sid}")).json()["status"] == "ended"

"""Local WebRTC handshake check against a running server (mock providers, no cloud credentials needed).

    LLM_PROVIDER=mock STT_PROVIDER=mock TTS_PROVIDER=mock uv run python -m app   # terminal 1
    uv run python scripts/webrtc_smoke.py http://localhost:8003                  # terminal 2

Creates a browser session, performs the SDP offer/answer with an aiortc client (silent mic track), waits for the
connection to be established and for bot audio (the mock TTS greeting tone) to arrive, then ends the session.
Exit code 0 only if connected AND audio frames were received AND the session ended cleanly.
"""

import asyncio
import json
import sys
import time

import httpx
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import AudioStreamTrack


async def main(base: str) -> int:
    api = f"{base}/api/v1"
    async with httpx.AsyncClient(timeout=15) as http:
        r = await http.post(f"{api}/sessions", json={"mode": "browser"})
        r.raise_for_status()
        sid = r.json()["id"]
        print("session", sid, "simulated=", r.json()["simulated"])

        pc = RTCPeerConnection()
        pc.addTrack(AudioStreamTrack())  # silence
        dc = pc.createDataChannel("chat")  # pipecat's RTVI messages ride this channel

        @dc.on("open")
        def _open():
            dc.send(json.dumps({"label": "rtvi-ai", "type": "client-ready", "id": "smoke-1",
                                "data": {"version": "1.2.0", "about": {"library": "webrtc_smoke.py"}}}))

        frames = {"n": 0}
        rtvi: list[dict] = []
        got_audio = asyncio.Event()

        @pc.on("track")
        def on_track(track):
            async def drain():
                while True:
                    try:
                        await track.recv()
                    except Exception:
                        return
                    frames["n"] += 1
                    if frames["n"] > 20:
                        got_audio.set()

            if track.kind == "audio":
                asyncio.ensure_future(drain())

        @dc.on("message")
        def on_msg(m):
            try:
                rtvi.append(json.loads(m))
            except Exception:
                pass

        await pc.setLocalDescription(await pc.createOffer())
        r = await http.post(f"{api}/sessions/{sid}/offer", json={"sdp": pc.localDescription.sdp, "type": "offer"})
        print("offer ->", r.status_code)
        r.raise_for_status()
        ans = r.json()
        await pc.setRemoteDescription(RTCSessionDescription(sdp=ans["sdp"], type=ans["type"]))
        t0 = time.time()
        while pc.connectionState != "connected" and time.time() - t0 < 15:
            await asyncio.sleep(0.2)
        print("pc.connectionState =", pc.connectionState, "pcId =", ans["pcId"])
        ok_conn = pc.connectionState == "connected"
        try:
            await asyncio.wait_for(got_audio.wait(), 15)
        except TimeoutError:
            pass
        print("bot audio frames received:", frames["n"])
        types = sorted({m.get("type") for m in rtvi if isinstance(m, dict)})
        print("RTVI message types seen:", types)
        s = (await http.get(f"{api}/sessions/{sid}")).json()
        print("session status while connected:", s["status"])
        await pc.close()
        await asyncio.sleep(1.5)
        s = (await http.get(f"{api}/sessions/{sid}")).json()
        print("session status after client disconnect:", s["status"])
        if s["status"] not in ("ended",):
            await http.post(f"{api}/sessions/{sid}/end")
            s = (await http.get(f"{api}/sessions/{sid}")).json()
            print("after explicit end:", s["status"])
        ok = ok_conn and frames["n"] > 0 and s["status"] == "ended" and "bot-ready" in types
        print("RESULT:", "PASS" if ok else "FAIL")
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8003")))

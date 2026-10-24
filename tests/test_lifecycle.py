import time
from datetime import timedelta

from conftest import client_for, make_settings, new_session, say

from app.contracts.models import SessionCreate
from app.domain import now
from app.main import build_container


def test_persist_transcripts_false_stores_no_text_and_redacts_traces():
    with client_for(persist_transcripts=False) as c:
        s = new_session(c)
        r = say(c, s["id"], "create task secret plan")
        assert "secret plan" in r["reply"]["text"]  # the live reply still works
        d = c.get(f"/api/v1/sessions/{s['id']}").json()
        assert d["messages"] == [] and d["transcriptsPersisted"] is False
        assert all("secret plan" not in str(t) for t in d["toolTrace"])


async def test_ttl_cleanup_ends_idle_sessions_and_keeps_active_ones():
    c = await build_container(make_settings(session_ttl_seconds=30))
    old = (await c.sessions.create(SessionCreate(mode="text"))).id
    fresh = (await c.sessions.create(SessionCreate(mode="text"))).id
    await c.repo.update_session(old, last_activity_at=now() - timedelta(seconds=120))
    assert await c.sessions.cleanup_expired() == 1
    assert (await c.sessions.get(old)).status == "ended" and (await c.sessions.get(fresh)).status == "created"
    assert old not in c.sessions.runtimes


async def test_orphaned_sessions_closed_on_startup():
    c = await build_container(make_settings())
    sid = (await c.sessions.create(SessionCreate(mode="text"))).id
    assert await c.repo.end_orphaned_sessions("ended: server restarted") == 1
    s = await c.sessions.get(sid)
    assert s.status == "ended" and s.error == "ended: server restarted"


def test_background_sweeper_runs():
    with client_for(session_ttl_seconds=5, cleanup_interval_seconds=1) as c:
        s = new_session(c)
        c.app.state.c.repo._sessions[s["id"]].last_activity_at = now() - timedelta(seconds=60)
        for _ in range(30):
            if c.get(f"/api/v1/sessions/{s['id']}").json()["status"] == "ended":
                break
            time.sleep(0.2)
        assert c.get(f"/api/v1/sessions/{s['id']}").json()["status"] == "ended"


def test_browser_failure_paths():
    with client_for() as c:
        s = new_session(c, "browser")
        r = c.post(f"/api/v1/sessions/{s['id']}/offer", json={"sdp": "garbage", "type": "offer"})
        assert r.status_code == 400 and r.json()["error"]["details"][0]["path"] == "sdp"
        # a structurally plausible but unusable offer fails cleanly and marks the session failed
        bad = "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"
        r = c.post(f"/api/v1/sessions/{s['id']}/offer", json={"sdp": bad, "type": "offer"})
        assert r.status_code == 502 and r.json()["error"]["code"] == "PROVIDER_ERROR"
        assert c.get(f"/api/v1/sessions/{s['id']}").json()["status"] == "failed"
        # offer on a text session / ended session / unknown session
        t = new_session(c)
        assert c.post(f"/api/v1/sessions/{t['id']}/offer", json={"sdp": "v=0 m=audio", "type": "offer"}).status_code == 409
        assert c.post("/api/v1/sessions/00000000-0000-0000-0000-000000000000/offer", json={"sdp": "v=0 m=audio", "type": "offer"}).status_code == 404
        # ICE for an unknown peer connection
        b = new_session(c, "browser")
        r = c.patch(f"/api/v1/sessions/{b['id']}/ice", json={"pcId": "nope", "candidates": []})
        assert r.status_code == 404
        # offer with bad body
        assert c.post(f"/api/v1/sessions/{b['id']}/offer", json={"type": "offer"}).status_code == 400


def test_shutdown_tears_down_runtimes():
    c = client_for()
    with c:
        s = new_session(c)
        container = c.app.state.c
        assert s["id"] in container.sessions.runtimes
    assert container.sessions.runtimes == {}

import io

from conftest import client_for, make_settings

from app.main import build_container


def _wav_bytes() -> bytes:
    num_samples = 160
    data_size = num_samples * 2
    return (
        b"RIFF"
        + (36 + data_size).to_bytes(4, "little")
        + b"WAVEfmt "
        + (16).to_bytes(4, "little")
        + (1).to_bytes(2, "little")
        + (1).to_bytes(2, "little")
        + (16000).to_bytes(4, "little")
        + (32000).to_bytes(4, "little")
        + (2).to_bytes(2, "little")
        + (16).to_bytes(2, "little")
        + b"data"
        + data_size.to_bytes(4, "little")
        + (b"\x00\x00" * num_samples)
    )


def test_scam_shield_routes_in_voice_ai(client):
    sc = client.get("/api/scenarios").json()
    assert sc["total"] == 4

    html = client.get("/scam-shield")
    assert html.status_code == 200 and "Scam Call Shield" in html.text

    wav = _wav_bytes()
    for sid, lang in [
        ("gujarati_digital_arrest", "gu"),
        ("hindi_digital_arrest", "hi"),
        ("english_bank_kyc", "en"),
    ]:
        r = client.post(
            "/api/analyze-chunk",
            files={"audio": ("chunk.wav", io.BytesIO(wav), "audio/wav")},
            data={"scenario_id": sid},
        )
        assert r.status_code == 200
        j = r.json()
        assert j["is_scam"] is True
        assert j["confidence_score"] >= 0.70
        assert j["detected_language"] == lang
        assert j["action"] == "HANG_UP_IMMEDIATELY"
        assert j["emergency_dispatch"]["status"] == "MOCK_SMS_SENT"
        assert j["emergency_dispatch"]["recipient"] == "Son (Rahul)"

    safe = client.post(
        "/api/analyze-chunk",
        files={"audio": ("chunk.wav", io.BytesIO(wav), "audio/wav")},
        data={"scenario_id": "genuine_delivery_control"},
    ).json()
    assert safe["is_scam"] is False
    assert safe["red_flags"] == []
    assert safe["action"] == "SAFE_TO_CONTINUE"
    assert safe["emergency_dispatch"] is None


async def test_analyze_call_scam_voice_tool():
    cont = await build_container(make_settings())
    from app.contracts.models import SessionCreate

    sid = (await cont.sessions.create(SessionCreate(mode="text"))).id
    out = await cont.sessions.executor.execute(
        sid,
        "analyze_call_scam",
        {"scenario_id": "gujarati_digital_arrest"},
    )
    assert out.status == "ok"
    assert out.result["is_scam"] is True
    assert out.result["action"] == "HANG_UP_IMMEDIATELY"
    assert out.result["emergency_dispatch"]["status"] == "MOCK_SMS_SENT"


def test_scam_shield_unconfigured_gemini_returns_503():
    with client_for(llm_provider="gemini", gemini_api_key=None) as c:
        r = c.post("/api/analyze-chunk", data={"scenario_id": "english_bank_kyc"})
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "PROVIDER_NOT_CONFIGURED"

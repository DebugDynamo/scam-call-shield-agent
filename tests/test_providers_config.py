import pytest
from conftest import client_for, make_settings, new_session
from pydantic import ValidationError

from app.agent.llm import ChatItem, GeminiLLM
from app.config import Settings
from app.errors import AppError


def test_missing_gemini_key_is_503_with_env_var_name():
    with client_for(llm_provider="gemini") as c:
        r = c.post("/api/v1/sessions", json={"mode": "text"})
        assert r.status_code == 503
        err = r.json()["error"]
        assert err["code"] == "PROVIDER_NOT_CONFIGURED" and "GEMINI_API_KEY" in err["message"]
        assert err["details"][0]["path"] == "env.GEMINI_API_KEY"
        assert err["requestId"] and r.headers["x-request-id"] == err["requestId"]
        rd = c.get("/ready")
        assert rd.status_code == 503 and rd.json()["checks"]["provider"] == {"status": "not_configured", "name": "gemini", "model": "gemini-2.5-flash", "missing": ["GEMINI_API_KEY"]}
        # liveness stays up
        assert c.get("/health").status_code == 200


@pytest.mark.parametrize("stt,tts,missing", [
    ("sarvam", "mock", "SARVAM_API_KEY"),
    ("google", "mock", "GOOGLE_APPLICATION_CREDENTIALS"),
    ("mock", "elevenlabs", "ELEVENLABS_API_KEY"),
    ("mock", "sarvam", "SARVAM_API_KEY"),
    ("mock", "google", "GEMINI_API_KEY"),
])
def test_voice_modes_require_stt_tts_credentials(stt, tts, missing):
    with client_for(stt_provider=stt, tts_provider=tts) as c:
        assert c.post("/api/v1/sessions", json={"mode": "text"}).status_code == 201  # text needs only the LLM
        r = c.post("/api/v1/sessions", json={"mode": "browser"})
        assert r.status_code == 503 and missing in r.json()["error"]["message"]


def test_mock_is_reported_simulated_and_providers_endpoint(client):
    p = client.get("/api/v1/providers").json()
    assert p["llm"]["status"] == "mock" and p["llm"]["simulated"] is True
    assert p["room"]["status"] == "not_implemented"
    assert p["telephony"]["verifiedAgainstLivePlivo"] is False
    assert "key" not in str(p).lower().replace("persist", "")  # no secret material


def test_google_api_key_alias_and_never_exposed():
    s = Settings(GOOGLE_API_KEY="alias-key-123", _env_file=None)
    assert s.gemini_api_key == "alias-key-123"
    with client_for(llm_provider="gemini", gemini_api_key="super-secret-xyz", stt_provider="mock", tts_provider="mock") as c:
        body = c.get("/ready").text + c.get("/api/v1/providers").text + c.get("/health").text
        assert "super-secret-xyz" not in body
        assert c.get("/ready").json()["checks"]["provider"]["status"] == "configured"


def test_invalid_provider_names_fail_config_validation():
    with pytest.raises(ValidationError) as e:
        Settings(llm_provider="openai", _env_file=None)
    assert "LLM_PROVIDER" in str(e.value)
    with pytest.raises(ValidationError):
        Settings(tts_provider="deepgram", _env_file=None)
    with pytest.raises(ValidationError):
        Settings(public_base_url="ftp://x", _env_file=None)


def test_database_url_precedence_and_ssl():
    s = Settings(db_host="h", db_port=1234, db_username="u", db_password="p w", db_database="d", _env_file=None)
    assert s.dsn == "postgresql://u:p%20w@h:1234/d?sslmode=disable"
    assert Settings(db_ssl=True, _env_file=None).dsn.endswith("sslmode=require")
    s = Settings(database_url="postgresql://x:y@z:1/db", db_host="ignored", _env_file=None)
    assert s.dsn == "postgresql://x:y@z:1/db"


def test_cors_wildcard_rejected():
    with pytest.raises(ValueError):
        _ = make_settings(cors_origins="*").cors_origin_list


async def test_gemini_error_mapping(monkeypatch):
    from google.genai import errors

    llm = GeminiLLM("fake", "m", 1.0)

    class Models:
        def __init__(self, exc):
            self.exc = exc

        async def generate_content(self, **kw):
            raise self.exc

    class Aio:
        def __init__(self, exc):
            self.models = Models(exc)

    class Client:
        def __init__(self, exc):
            self.aio = Aio(exc)

    for exc, code in [(errors.ClientError(429, {"error": {"message": "quota"}}), "PROVIDER_RATE_LIMITED"),
                      (errors.ClientError(403, {"error": {"message": "denied"}}), "PROVIDER_ERROR"),
                      (errors.ServerError(500, {"error": {"message": "boom"}}), "PROVIDER_ERROR")]:
        llm._client = Client(exc)
        with pytest.raises(AppError) as e:
            await llm.generate("sys", [ChatItem("user", "hi")], [])
        assert e.value.code == code

    import asyncio

    class Slow(Models):
        async def generate_content(self, **kw):
            await asyncio.sleep(5)

    llm._client = Client(None)
    llm._client.aio.models = Slow(None)
    llm._timeout = 0.05
    with pytest.raises(AppError) as e:
        await llm.generate("sys", [ChatItem("user", "hi")], [])
    assert e.value.code == "TIMEOUT"


def test_provider_failure_surfaces_via_http(monkeypatch):
    """A failing provider is an explicit error response; it never degrades to a fake reply."""
    from app.agent import llm as llm_mod

    async def boom(self, *a, **k):
        raise AppError("PROVIDER_ERROR", "upstream exploded")

    monkeypatch.setattr(llm_mod.MockLLM, "generate", boom)
    with client_for() as c:
        s = new_session(c)
        r = c.post(f"/api/v1/sessions/{s['id']}/messages", json={"text": "hi"})
        assert r.status_code == 502 and r.json()["error"]["code"] == "PROVIDER_ERROR"

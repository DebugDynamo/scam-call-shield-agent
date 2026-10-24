from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

# Make sure the developer's shell/.env never leaks real credentials or modes into tests.
for _k in list(os.environ):
    if _k.split("_")[0] in {"GEMINI", "GOOGLE", "SARVAM", "ELEVENLABS", "PLIVO", "LLM", "STT", "TTS", "DATA", "DATABASE", "DB",
                            "PUBLIC", "INTERNAL", "AGENT", "SEARCH", "PERSIST", "SESSION", "CORS", "TOOL", "VOICE"}:
        os.environ.pop(_k, None)

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402


def make_settings(**kw) -> Settings:
    base = dict(llm_provider="mock", stt_provider="mock", tts_provider="mock", data_mode="mock", _env_file=None)
    base.update(kw)
    return Settings(**base)


@pytest.fixture
def settings_factory():
    return make_settings


@pytest.fixture
def client():
    with TestClient(create_app(make_settings())) as c:
        yield c


def client_for(**kw):
    return TestClient(create_app(make_settings(**kw)))


def new_session(c: TestClient, mode="text", **extra) -> dict:
    r = c.post("/api/v1/sessions", json={"mode": mode, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def say(c: TestClient, sid: str, text: str) -> dict:
    r = c.post(f"/api/v1/sessions/{sid}/messages", json={"text": text})
    assert r.status_code == 200, r.text
    return r.json()

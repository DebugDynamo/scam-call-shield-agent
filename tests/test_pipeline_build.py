"""Pipeline construction for every provider combination, with fake keys and NO network access."""
import itertools
import json
import socket

import pytest
from conftest import make_settings

from app.contracts.models import SessionCreate
from app.main import build_container
from app.voice.pipeline import build_voice_worker, tools_schema

FAKE = dict(gemini_api_key="fake-gemini", sarvam_api_key="fake-sarvam", elevenlabs_api_key="fake-eleven")


def fake_service_account() -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    return json.dumps({"type": "service_account", "project_id": "fake", "private_key_id": "x", "private_key": pem,
                       "client_email": "fake@fake.iam.gserviceaccount.com", "client_id": "1",
                       "token_uri": "https://oauth2.googleapis.com/token"})


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def deny(*a, **k):
        raise AssertionError("network access attempted while only building the pipeline")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "create_connection", deny)


def webrtc_transport():
    from pipecat.transports.base_transport import TransportParams
    from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
    from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport

    return SmallWebRTCTransport(SmallWebRTCConnection(), TransportParams(audio_in_enabled=True, audio_out_enabled=True))


async def build(settings, mode="browser"):
    c = await build_container(settings)
    sess = await c.sessions.create(SessionCreate(mode=mode))
    rec = await c.repo.get_session(sess.id)
    rt = c.sessions.runtimes[sess.id]
    return c, build_voice_worker(settings, c.sessions, rec, rt, webrtc_transport(), telephony=(mode == "phone"))


@pytest.mark.parametrize("stt,tts", list(itertools.product(["sarvam", "google", "mock"], ["google", "elevenlabs", "sarvam", "mock"])))
async def test_gemini_pipeline_builds_for_each_stt_tts_combo(stt, tts):
    extra = dict(google_credentials_json=fake_service_account()) if stt == "google" else {}
    s = make_settings(llm_provider="gemini", stt_provider=stt, tts_provider=tts, **FAKE, **extra)
    _c, b = await build(s)
    names = [type(p).__name__ for p in b.processors]
    assert names[0] == "SmallWebRTCInputTransport" and names[-2] == "SmallWebRTCOutputTransport"
    assert "GoogleLLMService" in names and b.simulated == (stt == "mock" or tts == "mock")
    assert b.worker.rtvi is not None  # RTVI auto-added for the browser UI


async def test_mock_pipeline_and_phone_sample_rates():
    s = make_settings()
    _c, b = await build(s)
    assert b.simulated and "MockLLMProcessor" in [type(p).__name__ for p in b.processors]
    _c, b = await build(s, "phone")
    assert b.worker  # 8 kHz telephony params applied without error


async def test_every_registry_tool_is_exposed_to_the_llm():
    c = await build_container(make_settings())
    names = {t.name for t in tools_schema(c.registry).standard_tools}
    assert names == {t.name for t in c.registry.all()} and "delete_task" in names


async def test_building_with_missing_credentials_raises_503_not_fake_service():
    from app.errors import AppError
    from app.providers import factory

    with pytest.raises(AppError) as e:
        factory.build_tts(make_settings(tts_provider="elevenlabs"))
    assert e.value.code == "PROVIDER_NOT_CONFIGURED"

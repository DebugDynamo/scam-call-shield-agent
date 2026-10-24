"""Provider requirement checks. Never include secret values, only env-var names."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import Settings
from ..errors import AppError, provider_not_configured

GOOGLE_STT_ENV = "GOOGLE_APPLICATION_CREDENTIALS (or GOOGLE_CREDENTIALS_JSON)"


@dataclass
class ProviderState:
    kind: str
    name: str
    status: str  # configured | not_configured | mock
    model: str | None = None
    missing: list[str] = field(default_factory=list)

    @property
    def simulated(self) -> bool:
        return self.status == "mock"


def llm_state(s: Settings) -> ProviderState:
    if s.llm_provider == "mock":
        return ProviderState("llm", "mock", "mock", "mock-rules-v1")
    missing = [] if s.gemini_api_key else ["GEMINI_API_KEY"]
    return ProviderState("llm", "gemini", "not_configured" if missing else "configured", s.gemini_model, missing)


def stt_state(s: Settings) -> ProviderState:
    p = s.stt_provider
    if p == "mock":
        return ProviderState("stt", "mock", "mock", "mock-null-stt")
    if p == "sarvam":
        missing = [] if s.sarvam_api_key else ["SARVAM_API_KEY"]
        return ProviderState("stt", p, "not_configured" if missing else "configured", "saaras (pipecat default)", missing)
    missing = [] if (s.google_credentials_json or s.google_application_credentials) else [GOOGLE_STT_ENV]
    return ProviderState("stt", "google", "not_configured" if missing else "configured", "latest_long", missing)


def tts_state(s: Settings) -> ProviderState:
    p = s.tts_provider
    if p == "mock":
        return ProviderState("tts", "mock", "mock", "mock-tone-tts")
    if p == "elevenlabs":
        missing = [] if s.elevenlabs_api_key else ["ELEVENLABS_API_KEY"]
        return ProviderState("tts", p, "not_configured" if missing else "configured", "pipecat default", missing)
    if p == "sarvam":
        missing = [] if s.sarvam_api_key else ["SARVAM_API_KEY"]
        return ProviderState("tts", p, "not_configured" if missing else "configured", "bulbul (pipecat default)", missing)
    missing = [] if s.gemini_api_key else ["GEMINI_API_KEY"]
    return ProviderState("tts", "google", "not_configured" if missing else "configured", "gemini-tts (pipecat default)", missing)


def require(state: ProviderState) -> ProviderState:
    if state.status == "not_configured":
        raise provider_not_configured(state.name, state.missing[0])
    return state


def require_for_mode(s: Settings, mode: str) -> dict[str, ProviderState]:
    """Raise 503 PROVIDER_NOT_CONFIGURED early (at session creation) instead of failing mid-call."""
    llm = require(llm_state(s))
    if mode in ("browser", "phone", "room"):
        return {"llm": llm, "stt": require(stt_state(s)), "tts": require(tts_state(s))}
    return {"llm": llm}


__all__ = ["AppError", "ProviderState", "llm_state", "require", "require_for_mode", "stt_state", "tts_state"]

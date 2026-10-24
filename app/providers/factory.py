"""Builds Pipecat services from validated settings. Importing pipecat service modules is lazy so that
only the selected provider's SDK is touched. Constructing services performs NO network I/O."""

from __future__ import annotations

from typing import Any

from ..config import Settings
from .status import llm_state, require, stt_state, tts_state


def build_llm(s: Settings, system_prompt: str):
    require(llm_state(s))
    from pipecat.services.google.llm import GoogleLLMService

    return GoogleLLMService(
        api_key=s.gemini_api_key,
        settings=GoogleLLMService.Settings(model=s.gemini_model, system_instruction=system_prompt),
    )


def build_stt(s: Settings):
    require(stt_state(s))
    if s.stt_provider == "mock":
        from .mock_voice import MockSTT

        return MockSTT()
    if s.stt_provider == "sarvam":
        from pipecat.services.sarvam.stt import SarvamSTTService

        return SarvamSTTService(api_key=s.sarvam_api_key, settings=SarvamSTTService.Settings(language=s.voice_language))
    from pipecat.services.google.stt import GoogleSTTService

    kwargs: dict[str, Any] = {}
    if s.google_credentials_json:
        kwargs["credentials"] = s.google_credentials_json
    else:
        kwargs["credentials_path"] = s.google_application_credentials
    return GoogleSTTService(settings=GoogleSTTService.Settings(language=s.voice_language), **kwargs)


def build_tts(s: Settings):
    require(tts_state(s))
    if s.tts_provider == "mock":
        from .mock_voice import MockTTS

        return MockTTS()
    if s.tts_provider == "elevenlabs":
        from pipecat.services.elevenlabs.tts import ElevenLabsTTSService

        return ElevenLabsTTSService(api_key=s.elevenlabs_api_key, settings=ElevenLabsTTSService.Settings(voice=s.elevenlabs_voice_id))
    if s.tts_provider == "sarvam":
        from pipecat.services.sarvam.tts import SarvamTTSService

        return SarvamTTSService(api_key=s.sarvam_api_key, settings=SarvamTTSService.Settings(voice=s.sarvam_tts_voice, language=s.voice_language))
    from pipecat.services.google.tts import GeminiTTSService

    return GeminiTTSService(api_key=s.gemini_api_key, settings=GeminiTTSService.Settings(voice=s.gemini_tts_voice))

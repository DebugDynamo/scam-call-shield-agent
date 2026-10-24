"""Explicit mock voice services (STT_PROVIDER=mock / TTS_PROVIDER=mock / LLM_PROVIDER=mock).

They exist to exercise the transport + pipeline plumbing (WebRTC handshake, RTVI events, UI) without paid
services. They are NOT speech recognition or synthesis: MockSTT never transcribes, MockTTS emits a quiet tone.
"""

from __future__ import annotations

import math
import struct
from collections.abc import AsyncGenerator

from pipecat.frames.frames import (
    Frame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TTSAudioRawFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.settings import STTSettings, TTSSettings
from pipecat.services.stt_service import STTService
from pipecat.services.tts_service import TTSService


class MockSTT(STTService):
    """Consumes audio, never produces a transcript."""

    def __init__(self, **kwargs):
        super().__init__(settings=STTSettings(model=None, language=None), ttfs_p99_latency=0.1, **kwargs)

    async def run_stt(self, audio: bytes, **kwargs) -> AsyncGenerator[Frame, None]:
        return
        yield  # pragma: no cover  (makes this an async generator)


class MockTTS(TTSService):
    """Turns text into a short low-volume tone (100 ms per 8 characters, max 2 s) so the UI shows 'bot speaking'."""

    def __init__(self, **kwargs):
        super().__init__(push_start_frame=True, push_stop_frames=True,
                         settings=TTSSettings(model=None, voice=None, language=None), **kwargs)

    def can_generate_metrics(self) -> bool:
        return False

    async def run_tts(self, text: str, context_id: str, **kwargs) -> AsyncGenerator[Frame, None]:
        rate = self.sample_rate or 16000
        secs = min(2.0, max(0.2, len(text) / 80))
        n = int(rate * secs)
        pcm = b"".join(struct.pack("<h", int(1200 * math.sin(2 * math.pi * 330 * i / rate))) for i in range(n))
        step = rate // 50 * 2
        for i in range(0, len(pcm), step):
            yield TTSAudioRawFrame(pcm[i : i + step], rate, 1, context_id=context_id)


class MockLLMProcessor(FrameProcessor):
    """Stands in for the LLM service when LLM_PROVIDER=mock: replies using the shared MockLLM rules (no tools)."""

    def __init__(self, reply_fn, **kwargs):
        super().__init__(**kwargs)
        self._reply_fn = reply_fn  # async (user_text) -> str

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMContextFrame) and direction == FrameDirection.DOWNSTREAM:
            ctx: LLMContext = frame.context
            user_text = ""
            for m in reversed(ctx.get_messages()):
                if isinstance(m, dict) and m.get("role") == "user":
                    c = m.get("content")
                    user_text = c if isinstance(c, str) else " ".join(p.get("text", "") for p in c if isinstance(p, dict))
                    break
            await self.push_frame(LLMFullResponseStartFrame())
            await self.push_frame(LLMTextFrame(await self._reply_fn(user_text)))
            await self.push_frame(LLMFullResponseEndFrame())
            return
        await self.push_frame(frame, direction)

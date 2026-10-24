# Providers

Selected with env vars (`LLM_PROVIDER`, `STT_PROVIDER`, `TTS_PROVIDER`). There is **no silent fallback**: a real provider
without credentials fails with `503 PROVIDER_NOT_CONFIGURED` naming the missing env var; `mock` exists only when selected
explicitly and is always reported as `simulated: true` (in `/ready`, `/api/v1/providers`, session objects, the web badge).

Verified against: `pipecat-ai 1.14.0` (installed classes read from source), `google-genai 2.33.0`.
"Verified" below means "read in the installed package source"; nothing here was called against a live paid API
(no keys were available in the build environment). Latency numbers are **estimates** from vendor docs / general
experience, not measurements.

## Capability table

| Kind | `*_PROVIDER` | Pipecat class (installed) | Creds (env) | Streaming | Languages | Notes / latency (estimate) |
|---|---|---|---|---|---|---|
| LLM | `gemini` | `GoogleLLMService` (voice path); `google-genai` SDK `client.aio.models.generate_content` (text path) | `GEMINI_API_KEY` (alias `GOOGLE_API_KEY`), `GEMINI_MODEL` (default `gemini-2.5-flash`) | Voice: streamed tokens. Text path: non-streaming single response per step | Model-dependent (multilingual) | Function calling used via the shared tool registry. First-token latency typically 300-900 ms for flash-class models (estimate). Model name is configurable; pipecat's own default is `gemini-3.6-flash` - the default here is the model the reference bot used, unverified today |
| LLM | `mock` | `MockLLM` (text), `MockLLMProcessor` (voice) | none | n/a | English rules | Deterministic regex rules (calculate / create task / list tasks / search / delete task / ask a human). `simulated: true` |
| STT | `sarvam` (default) | `SarvamSTTService` (websocket, `saaras:v4` default in pipecat 1.14) | `SARVAM_API_KEY` | Yes (websocket, server VAD signals) | Indian languages + English-India (`language_to_sarvam_language` in pipecat); set `VOICE_LANGUAGE` (default `en-IN`) | Tuned for 8 kHz telephony per the reference. ~300-800 ms to final transcript (estimate) |
| STT | `google` | `GoogleSTTService` (Cloud Speech-to-Text v2, gRPC streaming; model `latest_long`) | `GOOGLE_APPLICATION_CREDENTIALS` (path) or `GOOGLE_CREDENTIALS_JSON` (inline JSON). **A Gemini API key is not enough** - this is Google Cloud, not the Gemini API | Yes, interim results | 125+ per Google docs (not verified here) | Needs a GCP project with Speech-to-Text enabled and billing |
| STT | `mock` | `MockSTT` | none | n/a | n/a | Consumes audio, never transcribes. Plumbing tests only. `simulated: true` |
| TTS | `google` (default) | `GeminiTTSService` with `api_key` => Gemini API (GenAI) backend; model default `gemini-3.1-flash-tts-preview` (a *preview* model), voice `GEMINI_TTS_VOICE` (default `Kore`) | `GEMINI_API_KEY` | Yes | Multilingual (model-dependent) | 24 kHz output. Preview model: names/availability may change |
| TTS | `elevenlabs` | `ElevenLabsTTSService` (websocket) | `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID` (default is ElevenLabs' public sample voice id; your plan may not include it) | Yes | Multilingual per ElevenLabs docs (not verified) | Typically the most natural voices; ~250-600 ms to first audio (estimate) |
| TTS | `sarvam` | `SarvamTTSService` (websocket; `bulbul:v3`, voice `SARVAM_TTS_VOICE` default `shubh`) | `SARVAM_API_KEY` | Yes | Indian languages + English-India | Best option for Indic phone calls (per the reference deployment) |
| TTS | `mock` | `MockTTS` | none | n/a | n/a | Emits a quiet tone (100 ms per 8 chars) so the UI shows "bot speaking". `simulated: true` |

Not included: Deepgram STT (extra not installed to keep dependencies small; add `deepgram` to the pipecat extras and a
branch in `app/providers/factory.py` + `status.py` if wanted), Google Cloud TTS (non-Gemini voices), OpenAI. Selecting
them fails config validation at startup with a message listing valid values.

## Credential matrix by mode

| Session mode | Needs |
|---|---|
| `text` | LLM only |
| `browser` / `phone` | LLM + STT + TTS (checked at session creation, before any audio starts) |
| `room` | Not implemented (501 NOT_IMPLEMENTED) |
| phone extras | `PLIVO_AUTH_ID`, `PLIVO_AUTH_TOKEN`, `PLIVO_PHONE_NUMBER` (outbound only), `PUBLIC_BASE_URL` |

Minimal real-voice setups: (a) Sarvam STT + Sarvam TTS + Gemini LLM (`SARVAM_API_KEY`, `GEMINI_API_KEY`);
(b) Sarvam STT + Gemini TTS (`SARVAM_API_KEY`, `GEMINI_API_KEY`) - the defaults; (c) Google STT + ElevenLabs TTS.

## Voice activity / interruption

Silero VAD (`VADParams(confidence=0.75, start_secs=0.25, stop_secs=0.4, min_volume=0.4)`) inside the user aggregator,
Pipecat's default turn strategies (Smart Turn v3 model is bundled in the package). Barge-in: speaking over the bot makes
the aggregator interrupt the pipeline. These numbers are starting points adapted from the reference's phone tuning, not
validated on real audio here.

## Room / meeting adapter

`app/voice/room.py` defines `RoomAdapter` only. **Not implemented, untested.** There is no Google Meet integration.
A Daily or LiveKit implementation would need: a Daily/LiveKit account and API key, a room+token creator, a
`DailyTransport`/`LiveKitTransport` pipeline built with `build_voice_worker`, and consent/recording rules.

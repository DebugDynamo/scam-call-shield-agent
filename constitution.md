# Constitution of repo-voice-ai

How the 17 principles apply to this repo, stated truthfully (including where we fall short).

1. **Working software over boilerplate.** Text, browser WebRTC and Plivo paths run end to end against mock providers, with tests.
   Anything not run is labelled (below).
2. **Secure defaults, no committed secrets.** `.env` is git-ignored, only `.env.example` is tracked, the web app only knows
   `VITE_VOICE_API_URL`, logs/status endpoints never carry keys (tests assert a key does not appear in `/ready`, `/providers`).
   Gap: session routes are open by contract in dev; admin/outbound routes need `INTERNAL_API_KEY` only when it is set (loud startup warning otherwise).
3. **Explicit, validated configuration.** One pydantic-settings model; bad provider names / CORS `*` / bad URLs fail at startup.
4. **Modular without needless complexity.** Four seams only: Repository, tool registry, provider factory, transport adapters.
   One `VoiceAgent`, one pipeline builder.
5. **Provider abstraction where substitution is real.** LLM (gemini|mock), STT (sarvam|google|mock), TTS (google|elevenlabs|sarvam|mock)
   are genuinely swappable Pipecat services. No abstraction over transports beyond what Pipecat already provides.
6. **Typed contracts between services.** Pydantic models for every contract shape in `app/contracts/`; OpenAPI at `/docs`.
7. **Reproducible migrations and seeds.** Ordered SQL files tracked in `schema_migrations`; seed is idempotent (tested twice).
8. **Deterministic demo mode.** `DATA_MODE=mock` + `*_PROVIDER=mock` need no network/DB; the mock LLM is rule-based and deterministic;
   seeded tasks use fixed UUIDs.
9. **Automated tests of important paths.** Sessions, tools (invalid input, permissions, timeouts, approvals), provider errors,
   config precedence, telephony, WebRTC handshake, lifecycle/TTL, Postgres persistence across a simulated restart.
10. **Graceful provider failures/timeouts.** Gemini errors map to `PROVIDER_ERROR` / `PROVIDER_RATE_LIMITED` / `TIMEOUT`; tools have
    per-tool timeouts; sessions fail cleanly and tear down pipelines on disconnect/TTL/shutdown.
11. **Observable workflows and AI execution.** Every tool attempt (including rejected/invalid ones) is traced with status and
    latency; request ids propagate; the UI shows trace, status, latency. Redacted when `PERSIST_TRANSCRIPTS=false`.
12. **No fake integrations presented as working.** Room/meeting mode returns 501 and says so; search says "unavailable" when
    unconfigured; Plivo and every real STT/TTS/LLM path are labelled "not verified against the live service" until the manual
    smoke test is run; `GET /providers` exposes `verifiedAgainstLivePlivo: false`.
13. **No hardcoded domain assumptions in shared infra.** Neutral prompt, neutral tools (calculator, tasks, search, approvals).
14. **No silent fallback from a real provider to fake responses.** Missing creds -> 503 naming the env var; a provider error is an
    error response; Postgres mode never degrades to memory.
15. **No destructive DB reset without opt-in.** `python -m app.db reset` refuses without `CONFIRM_RESET=yes` (tested); normal
    startup never drops or auto-creates anything.
16. **Document decisions and trade-offs.** README limitations, `docs/PROVIDERS.md`, `../docs/REFERENCE_CODEBASE_AUDIT.md`,
    contract deviations listed in the build report.
17. **Verify behaviour; don't claim success without evidence.** Commands and outputs are recorded in the build report; the
    unverified list (live Gemini/STT/TTS, live Plivo, real browser microphone, Daily/Meet rooms) is explicit.

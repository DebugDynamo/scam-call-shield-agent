# repo-voice-ai

Reusable, domain-neutral real-time voice AI platform: **Python 3.12 + Pipecat 1.14 + FastAPI** (port **8003**) and a
**React + Vite** console (port **5173**). One `VoiceAgent` (system prompt, tool registry, conversation history) is shared
by three transports:

| Transport | Status | Notes |
|---|---|---|
| Text (HTTP) | Implemented, tested | `POST /api/v1/sessions/:id/messages`; Gemini or explicit mock |
| Browser voice (SmallWebRTC + RTVI) | Implemented; handshake/media/RTVI/teardown tested with a real aiortc client and mock providers; real STT/TTS/LLM **not** run | `@pipecat-ai/client-js` UI |
| Phone (Plivo) | Implemented; webhooks, XML, signature (V2), outbound request and the websocket pipeline tested with simulated Plivo; **not** run against live Plivo | Outbound needs API key AND `confirm:true` |
| Room (meeting) | **NOT implemented** (interface only, 501) | No Meet integration |

Contract: `../docs/API_CONTRACTS.md` (voice section). OpenAPI at `http://localhost:8003/docs` (`/docs-json`).

## Quick start (no credentials, no database)

```bash
uv sync
cp .env.example .env            # optional; defaults are safe
LLM_PROVIDER=mock STT_PROVIDER=mock TTS_PROVIDER=mock uv run python -m app
curl localhost:8003/health ; curl localhost:8003/ready
cd web && npm install && npm run dev      # http://localhost:5173  (shows a DEMO / SIMULATED badge)
```

Without `LLM_PROVIDER=mock` the default is `gemini`; with no `GEMINI_API_KEY` the server starts, `/ready` answers 503 and
creating a session returns `503 PROVIDER_NOT_CONFIGURED` naming the variable. It never fakes a Gemini reply.

Text session by hand:

```bash
SID=$(curl -s -X POST localhost:8003/api/v1/sessions -H 'content-type: application/json' -d '{"mode":"text"}' | jq -r .id)
curl -s -X POST localhost:8003/api/v1/sessions/$SID/messages -H 'content-type: application/json' -d '{"text":"calculate 12 * (3 + 4)"}'
curl -s -X POST localhost:8003/api/v1/sessions/$SID/end
```

## Real providers

```bash
export LLM_PROVIDER=gemini GEMINI_API_KEY=...        # text chat works now
export STT_PROVIDER=sarvam SARVAM_API_KEY=...        # voice also needs STT + TTS
export TTS_PROVIDER=google                            # Gemini TTS, uses GEMINI_API_KEY
uv run python -m app
```
See `docs/PROVIDERS.md` (capability table, creds, caveats) and `docs/MANUAL_SMOKE_TEST.md` (real mic walkthrough).

## Postgres mode

```bash
docker compose up -d postgres            # from the workspace root (db garbathon_voice, postgres/postgres)
export DATA_MODE=postgres
uv run python -m app.db migrate          # explicit SQL migrations in ./migrations (no auto-create)
uv run python -m app.db seed             # idempotent demo tasks
uv run python -m app                     # fails fast (non-zero) if the DB is down; never falls back to memory
CONFIRM_RESET=yes uv run python -m app.db reset   # destructive; requires the opt-in
```
`DATABASE_URL` wins over `DB_HOST/DB_PORT/DB_USERNAME/DB_PASSWORD/DB_DATABASE`; `DB_SSL=true` adds `sslmode=require`.
Memory mode (`DATA_MODE=mock`, default) is a real repository implementation seeded with the same demo tasks; all data is
lost on restart.

## Tools

`calculator`, `web_search` (explicit *unavailable* unless `SEARCH_API_URL` is set), `create_task`, `get_task`,
`list_tasks`, `update_task`, `delete_task` (requires approval), `request_human_approval`, plus optional `agent_*` tools
proxied from repo-agent (`AGENT_API_URL`). Guardrails: JSON-schema validation, permission check
(`TOOL_PERMISSIONS`), per-tool timeouts, approval tokens (single-use, bound to tool+arguments), every call traced.
Approvals are resolved by a human through `POST /api/v1/sessions/:id/approvals/:approvalId` (also buttons in the UI);
the model cannot approve itself.

## Phone setup (Plivo)

Set `PLIVO_AUTH_ID`, `PLIVO_AUTH_TOKEN`, `PLIVO_PHONE_NUMBER`, `PUBLIC_BASE_URL` (public https URL of this server).
`POST /api/v1/telephony/outbound {"to":"+E164","confirm":true}` (with `X-Api-Key` if `INTERNAL_API_KEY` is set) creates
a phone session and asks Plivo to call. Plivo then hits `/api/v1/telephony/plivo/answer?session_id=...` (signature V2
verified), gets `<Stream bidirectional>` pointing at `wss://<PUBLIC_BASE_URL>/api/v1/telephony/plivo/stream/<id>/<token>`,
and `/plivo/hangup` ends the session. Nothing is dialled at startup or in tests. No `<Record>`; raw audio is never stored.

## Layout

```
app/            config, errors, contracts/ (pydantic), domain, repositories/ (memory, postgres), db/ (migrate/seed/reset CLI)
app/agent/      VoiceAgent (core.py) + text LLMs (Gemini SDK, mock)
app/tools/      registry, built-in tools, executor + approvals, repo-agent proxy
app/providers/  status checks, Pipecat service factory, mock voice services
app/voice/      pipeline.py (shared), browser.py (SmallWebRTC), plivo.py, room.py (interface only)
app/services/   SessionService (lifecycle, TTL cleanup, approvals)   app/api/ routes
migrations/     explicit SQL        tests/ pytest (no paid services)        web/ React console
```

## Commands

```bash
uv run pytest -q          # 74 tests; Postgres tests skip clearly if garbathon_voice is unreachable
uv run ruff check .
uv run python scripts/webrtc_smoke.py http://localhost:8003   # against a running mock-mode server
cd web && npm run build
```

## Versions

pipecat-ai 1.14.0 (extras google, sarvam, elevenlabs, silero, webrtc, runner), fastapi 0.162, google-genai 2.33,
psycopg 3 + pool, pydantic-settings 2; web: react 19.3, vite 8.3, typescript 7.0, `@pipecat-ai/client-js` 1.14.0,
`client-react` 1.9.0, `small-webrtc-transport` 1.11.0 (client-js must match the transport's `~1.14` peer range).

## Known limitations

- Run a **single** uvicorn worker (sessions, WebRTC peers and websocket tokens live in process memory).
- The official JS client PATCHes ICE candidates to the *offer* URL; both `/offer` and `/ice` accept PATCH.
- Gemini tool-calling over the voice path and every real STT/TTS service are built but were not exercised with credentials.
- Text path calls Gemini non-streaming; voice path streams.
- Voice sessions learn about an approval decision through a system note on the next turn.
- No authentication on session routes (contract says dev-open); put it behind your own gateway before exposing.

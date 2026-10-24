# Contributor guide

## Configure

`cp .env.example .env`, edit. Config is validated at startup by `app/config.py` (pydantic-settings); invalid provider names,
a `*` in `CORS_ORIGINS`, or a bad `PUBLIC_BASE_URL` stop the server with a readable message.

## Run

```bash
uv sync
LLM_PROVIDER=mock STT_PROVIDER=mock TTS_PROVIDER=mock uv run python -m app    # no creds, no DB
cd web && npm install && npm run dev
```
Docs: http://localhost:8003/docs. Port busy => uvicorn prints `address already in use` and exits non-zero (3). Change `PORT` (and
`VITE_VOICE_API_URL` in `web/.env`).

## Switch providers

Set `LLM_PROVIDER` (`gemini|mock`), `STT_PROVIDER` (`sarvam|google|mock`), `TTS_PROVIDER` (`google|elevenlabs|sarvam|mock`)
and the matching keys (table in `docs/PROVIDERS.md`). Check `GET /ready` or `GET /api/v1/providers`: `configured` vs
`not_configured` (with the missing env var names) vs `mock`. Text sessions need only the LLM; voice needs all three.

To **add a provider**: add the name to the tuple in `config.py`, a branch in `providers/status.py` (requirements) and
`providers/factory.py` (Pipecat service; add the pipecat extra in `pyproject.toml`), then extend
`tests/test_pipeline_build.py` and `docs/PROVIDERS.md`. Read the installed service class first.

## Add a tool

1. Write `async def my_tool(ctx: ToolContext, args: dict) -> dict` in `app/tools/builtin.py`. Raise `ToolFailure(msg, code)` for
   expected failures and `ToolUnavailable(msg)` when an integration is not configured. Return plain JSON-serialisable data.
2. Register it in `build_registry()` with a strict JSON schema (`additionalProperties: False`), `risk`, `permissions`
   (must be in `TOOL_PERMISSIONS`), `timeout_ms`, and `requires_approval=True` for anything consequential/irreversible.
3. Add a rule to `MockLLM` if you want the mock to call it, and tests in `tests/test_tools.py` (valid, invalid input,
   permission, timeout, approval). The tool is automatically exposed to Gemini (text path) and Pipecat (voice path).

## Approvals

Tool with `requires_approval` -> first call returns `pending_approval` + `approvalId` and stores the call. A human calls
`POST /api/v1/sessions/:id/approvals/:approvalId {"decision":"approve|reject"}`; approve issues a single-use token bound
to the exact tool+arguments and executes the call. `request_human_approval` asks for approval of free-form actions.
Expires after `APPROVAL_TTL_SECONDS`.

## Persistence, migrations, seeds

`DATA_MODE=mock` (default) = in-memory. `DATA_MODE=postgres`: `uv run python -m app.db migrate` (applies `migrations/*.sql`
in order, tracked in `schema_migrations`), `seed` (idempotent), `status`, `reset` (needs `CONFIRM_RESET=yes`). Add a schema
change as a new numbered file, e.g. `migrations/0002_xyz.sql`; never edit an applied one. Mirror the change in both
repositories and `tests/test_postgres.py`. On startup, sessions left `created/active` by a previous process are marked `ended`.
A background sweeper ends sessions idle longer than `SESSION_TTL_SECONDS`. `PERSIST_TRANSCRIPTS=false` stores no message
text and redacts tool args/results in traces.

## Browser voice

Client flow: `POST /sessions {mode:"browser"}` -> `client.connect({webrtcRequestParams:{endpoint: .../sessions/:id/offer}})`.
Pipecat's `SmallWebRTCRequestHandler` does the SDP/ICE work; the official client sends trickle ICE as `PATCH` to the same
offer URL (we also serve `PATCH /ice`). RTVI events (`bot-ready`, transcripts, speaking, metrics) are produced by Pipecat's
automatic RTVI processor; tool/latency events are sent as RTVI server messages.

## Phone

See README "Phone setup". Local simulation of Plivo frames is in `tests/test_telephony.py`. Signature check is Plivo
V2 only; V3 is not implemented. Single worker only.

## Tests

`uv run pytest -q` (add `-k name`, `--timeout 60` is the default). Postgres tests need the docker DB and skip otherwise.
`tests/test_pipeline_build.py` constructs the pipeline for every STT x TTS combination with fake keys and blocks network.

## Common errors

| Symptom | Cause / fix |
|---|---|
| `503 PROVIDER_NOT_CONFIGURED ... GEMINI_API_KEY` | Set the key or choose `LLM_PROVIDER=mock` for demos |
| `/ready` 503 `not_ready` | LLM not configured, or Postgres down |
| Startup "no migrations applied" | `uv run python -m app.db migrate` |
| `DATABASE_UNAVAILABLE` at startup | DB not reachable: check `DATABASE_URL`/`DB_*`, docker postgres running |
| Voice start returns 503 mentioning `SARVAM_API_KEY` / `GOOGLE_APPLICATION_CREDENTIALS` | STT/TTS creds missing (Google STT needs Cloud creds, not a Gemini key) |
| Browser: "Microphone permission denied" | Allow mic in the browser; needs https or localhost |
| Plivo `401 Invalid Plivo signature` | `PUBLIC_BASE_URL` must equal the URL Plivo calls exactly (scheme, host, path, query) |
| 409 "already has a peer connection" | One WebRTC peer per session; create a new session |
| `ruff` / tests fail after pipecat upgrade | Re-read the installed pipecat source; APIs move between minors |

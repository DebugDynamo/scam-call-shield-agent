# CLAUDE.md: repo-voice-ai

Domain-neutral real-time voice AI service (Python 3.12, Pipecat 1.14, FastAPI :8003) with a React console in `web/` (:5173).
Binding contract: `../docs/API_CONTRACTS.md` (voice section) and `../docs/BUILD_BRIEF.md`. Deviations/extensions are listed in
`README.md` and must be reported to the lead, not hidden.

## Commands

```bash
uv sync                                   # install (repo-local .venv)
uv run pytest -q                          # all tests, no paid services; Postgres tests skip if :5432 unreachable
uv run ruff check .                       # lint (must pass)
LLM_PROVIDER=mock STT_PROVIDER=mock TTS_PROVIDER=mock uv run python -m app      # run server on :8003
uv run python -m app.db {migrate|seed|status}   # postgres; `reset` needs CONFIRM_RESET=yes
uv run python scripts/webrtc_smoke.py http://localhost:8003   # real aiortc handshake against mock-mode server
cd web && npm run build                   # typecheck + production build
```

## Architecture (read before changing)

- `app/agent/core.py` `VoiceAgent`: prompt + tool access + history. ALL transports use it. Do not fork behaviour per transport.
- `app/tools/`: `registry.py` (ToolSpec), `builtin.py` (add tools here), `executor.py` (validation, permissions, approval
  tokens, timeout, trace; never raises into the model), `agent_proxy.py` (optional repo-agent).
- `app/voice/pipeline.py`: the one place that builds Pipecat pipelines. `browser.py` and `plivo.py` only wire transports.
- `app/providers/`: `status.py` decides configured/not_configured/mock WITHOUT secrets; `factory.py` builds Pipecat services.
- `app/repositories/`: `Repository` Protocol; memory and Postgres must stay behaviourally identical. SQL lives only in
  `postgres.py` and `migrations/*.sql`. Never `CREATE TABLE` at runtime.
- `app/contracts/models.py`: pydantic models for every public shape (camelCase on the wire). Extensions are marked `EXT`.
- Errors: raise `AppError(code, ...)`; the handler produces the contract error body + `X-Request-Id`.

## Conventions and rules

- Python 3.12, type hints, ruff-clean, small modules, no speculative abstractions. Inspect existing code before adding.
- Read installed Pipecat source (`.venv/lib/python3.12/site-packages/pipecat`) before using an API; names have changed across
  versions (e.g. `PipelineWorker`/`WorkerRunner` replace `PipelineTask`/`PipelineRunner`; schemas moved to `pipecat.adapters.llm.schemas`).
- Providers: a missing credential in real mode is `PROVIDER_NOT_CONFIGURED` (503). Mock is only for explicit `*_PROVIDER=mock`
  and always `simulated: true`. Never degrade to a fake response on provider failure; map errors to `PROVIDER_ERROR` / `TIMEOUT` /
  `PROVIDER_RATE_LIMITED`.
- Security: secrets only in env; never log keys/headers/tokens; never return them (status endpoints list env var NAMES only).
  Validate all input (pydantic + JSON schema for tools). `INTERNAL_API_KEY` guards outbound calls and admin routes. Plivo webhooks
  are signature-checked. CORS is an allow-list (never `*`). Calls/emails are never initiated at startup or in tests.
- Consequential tools set `requires_approval=True`; the approval is resolved only through the HTTP endpoint by a human.
- No raw audio is stored. `PERSIST_TRANSCRIPTS=false` must keep message text and tool args/results out of storage.
- Postgres mode never falls back to memory; startup fails loudly if the DB is down or unmigrated.
- Run a single uvicorn worker (in-process session/WebRTC state).

## Testing discipline

Run `uv run pytest -q` and `uv run ruff check .` after every change; add a test with every behaviour change (tool, error path,
repository method, endpoint). Tests must not need network or keys (the pipeline-build test blocks sockets on purpose).
Real-provider behaviour is verified only by `docs/MANUAL_SMOKE_TEST.md`; never claim it works without running it.

## Boundaries

Only edit inside this repo. `../docs/` and the workspace root are owned by the lead. The reference project
`/Users/cn1/Documents/collarup` is read-only; never open its `.env`/`secrets`; never copy its prompts, keys or data.

# Manual smoke test: real microphone to real audio

Automated tests never use a microphone or paid services. Run this by hand once you have credentials. Record the
result in your PR/notes; nothing here has been run by the original author of this repo.

## 0. Prerequisites

- Chrome/Edge/Firefox on `http://localhost:5173` (microphone works on localhost without https), headphones (avoid echo).
- `uv sync` and `cd web && npm install`.
- A `.env` (copy `.env.example`) with at least:
  `LLM_PROVIDER=gemini`, `GEMINI_API_KEY=...`, `STT_PROVIDER=sarvam`, `SARVAM_API_KEY=...`, `TTS_PROVIDER=google`
  (or `elevenlabs` + `ELEVENLABS_API_KEY`). `INTERNAL_API_KEY` optional.

## 1. Start

```bash
uv run python -m app                    # terminal 1  -> http://localhost:8003/docs
cd web && npm run dev                   # terminal 2  -> http://localhost:5173
curl -s localhost:8003/ready            # expect "status":"ready", provider/stt/tts "configured" (not "mock")
```

## 2. Text first (cheap check that the LLM key works)

Open the "Text chat" tab and send `calculate 17 * 23`. Expect a tool call `calculator -> ok` in the trace panel and a
natural-language reply containing 391. No DEMO badge should show if `DATA_MODE=postgres` and nothing is mock.

## 3. Voice

1. Open "Browser voice", click **Start**. Allow the microphone prompt.
2. State goes `connecting -> connected`, then "bot ready". The bot should greet you within ~2-4 s.
3. Say: "What is 48 times 12?" Expect your words in the transcript, a `calculator` entry in the voice log/trace, and a
   spoken answer (576).
4. Say: "Create a task called buy milk." Expect `create_task -> ok`.
5. Say: "Delete that task." Expect `delete_task -> pending_approval`; the bot must say it needs approval. Approve or
   reject in the right-hand trace panel (or `POST /api/v1/sessions/<id>/approvals/<approvalId>` with
   `{"decision":"approve"}`). The bot is told about the decision via a system note on its next turn.
6. Interrupt: while the bot is speaking a long answer, start talking. Expect the bot to stop within about a second.
7. Click **Stop**. Session must show `ended` (`GET /api/v1/sessions/<id>`). Server log shows teardown, no tracebacks.

## 4. Failure checks

- Deny the mic permission: UI shows a clear permission message; session is ended.
- Stop the server mid-call: UI shows an error/disconnected state.
- Unset `SARVAM_API_KEY`, restart, click Start: UI shows `503 PROVIDER_NOT_CONFIGURED ... SARVAM_API_KEY`.

## 5. Phone (optional, costs money, places a real call)

Requires a Plivo account, a verified/own destination number, and a public https URL (e.g. a tunnel) as `PUBLIC_BASE_URL`.

```bash
curl -s -X POST localhost:8003/api/v1/telephony/outbound -H 'content-type: application/json' \
  -H "X-Api-Key: $INTERNAL_API_KEY" -d '{"to":"+<your number in E.164>","confirm":true}'
```

Expect your phone to ring, the bot to greet you, and `GET /api/v1/sessions/<id>` to go `active` then `ended` on hangup.
Unverified against live Plivo: signature verification (V2), answer XML acceptance, websocket framing.
If Plivo rejects webhook signatures, set `PLIVO_VERIFY_SIGNATURE=false` temporarily to isolate the problem.

## 6. Local, no-credentials check of the WebRTC plumbing

```bash
LLM_PROVIDER=mock STT_PROVIDER=mock TTS_PROVIDER=mock uv run python -m app
uv run python scripts/webrtc_smoke.py http://localhost:8003   # aiortc client; prints PASS/FAIL
```
This proves signalling, media transport, RTVI events and teardown. It does not test speech recognition or synthesis.

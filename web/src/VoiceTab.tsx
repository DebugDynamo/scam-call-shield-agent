import { PipecatClient, type TransportState } from "@pipecat-ai/client-js";
import { PipecatClientAudio, PipecatClientProvider } from "@pipecat-ai/client-react";
import { SmallWebRTCTransport } from "@pipecat-ai/small-webrtc-transport";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, ApiException, type Ready, type Session } from "./api";

interface Line { who: "user" | "bot" | "tool" | "system"; text: string; interim?: boolean }

export default function VoiceTab({ ready, onSession }: { ready: Ready | null; onSession: (s: Session | null) => void }) {
  const [state, setState] = useState<TransportState>("disconnected");
  const [botReady, setBotReady] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [userSpeaking, setUserSpeaking] = useState(false);
  const [lines, setLines] = useState<Line[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [latencyMs, setLatencyMs] = useState<number | null>(null);
  const [ttfb, setTtfb] = useState<string>("");
  const [session, setSession] = useState<Session | null>(null);
  const [muted, setMuted] = useState(false);
  const sessionRef = useRef<Session | null>(null);

  const push = (l: Line) => setLines((x) => [...x, l]);

  const client = useMemo(
    () =>
      new PipecatClient({
        transport: new SmallWebRTCTransport({ waitForICEGathering: false }),
        enableMic: true,
        enableCam: false,
        callbacks: {
          onTransportStateChanged: (s) => setState(s),
          onBotReady: () => { setBotReady(true); push({ who: "system", text: "Bot is ready" }); },
          onDisconnected: () => { setBotReady(false); setSpeaking(false); setUserSpeaking(false); },
          onBotStartedSpeaking: () => setSpeaking(true),
          onBotStoppedSpeaking: () => setSpeaking(false),
          onUserStartedSpeaking: () => setUserSpeaking(true),
          onUserStoppedSpeaking: () => setUserSpeaking(false),
          onUserTranscript: (d) => {
            if (d.final) push({ who: "user", text: d.text });
          },
          onBotOutput: (d) => {
            if (d.text_type === "sentence" || d.text_type === undefined) {
              push({ who: "bot", text: d.text });
            }
          },
          onMetrics: (m) => {
            const first = m.ttfb?.[0];
            if (first) setTtfb(`${first.processor}: ${Math.round(first.value * 1000)} ms`);
          },
          onServerMessage: (d: { type?: string; ms?: number; tool?: string; status?: string; text?: string }) => {
            if (d?.type === "latency" && typeof d.ms === "number") setLatencyMs(d.ms);
            if (d?.type === "tool") push({ who: "tool", text: `${d.tool} → ${d.status}` });
            if (d?.type === "approval_resolved") push({ who: "system", text: d.text ?? "approval resolved" });
            const s = sessionRef.current;
            if (s) void api.getSession(s.id).then((x) => { setSession(x); onSession(x); }).catch(() => undefined);
          },
          onError: (m) => setError(`Voice error: ${JSON.stringify((m as { data?: unknown }).data ?? m)}`),
        },
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  useEffect(() => () => { void client.disconnect().catch(() => undefined); }, [client]);

  const missing = [...(ready?.checks.provider.missing ?? []), ...(ready?.checks.stt?.missing ?? []), ...(ready?.checks.tts?.missing ?? [])];
  const simulated = ready?.checks.stt?.status === "mock" || ready?.checks.tts?.status === "mock" || ready?.checks.provider.status === "mock";

  const start = async () => {
    setError(null);
    setLines([]);
    setLatencyMs(null);
    try {
      if (!navigator.mediaDevices?.getUserMedia) throw new Error("This browser cannot capture the microphone (needs https or localhost).");
      const s = await api.createSession("browser");
      sessionRef.current = s;
      setSession(s);
      onSession(s);
      await client.connect({ webrtcRequestParams: { endpoint: api.offerUrl(s.id) } } as never);
    } catch (e) {
      const msg = e instanceof ApiException ? `${e.error.code}: ${e.error.message}` : e instanceof Error ? e.message : String(e);
      setError(/permission|denied|NotAllowed/i.test(msg) ? `Microphone permission was denied. Allow it in the browser address bar and retry. (${msg})` : msg);
      if (sessionRef.current) await api.end(sessionRef.current.id).catch(() => undefined);
    }
  };

  const stop = async () => {
    try { await client.disconnect(); } catch { /* ignore */ }
    const s = sessionRef.current;
    if (s) {
      const done = await api.end(s.id).catch(() => null);
      if (done) { setSession(done); onSession(done); }
    }
  };

  const connected = state === "connected" || state === "ready";
  const busy = state === "connecting" || state === "authenticating" || state === "initializing" || state === "initialized";

  return (
    <PipecatClientProvider client={client}>
      <PipecatClientAudio />
      <div className="panel">
        <div className="bar">
          <strong>Browser voice (SmallWebRTC + RTVI)</strong>
          <span className={`pill ${connected ? "ok" : busy ? "warn" : ""}`}>{state}</span>
          {botReady && <span className="pill ok">bot ready</span>}
          {speaking && <span className="pill speaking">bot speaking</span>}
          {userSpeaking && <span className="pill warn">you are speaking</span>}
          {simulated && <span className="badge demo">MOCK PROVIDERS: audio is simulated, speech is NOT recognised</span>}
        </div>
        {missing.length > 0 && <div className="error">Voice needs server env vars: {[...new Set(missing)].join(", ")}. Starting will return 503 PROVIDER_NOT_CONFIGURED.</div>}
        <div className="controls">
          <button onClick={start} disabled={connected || busy}>Start (asks for microphone)</button>
          <button onClick={stop} disabled={!connected && !busy}>Stop</button>
          <button onClick={() => { client.enableMic(muted); setMuted(!muted); }} disabled={!connected}>{muted ? "Unmute mic" : "Mute mic"}</button>
        </div>
        <div className="diag">
          <span>session: {session ? `${session.id.slice(0, 8)} (${session.status})` : "-"}</span>
          <span>last turn latency: {latencyMs !== null ? `${latencyMs} ms` : "-"}</span>
          <span>first TTFB metric: {ttfb || "-"}</span>
        </div>
        <p className="hint">Interruption (barge-in) is handled by the server pipeline: speak while the bot talks and it stops.</p>
        <div className="log">
          {lines.map((l, i) => (
            <div key={i} className={`msg ${l.who === "bot" ? "assistant" : l.who}`}>
              <span className="role">{l.who}</span>
              <span>{l.text}</span>
            </div>
          ))}
        </div>
        {error && <div className="error">{error}</div>}
      </div>
    </PipecatClientProvider>
  );
}

import { useCallback, useEffect, useState } from "react";
import { api, API_URL, type Health, type Ready, type Session } from "./api";
import ChatTab from "./ChatTab";
import VoiceTab from "./VoiceTab";
import TracePanel from "./TracePanel";

export default function App() {
  const [tab, setTab] = useState<"chat" | "voice">("chat");
  const [health, setHealth] = useState<Health | null>(null);
  const [ready, setReady] = useState<Ready | null>(null);
  const [apiError, setApiError] = useState<string | null>(null);
  const [session, setSession] = useState<Session | null>(null);

  const refreshStatus = useCallback(async () => {
    try {
      setHealth(await api.health());
      setReady(await api.ready());
      setApiError(null);
    } catch (e) {
      setHealth(null);
      setReady(null);
      setApiError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void refreshStatus();
    const t = setInterval(() => void refreshStatus(), 10000);
    return () => clearInterval(t);
  }, [refreshStatus]);

  const demo = health?.dataMode === "mock" || ready?.simulated || session?.simulated;

  return (
    <div className="app">
      <header>
        <h1>Voice AI Console</h1>
        <div className="badges">
          {demo && <span className="badge demo" title="Mock data mode and/or a mock provider is active">DEMO / SIMULATED</span>}
          <span className={`badge ${health ? "ok" : "bad"}`}>{health ? `API ${health.version} (${health.dataMode})` : "API unreachable"}</span>
          {ready && (
            <>
              <span className={`badge ${ready.checks.provider.status === "not_configured" ? "bad" : "ok"}`}>
                LLM: {ready.checks.provider.name} [{ready.checks.provider.status}]
              </span>
              <span className="badge neutral">STT: {ready.checks.stt?.name} [{ready.checks.stt?.status}]</span>
              <span className="badge neutral">TTS: {ready.checks.tts?.name} [{ready.checks.tts?.status}]</span>
              <span className="badge neutral">DB: {ready.checks.database.status}</span>
            </>
          )}
        </div>
      </header>
      {apiError && <div className="error">{apiError} (VITE_VOICE_API_URL = {API_URL})</div>}
      {ready && ready.checks.provider.missing.length > 0 && (
        <div className="error">LLM provider not configured. Missing env: {ready.checks.provider.missing.join(", ")} (server-side).</div>
      )}
      <nav>
        <button className={tab === "chat" ? "active" : ""} onClick={() => setTab("chat")}>Text chat</button>
        <button className={tab === "voice" ? "active" : ""} onClick={() => setTab("voice")}>Browser voice</button>
      </nav>
      <main>
        <section className="left">
          {tab === "chat" ? <ChatTab onSession={setSession} /> : <VoiceTab ready={ready} onSession={setSession} />}
        </section>
        <aside className="right">
          <TracePanel session={session} onSession={setSession} />
        </aside>
      </main>
    </div>
  );
}

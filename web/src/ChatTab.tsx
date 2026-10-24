import { useEffect, useRef, useState } from "react";
import { api, ApiException, type Message, type Session } from "./api";

export default function ChatTab({ onSession }: { onSession: (s: Session | null) => void }) {
  const [session, setSession] = useState<Session | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastMs, setLastMs] = useState<number | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => bottom.current?.scrollIntoView({ behavior: "smooth" }), [messages]);

  const fail = (e: unknown) => setError(e instanceof ApiException ? `${e.error.code}: ${e.error.message}` : String(e));

  const refresh = async (id: string) => {
    const s = await api.getSession(id);
    setSession(s);
    onSession(s);
  };

  const ensureSession = async (): Promise<Session> => {
    if (session && session.status !== "ended" && session.status !== "failed") return session;
    const s = await api.createSession("text");
    setSession(s);
    setMessages([]);
    onSession(s);
    return s;
  };

  const send = async () => {
    const t = text.trim();
    if (!t || busy) return;
    setBusy(true);
    setError(null);
    setText("");
    try {
      const s = await ensureSession();
      setMessages((m) => [...m, { role: "user", text: t, at: new Date().toISOString() }]);
      const t0 = performance.now();
      const r = await api.send(s.id, t);
      setLastMs(Math.round(performance.now() - t0));
      setMessages((m) => [...m, ...r.messages.slice(1)]);
      await refresh(s.id);
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  };

  const end = async () => {
    if (!session) return;
    try {
      const s = await api.end(session.id);
      setSession(s);
      onSession(s);
    } catch (e) {
      fail(e);
    }
  };

  const decide = async (aid: string, d: "approve" | "reject") => {
    if (!session) return;
    try {
      await api.decide(session.id, aid, d);
      const s = await api.getSession(session.id);
      setSession(s);
      onSession(s);
      setMessages(s.messages);
    } catch (e) {
      fail(e);
    }
  };

  const pending = session?.approvals.filter((a) => a.status === "pending") ?? [];

  return (
    <div className="panel">
      <div className="bar">
        <strong>Text session</strong>
        <span className="muted">{session ? `${session.id.slice(0, 8)} · ${session.status}` : "no session yet (created on first message)"}</span>
        {lastMs !== null && <span className="muted">last reply {lastMs} ms</span>}
        <button onClick={end} disabled={!session || session.status === "ended"}>End session</button>
      </div>
      <p className="hint">Works without a microphone. Try: “calculate 12 * (3 + 4)”, “create task buy milk”, “list tasks”, “delete task &lt;id&gt;” (needs approval), “search for pipecat”.</p>
      <div className="log">
        {messages.map((m, i) => (
          <div key={i} className={`msg ${m.role}`}>
            <span className="role">{m.role}</span>
            <span>{m.role === "tool" ? <code>{m.text}</code> : m.text}</span>
          </div>
        ))}
        {busy && <div className="msg assistant muted">thinking…</div>}
        <div ref={bottom} />
      </div>
      {pending.map((a) => (
        <div key={a.id} className="approval">
          Approval needed: <code>{a.tool}</code> <code>{JSON.stringify(a.arguments)}</code>
          <button onClick={() => decide(a.id, "approve")}>Approve</button>
          <button onClick={() => decide(a.id, "reject")}>Reject</button>
        </div>
      ))}
      {error && <div className="error">{error}</div>}
      <form onSubmit={(e) => { e.preventDefault(); void send(); }} className="composer">
        <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Type a message" disabled={busy} />
        <button type="submit" disabled={busy || !text.trim()}>Send</button>
      </form>
    </div>
  );
}

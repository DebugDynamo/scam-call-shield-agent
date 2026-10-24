import { useState } from "react";
import { api, type Session, type ToolContract } from "./api";

export default function TracePanel({ session, onSession }: { session: Session | null; onSession: (s: Session) => void }) {
  const [tools, setTools] = useState<ToolContract[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const loadTools = async () => {
    try { setTools(await api.tools()); setErr(null); } catch (e) { setErr(String(e)); }
  };

  const decide = async (aid: string, d: "approve" | "reject") => {
    if (!session) return;
    try { await api.decide(session.id, aid, d); onSession(await api.getSession(session.id)); setErr(null); } catch (e) { setErr(String(e)); }
  };
  const pending = session?.approvals.filter((a) => a.status === "pending") ?? [];

  return (
    <div className="panel">
      <strong>Session &amp; tool trace</strong>
      {!session && <p className="muted">No session yet.</p>}
      {session && (
        <>
          <dl>
            <dt>id</dt><dd>{session.id}</dd>
            <dt>mode / transport</dt><dd>{session.mode} / {session.transport}</dd>
            <dt>status</dt><dd>{session.status}{session.error ? ` (${session.error})` : ""}</dd>
            <dt>providers</dt><dd>llm={session.providers.llm} stt={session.providers.stt ?? "-"} tts={session.providers.tts ?? "-"}</dd>
            <dt>simulated</dt><dd>{String(session.simulated)}</dd>
          </dl>
          {session.mode === "browser" && pending.map((a) => (
            <div key={a.id} className="approval">
              Approval needed: <code>{a.tool}</code> <code>{JSON.stringify(a.arguments)}</code>
              <button onClick={() => decide(a.id, "approve")}>Approve</button>
              <button onClick={() => decide(a.id, "reject")}>Reject</button>
            </div>
          ))}
          <h3>Tool calls ({session.toolTrace.length})</h3>
          {session.toolTrace.map((t, i) => (
            <details key={i} className={`trace ${t.status}`}>
              <summary><code>{t.tool}</code> <span className="pill">{t.status}</span> {t.latencyMs !== undefined && <span className="muted">{t.latencyMs} ms</span>}</summary>
              <pre>{JSON.stringify({ arguments: t.arguments, result: t.result }, null, 2)}</pre>
            </details>
          ))}
        </>
      )}
      <h3>Tool registry</h3>
      <button onClick={loadTools}>{tools ? "Reload" : "Load"} tools</button>
      {err && <div className="error">{err}</div>}
      {tools?.map((t) => (
        <div key={t.name} className="tool">
          <code>{t.name}</code> <span className="pill">{t.risk}</span> {t.requiresApproval && <span className="pill warn">approval</span>}
          <div className="muted">{t.description}</div>
        </div>
      ))}
    </div>
  );
}

import { useState } from "react";
import { API_URL } from "./api";

interface EmergencySmsPayload {
  recipient: string;
  phone_mock: string;
  channel: string;
  status: string;
  message: string;
  dispatched_at: string;
}

interface AnalyzeChunkResponse {
  is_scam: boolean;
  confidence_score: number;
  detected_language: "gu" | "hi" | "en";
  red_flags: string[];
  warning_gujarati: string;
  warning_hindi: string;
  action: "HANG_UP_IMMEDIATELY" | "SAFE_TO_CONTINUE";
  request_id: string;
  scenario_id?: string | null;
  transcript_excerpt: string;
  emergency_dispatch?: EmergencySmsPayload | null;
  provider: string;
  model: string;
  simulated: boolean;
  latency_ms: number;
}

const SCENARIOS = [
  {
    id: "gujarati_digital_arrest",
    title: '1. Gujarati Scam ("Digital Arrest")',
    locale: "gu-IN",
    badge: "Scam · Gujarati (gu-IN)",
    transcript:
      "નમસ્તે, હું મુંબઈ પોલીસ સાયબર સેલમાંથી બોલું છું. તમારા આઈડી નંબર પર મોકલેલા પાર્સલમાં ડ્રગ્સ મળ્યા છે. તમે ડિજિટલ અરેસ્ટમાં છો, ફોન કાપશો નહીં. કેસ બંધ કરવા માટે અત્યારે જ ₹50,000 ટ્રાન્સફર કરો.",
  },
  {
    id: "hindi_digital_arrest",
    title: '2. Hindi Scam ("Digital Arrest")',
    locale: "hi-IN",
    badge: "Scam · Hindi (hi-IN)",
    transcript:
      "नमस्ते, मैं मुंबई पुलिस साइबर सेल से बोल रहा हूँ। आपके आईडी नंबर से भेजे गए पार्सल में ड्रग्स मिले हैं। आप डिजिटल अरेस्ट में हैं, कॉल मत काटिए। केस बंद करने के लिए अभी ₹50,000 ट्रांसफर कीजिए।",
  },
  {
    id: "english_bank_kyc",
    title: '3. English Scam ("Bank KYC")',
    locale: "en-IN",
    badge: "Scam · English (en-IN)",
    transcript:
      "Hello, this is your bank's KYC team. Your account will be blocked today because your KYC has expired. To keep it active, please share the OTP we just sent to your phone.",
  },
  {
    id: "genuine_delivery_control",
    title: "4. Genuine Control Call (Swiggy Delivery)",
    locale: "en-IN",
    badge: "Safe Control · English (en-IN)",
    transcript:
      "Hi, this is the delivery partner from Swiggy. I'm outside your building with your order. Which gate should I come to?",
  },
];

function createWavBlob(seedText: string): Blob {
  const sampleRate = 16000;
  const numSamples = 16000; // 1.0s PCM chunk
  const buffer = new ArrayBuffer(44 + numSamples * 2);
  const view = new DataView(buffer);
  const writeStr = (off: number, s: string) => {
    for (let i = 0; i < s.length; i++) view.setUint8(off + i, s.charCodeAt(i));
  };
  writeStr(0, "RIFF");
  view.setUint32(4, 36 + numSamples * 2, true);
  writeStr(8, "WAVE");
  writeStr(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeStr(36, "data");
  view.setUint32(40, numSamples * 2, true);

  let seed = 0;
  for (let i = 0; i < seedText.length; i++) seed = (seed * 31 + seedText.charCodeAt(i)) & 0xffff;
  for (let i = 0; i < numSamples; i++) {
    const t = i / sampleRate;
    const freq = 220 + ((seed + Math.floor(t * 4) * 53) % 280);
    view.setInt16(44 + i * 2, Math.sin(2 * Math.PI * freq * t) * 6000, true);
  }
  return new Blob([buffer], { type: "audio/wav" });
}

function speak(text: string, locale: string) {
  if (!("speechSynthesis" in window)) return;
  window.speechSynthesis.cancel();
  const utter = new SpeechSynthesisUtterance(text);
  utter.lang = locale;
  const voices = window.speechSynthesis.getVoices();
  const prefix = locale.split("-")[0].toLowerCase();
  const voice =
    voices.find((v) => v.lang.toLowerCase() === locale.toLowerCase()) ||
    voices.find((v) => v.lang.toLowerCase().startsWith(prefix));
  if (voice) utter.voice = voice;
  window.speechSynthesis.speak(utter);
}

export default function ScamShieldTab() {
  const [selectedId, setSelectedId] = useState<string>("gujarati_digital_arrest");
  const [engineMode, setEngineMode] = useState<"gemini" | "mock">("gemini");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<AnalyzeChunkResponse | null>(null);

  async function toggleMode(nextMode: "gemini" | "mock") {
    setEngineMode(nextMode);
    try {
      await fetch(`${API_URL}/api/mode`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: nextMode }),
      });
    } catch {
      // ignore if offline
    }
  }

  async function runScenario(sc: (typeof SCENARIOS)[number]) {
    setSelectedId(sc.id);
    setError(null);
    setLoading(true);
    speak(sc.transcript, sc.locale);

    try {
      const wavBlob = createWavBlob(sc.transcript);
      const fd = new FormData();
      fd.append("audio", wavBlob, `${sc.id}.wav`);
      fd.append("scenario_id", sc.id);
      fd.append("transcript", sc.transcript);
      fd.append("recipient", "Son (Rahul)");
      fd.append("mode", engineMode);

      const res = await fetch(`${API_URL}/api/analyze-chunk`, {
        method: "POST",
        body: fd,
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data?.error?.message || `HTTP ${res.status}`);
      }
      setResult(data as AnalyzeChunkResponse);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="panel">
      <div className="bar" style={{ justifyContent: "space-between" }}>
        <div>
          <strong>Scam Call Shield — Active India Anti-Fraud Guardian</strong>
          <div className="muted">
            Select a scenario below to synthesize caller audio &amp; send a 3–5s audio chunk to{" "}
            <code>POST /api/analyze-chunk</code> (or open{" "}
            <a href={`${API_URL}/scam-shield`} target="_blank" rel="noreferrer">
              standalone full-screen dashboard ↗
            </a>
            )
          </div>
        </div>
        <div className="controls" style={{ margin: 0 }}>
          <button
            className={engineMode === "gemini" ? "active" : ""}
            style={{
              background: engineMode === "gemini" ? "var(--ok)" : undefined,
              color: engineMode === "gemini" ? "#fff" : undefined,
            }}
            onClick={() => void toggleMode("gemini")}
          >
            ⚡ Real-Time Gemini
          </button>
          <button
            className={engineMode === "mock" ? "active" : ""}
            style={{
              background: engineMode === "mock" ? "var(--demo)" : undefined,
              color: engineMode === "mock" ? "#fff" : undefined,
            }}
            onClick={() => void toggleMode("mock")}
          >
            🧪 Demo Mode
          </button>
        </div>
      </div>

      <div className="controls">
        {SCENARIOS.map((sc) => (
          <button
            key={sc.id}
            onClick={() => void runScenario(sc)}
            disabled={loading}
            style={{
              borderColor: selectedId === sc.id ? "var(--accent)" : undefined,
              fontWeight: selectedId === sc.id ? 600 : 400,
            }}
          >
            {sc.title}
          </button>
        ))}
      </div>

      {error && <div className="error">{error}</div>}
      {loading && <div className="muted">Analyzing 3–5s audio chunk via Gemini...</div>}

      {result && (
        <div style={{ marginTop: 12 }}>
          <div
            className={result.is_scam ? "error" : "panel"}
            style={{
              borderColor: result.is_scam ? "var(--bad)" : "var(--ok)",
              padding: 12,
            }}
          >
            <div className="bar" style={{ justifyContent: "space-between", marginBottom: 4 }}>
              <strong>
                {result.is_scam ? "🚨 SCAM DETECTED — " : "✅ SAFE CALL — "}
                {result.action}
              </strong>
              <span className={`pill ${result.is_scam ? "warn" : "ok"}`}>
                Confidence: {Math.round(result.confidence_score * 100)}% · Lang:{" "}
                {result.detected_language.toUpperCase()}
              </span>
            </div>
            <div className="muted" style={{ marginBottom: 8 }}>
              Transcript: &ldquo;{result.transcript_excerpt}&rdquo;
            </div>

            {result.red_flags.length > 0 && (
              <div className="badges" style={{ marginBottom: 8 }}>
                {result.red_flags.map((rf) => (
                  <span key={rf} className="badge bad">
                    🚩 {rf}
                  </span>
                ))}
              </div>
            )}

            <div className="controls">
              <button onClick={() => speak(result.warning_gujarati, "gu-IN")}>
                🔊 Speak Gujarati Warning (gu-IN)
              </button>
              <button onClick={() => speak(result.warning_hindi, "hi-IN")}>
                🔊 Speak Hindi Warning (hi-IN)
              </button>
            </div>
            <dl>
              <dt>Gujarati (gu-IN)</dt>
              <dd>{result.warning_gujarati}</dd>
              <dt>Hindi (hi-IN)</dt>
              <dd>{result.warning_hindi}</dd>
            </dl>

            {result.emergency_dispatch && (
              <div className="approval" style={{ marginTop: 8 }}>
                <span className="badge bad">{result.emergency_dispatch.status}</span>
                <strong>To: {result.emergency_dispatch.recipient}</strong>
                <span>{result.emergency_dispatch.message}</span>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

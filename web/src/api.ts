export const API_URL = (import.meta.env.VITE_VOICE_API_URL ?? "http://localhost:8003").replace(/\/$/, "");

export interface ApiError {
  code: string;
  message: string;
  requestId?: string;
  details?: { path: string; message: string }[];
}

export class ApiException extends Error {
  constructor(public status: number, public error: ApiError) {
    super(`${error.code}: ${error.message}`);
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ApiException(0, { code: "NETWORK", message: `Cannot reach the voice API at ${API_URL}. Is it running on that port?` });
  }
  const text = await res.text();
  const body = text ? JSON.parse(text) : null;
  if (!res.ok) {
    throw new ApiException(res.status, body?.error ?? { code: "INTERNAL", message: `HTTP ${res.status}` });
  }
  return body as T;
}

export interface ToolCallInfo { name?: string; arguments?: Record<string, unknown>; result?: Record<string, unknown>; status?: string }
export interface Message { role: "user" | "assistant" | "tool"; text: string; at: string; toolCall?: ToolCallInfo | null }
export interface ToolTrace { tool: string; arguments: Record<string, unknown>; result: Record<string, unknown>; status: string; at: string; latencyMs?: number }
export interface Approval { id: string; tool: string; arguments: Record<string, unknown>; reason: string; status: string; expiresAt: string }
export interface Session {
  id: string; mode: string; status: string; transport: string;
  providers: { llm: string | null; stt: string | null; tts: string | null };
  messages: Message[]; toolTrace: ToolTrace[]; approvals: Approval[];
  simulated: boolean; error: string | null; createdAt: string; endedAt: string | null;
}
export interface MessageReply { sessionId: string; reply: Message; messages: Message[]; toolCalls: ToolTrace[]; simulated: boolean; provider: Record<string, string | null> }
export interface Ready {
  status: string; dataMode: string; simulated: boolean;
  checks: { database: { status: string; latencyMs?: number }; provider: { status: string; name?: string; model?: string; missing: string[] };
            stt?: { status: string; name?: string; missing: string[] }; tts?: { status: string; name?: string; missing: string[] } };
}
export interface Health { status: string; dataMode: "mock" | "postgres"; version: string }
export interface ToolContract { name: string; description: string; risk: string; requiresApproval: boolean; permissions: string[] }

export const api = {
  health: () => call<Health>("/health"),
  ready: async (): Promise<Ready> => {
    // /ready answers 503 with a full body when not ready; we want the body either way
    const res = await fetch(`${API_URL}/ready`);
    return (await res.json()) as Ready;
  },
  tools: () => call<ToolContract[]>("/api/v1/tools"),
  createSession: (mode: "text" | "browser") => call<Session>("/api/v1/sessions", { method: "POST", body: JSON.stringify({ mode }) }),
  getSession: (id: string) => call<Session>(`/api/v1/sessions/${id}`),
  send: (id: string, text: string) => call<MessageReply>(`/api/v1/sessions/${id}/messages`, { method: "POST", body: JSON.stringify({ text }) }),
  end: (id: string) => call<Session>(`/api/v1/sessions/${id}/end`, { method: "POST" }),
  decide: (sid: string, aid: string, decision: "approve" | "reject") =>
    call(`/api/v1/sessions/${sid}/approvals/${aid}`, { method: "POST", body: JSON.stringify({ decision }) }),
  offerUrl: (id: string) => `${API_URL}/api/v1/sessions/${id}/offer`,
};

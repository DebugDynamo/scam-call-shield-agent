CREATE TABLE IF NOT EXISTS sessions (
  id uuid PRIMARY KEY,
  mode text NOT NULL,
  status text NOT NULL,
  transport text NOT NULL,
  providers jsonb NOT NULL DEFAULT '{}'::jsonb,
  simulated boolean NOT NULL DEFAULT false,
  error text,
  agent jsonb NOT NULL DEFAULT '{}'::jsonb,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL,
  ended_at timestamptz,
  last_activity_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_status_activity ON sessions (status, last_activity_at);
CREATE INDEX IF NOT EXISTS idx_sessions_created ON sessions (created_at DESC);

CREATE TABLE IF NOT EXISTS messages (
  id uuid PRIMARY KEY,
  session_id uuid NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  seq bigserial,
  role text NOT NULL,
  text text NOT NULL,
  tool_call jsonb,
  at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages (session_id, seq);

CREATE TABLE IF NOT EXISTS tool_traces (
  id uuid PRIMARY KEY,
  session_id uuid NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  seq bigserial,
  tool text NOT NULL,
  arguments jsonb NOT NULL DEFAULT '{}'::jsonb,
  result jsonb NOT NULL DEFAULT '{}'::jsonb,
  status text NOT NULL,
  latency_ms integer NOT NULL DEFAULT 0,
  at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_traces_session ON tool_traces (session_id, seq);

CREATE TABLE IF NOT EXISTS tasks (
  id uuid PRIMARY KEY,
  title text NOT NULL,
  description text,
  status text NOT NULL DEFAULT 'todo',
  priority text NOT NULL DEFAULT 'medium',
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
  id uuid PRIMARY KEY,
  session_id uuid NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  tool text NOT NULL,
  arguments jsonb NOT NULL DEFAULT '{}'::jsonb,
  reason text NOT NULL DEFAULT '',
  status text NOT NULL,
  token_hash text,
  created_at timestamptz NOT NULL,
  expires_at timestamptz NOT NULL,
  resolved_at timestamptz
);
CREATE INDEX IF NOT EXISTS idx_approvals_session ON approvals (session_id);

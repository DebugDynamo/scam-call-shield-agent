"""Usage: python -m app.db {migrate|seed|reset|status}   (reset needs CONFIRM_RESET=yes)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg

from ..config import get_settings
from ..seed_data import seed_tasks

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"


def _connect() -> psycopg.Connection:
    s = get_settings()
    try:
        return psycopg.connect(s.dsn, connect_timeout=5, autocommit=False)
    except psycopg.OperationalError as e:
        print(f"ERROR: cannot connect to Postgres ({type(e).__name__}): check DATABASE_URL / DB_* settings.", file=sys.stderr)
        sys.exit(2)


def migrate() -> None:
    with _connect() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
        done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
        for f in sorted(MIGRATIONS.glob("*.sql")):
            if f.stem in done:
                print(f"skip   {f.name}")
                continue
            conn.execute(f.read_text())
            conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (f.stem,))
            print(f"apply  {f.name}")
        conn.commit()
    print("migrations up to date")


def seed() -> None:
    with _connect() as conn:
        inserted = 0
        for t in seed_tasks():
            cur = conn.execute(
                "INSERT INTO tasks (id,title,description,status,priority,created_at,updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING",
                (t.id, t.title, t.description, t.status, t.priority, t.created_at, t.updated_at),
            )
            inserted += cur.rowcount
        conn.commit()
    print(f"seed done: {inserted} inserted, {len(seed_tasks()) - inserted} already present")


def reset() -> None:
    if os.environ.get("CONFIRM_RESET") != "yes":
        print("Refusing to reset: set CONFIRM_RESET=yes to drop all voice tables.", file=sys.stderr)
        sys.exit(3)
    with _connect() as conn:
        conn.execute("DROP TABLE IF EXISTS approvals, tool_traces, messages, tasks, sessions, schema_migrations CASCADE")
        conn.commit()
    print("reset: all tables dropped (run migrate + seed)")


def status() -> None:
    with _connect() as conn:
        try:
            rows = [r[0] for r in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]
        except psycopg.errors.UndefinedTable:
            rows = []
    print("applied:", rows or "none")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    fns = {"migrate": migrate, "seed": seed, "reset": reset, "status": status}
    if cmd not in fns:
        print(__doc__, file=sys.stderr)
        sys.exit(1)
    fns[cmd]()


if __name__ == "__main__":
    main()

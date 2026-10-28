"""Versioned schema migrations, one step at a time in either direction (NFR-007).

Each migration is a pair ``NNNN_name.up.sql`` / ``NNNN_name.down.sql``; ``schema_migrations``
records what is applied. Every step runs in its own transaction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import resources

import psycopg

_NAME = re.compile(r"^(\d{4})_([a-z0-9_]+)\.(up|down)\.sql$")


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    up: str
    down: str


def available() -> list[Migration]:
    folder = resources.files("sdp.adapters.postgres") / "migrations"
    parts: dict[int, dict[str, str]] = {}
    for entry in folder.iterdir():
        match = _NAME.match(entry.name)
        if match:
            version = int(match.group(1))
            parts.setdefault(version, {"name": match.group(2)})[match.group(3)] = entry.read_text("utf-8")
    migrations = []
    for version, p in sorted(parts.items()):
        if "up" not in p or "down" not in p:
            raise RuntimeError(f"migration {version:04d} needs both an up and a down script")
        migrations.append(Migration(version, p["name"], p["up"], p["down"]))
    return migrations


def _ensure_table(conn: psycopg.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version integer PRIMARY KEY, name text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())"
    )


def current(conn: psycopg.Connection) -> int:
    _ensure_table(conn)
    row = conn.execute("SELECT coalesce(max(version), 0) FROM schema_migrations").fetchone()
    return int(row[0]) if row else 0


def upgrade(dsn: str, target: int | None = None) -> list[int]:
    """Apply pending migrations up to ``target`` (default: latest). Returns the versions applied."""
    applied = []
    with psycopg.connect(dsn, client_encoding="utf8") as conn:
        for m in available():
            if m.version <= current(conn) or (target is not None and m.version > target):
                continue
            with conn.transaction():
                conn.execute(m.up)
                conn.execute("INSERT INTO schema_migrations (version, name) VALUES (%s, %s)", (m.version, m.name))
            applied.append(m.version)
    return applied


def downgrade(dsn: str, steps: int = 1) -> list[int]:
    """Revert the latest ``steps`` migrations. Returns the versions reverted."""
    reverted = []
    by_version = {m.version: m for m in available()}
    with psycopg.connect(dsn, client_encoding="utf8") as conn:
        for _ in range(steps):
            version = current(conn)
            if version == 0:
                break
            with conn.transaction():
                conn.execute(by_version[version].down)
                conn.execute("DELETE FROM schema_migrations WHERE version = %s", (version,))
            reverted.append(version)
    return reverted

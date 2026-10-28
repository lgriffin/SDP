"""``CapabilityRegistry`` on PostgreSQL (Integration v0.4.0 §2, Stage C, migration 0003).

``put`` supersedes the current descriptor and inserts the new one in one transaction; the schema
refuses edits and deletes, so the descriptor a plan was matched against can always be read back.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import psycopg
from psycopg.types.json import Jsonb

from sdp.kernel.domain.integration import SourceCapabilityDescriptor


@dataclass(frozen=True)
class PostgresCapabilityRegistry:
    dsn: str

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self.dsn, autocommit=False, client_encoding="utf8")

    def put(self, descriptor: SourceCapabilityDescriptor) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE capability_descriptors SET superseded_at = now()"
                " WHERE source_id = %s AND superseded_at IS NULL",
                (descriptor.source_id,),
            )
            conn.execute(
                "INSERT INTO capability_descriptors (source_id, verified_on, document) VALUES (%s, %s, %s)",
                (descriptor.source_id, descriptor.verified_on, Jsonb(descriptor.to_document())),
            )
            conn.commit()

    def get(self, source_id: str) -> SourceCapabilityDescriptor | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT document FROM capability_descriptors WHERE source_id = %s AND superseded_at IS NULL",
                (source_id,),
            ).fetchone()
        return SourceCapabilityDescriptor.from_document(row[0]) if row else None

    def all(self) -> Sequence[SourceCapabilityDescriptor]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT document FROM capability_descriptors WHERE superseded_at IS NULL ORDER BY source_id"
            ).fetchall()
        return tuple(SourceCapabilityDescriptor.from_document(r[0]) for r in rows)

    def stale(self, older_than: date) -> Sequence[SourceCapabilityDescriptor]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT document FROM capability_descriptors WHERE superseded_at IS NULL AND verified_on < %s"
                " ORDER BY source_id",
                (older_than,),
            ).fetchall()
        return tuple(SourceCapabilityDescriptor.from_document(r[0]) for r in rows)

    def history(self, source_id: str) -> Sequence[SourceCapabilityDescriptor]:
        """Every descriptor ever registered for the source, oldest first."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT document FROM capability_descriptors WHERE source_id = %s ORDER BY descriptor_id",
                (source_id,),
            ).fetchall()
        return tuple(SourceCapabilityDescriptor.from_document(r[0]) for r in rows)

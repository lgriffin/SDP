"""FilesystemHistoricalSources: documents, page scans, transcriptions and verifications on the NAS.

    <root>/<document_id>/document.json        the HistoricalDocument
    <root>/<document_id>/content              the document file, if held
    <root>/<document_id>/pages/<n>            page scans
    <root>/<document_id>/transcriptions.jsonl append-only; a record id is written once
    <root>/<document_id>/verifications.jsonl  append-only second passes

Transcriptions are never edited in place: a correction is a verification record that does not
match, carrying the corrected fields (REL-001, REL-G-02).
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sdp.kernel.domain.historical import HistoricalDocument, TranscribedRecord, VerificationRecord
from sdp.kernel.domain.sources import SourceRef

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _iso(at: datetime) -> str:
    return at.isoformat().replace("+00:00", "Z")


def _parse(at: str) -> datetime:
    return datetime.fromisoformat(at.replace("Z", "+00:00"))


def _append(path: Path, doc: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(doc, sort_keys=True, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _lines(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]


@dataclass
class FilesystemHistoricalSources:
    root: Path

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _dir(self, document_id: str) -> Path:
        if not _SAFE_ID.match(document_id):
            raise ValueError(f"unsafe document id {document_id!r}")
        return self.root / document_id

    def register(self, document: HistoricalDocument, content: bytes | None = None) -> None:
        folder = self._dir(document.document_id)
        (folder / "pages").mkdir(parents=True, exist_ok=True)
        doc = {
            "document_id": document.document_id,
            "source": document.source.to_document(),
            "title": document.title,
            "pages": document.pages,
        }
        (folder / "document.json").write_text(json.dumps(doc, sort_keys=True, indent=2) + "\n", "utf-8")
        if content is not None:
            (folder / "content").write_bytes(content)

    def add_page(self, document_id: str, page: int, content: bytes) -> None:
        (self._dir(document_id) / "pages" / str(int(page))).write_bytes(content)

    def document(self, document_id: str) -> HistoricalDocument | None:
        path = self._dir(document_id) / "document.json"
        if not path.exists():
            return None
        doc = json.loads(path.read_text("utf-8"))
        return HistoricalDocument(
            doc["document_id"], SourceRef.from_document(doc["source"]), doc["title"], doc.get("pages")
        )

    def page(self, document_id: str, page: int) -> bytes | None:
        path = self._dir(document_id) / "pages" / str(int(page))
        return path.read_bytes() if path.exists() else None

    def _documents(self) -> list[str]:
        return sorted(p.name for p in self.root.iterdir() if (p / "document.json").exists())

    def add_transcription(self, record: TranscribedRecord) -> None:
        if self.document(record.document_id) is None:
            raise KeyError(f"unknown document {record.document_id}")
        if any(r.record_id == record.record_id for r in self.transcriptions()):
            raise ValueError(f"record {record.record_id} already transcribed; corrections go through verification")
        _append(
            self._dir(record.document_id) / "transcriptions.jsonl",
            {
                "record_id": record.record_id,
                "document_id": record.document_id,
                "locator": record.locator,
                "fields": dict(record.fields),
                "transcriber": record.transcriber,
                "transcribed_at": _iso(record.transcribed_at),
                "notes": record.notes,
            },
        )

    def add_verification(self, record: VerificationRecord) -> None:
        owner = next((r for r in self.transcriptions() if r.record_id == record.record_id), None)
        if owner is None:
            raise KeyError(f"cannot verify unknown record {record.record_id}")
        _append(
            self._dir(owner.document_id) / "verifications.jsonl",
            {
                "record_id": record.record_id,
                "verifier": record.verifier,
                "verified_at": _iso(record.verified_at),
                "matches": record.matches,
                "corrections": dict(record.corrections),
                "notes": record.notes,
            },
        )

    def transcriptions(self, document_id: str | None = None) -> Sequence[TranscribedRecord]:
        ids = [document_id] if document_id else self._documents()
        return [
            TranscribedRecord(
                d["record_id"],
                d["document_id"],
                d["locator"],
                d["fields"],
                d["transcriber"],
                _parse(d["transcribed_at"]),
                d.get("notes", ""),
            )
            for doc_id in ids
            for d in _lines(self._dir(doc_id) / "transcriptions.jsonl")
        ]

    def verifications(self, record_id: str | None = None) -> Sequence[VerificationRecord]:
        return [
            VerificationRecord(
                d["record_id"],
                d["verifier"],
                _parse(d["verified_at"]),
                d["matches"],
                d.get("corrections", {}),
                d.get("notes", ""),
            )
            for doc_id in self._documents()
            for d in _lines(self._dir(doc_id) / "verifications.jsonl")
            if record_id is None or d["record_id"] == record_id
        ]

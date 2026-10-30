"""Fingerprints of a local source's frames, to show the originals are still intact (#71).

The first SHA-256 seen for each frame is its fingerprint, kept in ``<data>/index/<source>/fingerprints.parquet``:
from ``sdp local solve``, which hashes the bytes it reads anyway, or from the first ``sdp local verify``. A
verification hashes the files again and reports what is missing, new, modified (its size or date changed too)
or corrupted (same size and date, other bytes). A fingerprint is replaced only when asked (``--accept``).
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from sdp.adapters.local.index import walk_frames
from sdp.adapters.local.solving import REFUSED, SOLVED
from sdp.adapters.local.sources import LocalSource, LocalSourceError

SCHEMA = pa.schema(
    [
        ("path", pa.string()),
        ("size", pa.int64()),
        ("mtime_ns", pa.int64()),
        ("sha256", pa.string()),
        ("since", pa.string()),  # when this fingerprint was first taken
        ("by", pa.string()),  # solve or verify
    ]
)
#: how many paths of each problem a report lists in full; the whole list is in the report file
LISTED = 50
_BLOCK = 8 << 20


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(_BLOCK):
            digest.update(block)
    return digest.hexdigest()


@dataclass
class Report:
    checked: int = 0
    hashed: int = 0
    ok: list[str] = field(default_factory=list)
    new: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    corrupted: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    accepted: list[str] = field(default_factory=list)

    @property
    def intact(self) -> bool:
        return not (self.modified or self.corrupted or self.missing)

    def to_document(self, listed: int | None = LISTED) -> dict[str, Any]:
        doc: dict[str, Any] = {"checked": self.checked, "hashed": self.hashed, "intact": self.intact}
        for name in ("ok", "new", "modified", "corrupted", "missing", "accepted"):
            paths = getattr(self, name)
            doc[name] = len(paths)
            if name != "ok" and paths:
                doc[f"{name}_paths"] = paths if listed is None else paths[:listed]
        return doc


@dataclass(frozen=True)
class Fingerprints:
    source: LocalSource
    data: Path

    @property
    def path(self) -> Path:
        return self.source.output_dir(self.data, "index") / "fingerprints.parquet"

    def known(self) -> dict[str, dict[str, Any]]:
        """The fingerprints file's, then (for frames not in it) those the solver recorded."""
        found: dict[str, dict[str, Any]] = {}
        solutions = self.source.output_dir(self.data, "solutions")
        if solutions.is_dir():
            for result in sorted(solutions.rglob("*.json")):
                if not result.name.endswith((SOLVED, REFUSED)):
                    continue
                doc = json.loads(result.read_text("utf-8"))
                # the first fingerprint, carried over when the frame was solved again
                file = doc.get("first_file") or doc.get("file") or {}
                if file.get("sha256") and doc.get("frame"):
                    found[doc["frame"]] = {
                        "path": doc["frame"],
                        "size": file["size"],
                        "mtime_ns": file["mtime_ns"],
                        "sha256": file["sha256"],
                        "since": file.get("since") or doc.get("written_at", ""),
                        "by": "solve",
                    }
        if self.path.exists():
            found |= {r["path"]: r for r in pq.read_table(self.path).to_pylist()}
        return found

    def _save(self, rows: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        table = pa.Table.from_pylist([rows[p] for p in sorted(rows)], schema=SCHEMA)
        pq.write_table(table, tmp, compression="zstd")
        tmp.replace(self.path)

    def verify(
        self,
        *,
        quick: bool = False,
        accept: bool = False,
        progress: Callable[[int, int], None] | None = None,
    ) -> Report:
        """Every frame against its fingerprint. ``quick`` compares sizes and dates only and hashes only frames
        with no fingerprint yet; ``accept`` takes a modified or corrupted frame's bytes as its new fingerprint."""
        if not self.source.path.is_dir():
            raise LocalSourceError(f"{self.source.path} is not there (is the drive connected?)")
        known = self.known()
        rows = dict(known)
        report = Report()
        now = datetime.now(UTC).isoformat(timespec="seconds")
        seen: set[str] = set()
        for count, (path, relative) in enumerate(walk_frames(self.source.path), start=1):
            key = str(relative)
            seen.add(key)
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            report.checked += 1
            before = known.get(key)
            same_stat = before is not None and (before["size"], before["mtime_ns"]) == (stat.st_size, stat.st_mtime_ns)
            # quick compares sizes and dates; a changed frame is hashed when it is to be accepted
            if before is not None and quick and (same_stat or not accept):
                (report.ok if same_stat else report.modified).append(key)
            else:
                digest = sha256_of(path)
                report.hashed += 1
                row = {"path": key, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "sha256": digest}
                if before is None:
                    report.new.append(key)
                    rows[key] = {**row, "since": now, "by": "verify"}
                elif digest == before["sha256"]:
                    report.ok.append(key)
                    if not same_stat:  # copied or touched, same bytes: later quick checks use the new date
                        rows[key] = {**before, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
                else:
                    (report.corrupted if same_stat else report.modified).append(key)
                    if accept:
                        report.accepted.append(key)
                        rows[key] = {**row, "since": now, "by": "verify"}
            if progress is not None and count % 500 == 0:
                progress(count, report.hashed)
        report.missing = sorted(set(known) - seen)
        self._save(rows)
        return report

    def manifest(self, out: Path) -> int:
        """A CSV of path, size and SHA-256 of every fingerprinted frame, for a backup tool to check against.
        Run ``verify`` first so that every frame has one."""
        rows = self.known()
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["path", "size_bytes", "sha256", "since"])
            for path in sorted(rows):
                writer.writerow([path, rows[path]["size"], rows[path]["sha256"], rows[path]["since"]])
        return len(rows)

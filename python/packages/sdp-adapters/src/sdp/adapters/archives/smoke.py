"""The weekly live smoke (T11, DSI-008): one schema query and one small cone per source, recorded.

For each source it asks TAP_SCHEMA for the table's columns live, compares them with the committed
recording of the same request (``<baseline>/<source>/``) and with the declared columns, and records
the new answers under ``<record>/<source>/``. Any drift fails the job, naming the column.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from sdp.adapters.archives.drift import columns_from, schema_drift, tap_schema_request
from sdp.adapters.archives.tap import TapArchiveAdapter, TapSource
from sdp.adapters.archives.transport import CassetteTransport, Transport, TransportError, read_cassette
from sdp.kernel.domain.archive import CatalogueQuery, Cone


@dataclass(frozen=True)
class SmokeResult:
    source_id: str
    problems: tuple[str, ...]
    rows: int | None

    def to_document(self) -> dict[str, object]:
        return {"source_id": self.source_id, "problems": list(self.problems), "rows": self.rows}


#: A small, dense, uncontroversial field: the Pleiades core.
SMOKE_CONE = Cone(56.75, 24.12, 0.05)


def smoke_query(source: TapSource) -> CatalogueQuery:
    rows = min(50, source.descriptor.max_rows_per_call)
    return CatalogueQuery(
        source.descriptor.source_id,
        source.descriptor.release_label,
        SMOKE_CONE,
        source.descriptor.column_names(),
        row_cap=rows,
        chunk_rows=rows,
    )


def run_smoke(
    sources: Mapping[str, TapSource], live: Callable[[TapSource], Transport], baseline: Path, record: Path
) -> list[SmokeResult]:
    results: list[SmokeResult] = []
    for sid, source in sorted(sources.items()):
        recorder = CassetteTransport(record / sid, "record", inner=live(source), recorder="weekly-live-smoke")
        request = tap_schema_request(source)
        try:
            response = recorder.send(request)
        except TransportError as exc:
            # one unreachable archive is reported; the others are still asked and recorded
            results.append(SmokeResult(sid, (f"{sid}: TAP_SCHEMA query failed: {exc}",), None))
            continue
        if response.status != 200:
            results.append(SmokeResult(sid, (f"{sid}: TAP_SCHEMA query failed with HTTP {response.status}",), None))
            continue
        previous_path = baseline / sid / f"{request.key()}.json"
        previous = columns_from(read_cassette(previous_path).body) if previous_path.exists() else None
        problems = schema_drift(source, previous, columns_from(response.body))
        rows: int | None = None
        try:
            adapter = TapArchiveAdapter({sid: source}, recorder)
            rows = sum(b.num_rows for b in adapter.fetch(adapter.query(smoke_query(source))))
        except Exception as exc:
            problems = [*problems, f"{sid}: smoke cone failed: {exc}"]
        results.append(SmokeResult(sid, tuple(problems), rows))
    return results

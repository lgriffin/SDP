"""CuratedCatalogue: DuckDB over curated Parquet artifacts (ADR-011).

Artifacts are read through the ArtifactStore, so every read is SHA-256 verified (NFR-003), and
registered as DuckDB views by name. The connection is in-memory and the views are read-only: DuckDB
is for analysis, PostgreSQL stays the system of record. ``cone`` is the common selection, done
with a haversine distance so it needs no extension.
"""

from __future__ import annotations

import io
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from sdp.kernel.domain.ids import ArtifactId
from sdp.kernel.ports.storage import ArtifactStore

_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass
class CuratedCatalogue:
    store: ArtifactStore
    _con: duckdb.DuckDBPyConnection = field(init=False, repr=False)
    _views: dict[str, tuple[str, ...]] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self._con = duckdb.connect(":memory:")

    def register(self, name: str, artifact_ids: Sequence[ArtifactId]) -> int:
        """One view over the union of the given curated artifacts; returns its row count."""
        if not _NAME.match(name):
            raise ValueError(f"not a view name: {name!r}")
        if not artifact_ids:
            raise ValueError("a view needs at least one artifact")
        tables = [pq.read_table(io.BytesIO(self.store.get(a))) for a in artifact_ids]
        table = pa.concat_tables([t.replace_schema_metadata(None) for t in tables], promote_options="default")
        self._con.register(name, table)
        self._views[name] = tuple(a.value for a in artifact_ids)
        return int(table.num_rows)

    def views(self) -> dict[str, tuple[str, ...]]:
        return dict(self._views)

    def query(self, sql: str, parameters: Sequence[object] = ()) -> pa.Table:
        return self._con.sql(sql, params=list(parameters) or None).to_arrow_table()

    def cone(self, view: str, ra_deg: float, dec_deg: float, radius_deg: float) -> pa.Table:
        if view not in self._views:
            raise KeyError(f"no view {view!r}")
        sql = f"""
            SELECT * FROM {view}
            WHERE 2 * asin(sqrt(
                pow(sin(radians(dec_deg - ?) / 2), 2)
                + cos(radians(dec_deg)) * cos(radians(?)) * pow(sin(radians(ra_deg - ?) / 2), 2)
            )) <= radians(?)
            ORDER BY source_key
        """
        return self.query(sql, (dec_deg, dec_deg, ra_deg, radius_deg))

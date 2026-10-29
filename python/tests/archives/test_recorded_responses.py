"""T5 on real archive answers: the cones the live smoke recorded replay through the TAP adapter
and curate through the packaged dialects. Recordings come only from the live
smoke workflow; a source without a recorded cone is skipped, never faked. ``SDP_CASSETTES`` points
the replay at another recordings directory (tools/record_live_data.py checks a fresh run that way)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pyarrow as pa
import pytest

from sdp.adapters.archives.dialect import load_dialects
from sdp.adapters.archives.smoke import smoke_query
from sdp.adapters.archives.tap import TapArchiveAdapter, load_sources
from sdp.adapters.archives.transport import CassetteMissing, CassetteTransport

pytestmark = pytest.mark.tier("T5")

CASSETTES = Path(os.environ.get("SDP_CASSETTES") or Path(__file__).resolve().parents[1] / "cassettes")
SOURCES = load_sources()


def _replay(source_id: str) -> pa.Table:
    source = SOURCES[source_id]
    adapter = TapArchiveAdapter({source_id: source}, CassetteTransport(CASSETTES / source_id, "replay"))
    try:
        return pa.Table.from_batches(list(adapter.fetch(adapter.query(smoke_query(source)))))
    except CassetteMissing:
        pytest.skip(f"no recorded {source_id} cone yet; run the Archive live smoke workflow")


@pytest.mark.parametrize("source_id", sorted(SOURCES))
def test_a_recorded_cone_replays_with_every_declared_column(source_id: str) -> None:
    table = _replay(source_id)
    assert table.num_rows > 0
    assert set(table.column_names) == {c.name for c in SOURCES[source_id].descriptor.columns}


@pytest.mark.parametrize("source_id", sorted(SOURCES))
def test_a_recorded_cone_curates_to_canonical_rows(source_id: str) -> None:
    table = _replay(source_id)
    dialect = load_dialects()[SOURCES[source_id].descriptor.dialect_id]
    curated = dialect.curate(table)
    excluded = json.loads((curated.schema.metadata or {}).get(b"sdp.excluded_rows", b'{"count": 0}'))
    assert curated.num_rows + excluded["count"] == table.num_rows
    assert curated.num_rows > 0
    # Every recorded cone is around the Pleiades smoke position, and every epoch is a modern one.
    assert all(abs(ra - 56.75) < 0.1 for ra in curated.column("ra_deg").to_pylist())
    assert all(1990.0 < e < 2030.0 for e in curated.column("epoch_jyear").to_pylist())

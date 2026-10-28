"""CDS X-Match request and response handling, schema drift, and DuckDB over curated Parquet."""

from __future__ import annotations

import io

import astropy.units as u
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from sdp.adapters.analytics.duckdb import CuratedCatalogue
from sdp.adapters.archives.dialect import load_dialects, load_golden
from sdp.adapters.archives.drift import columns_from, schema_drift, tap_schema_request
from sdp.adapters.archives.votable import to_votable
from sdp.adapters.archives.xmatch import CdsXMatchAdapter
from sdp.adapters.storage.nas import NasArtifactStore
from sdp.testing.contracts.cross_match import CrossMatchContract, frame
from sdp.testing.fakes.archives import AstropyCrossMatch

from .conftest import GAIA, FakeXMatchService


@pytest.mark.tier("T3")
class TestCdsXMatchAdapterAgainstAFakeService(CrossMatchContract):
    """Request formation and response parsing. Agreement with the live service (DSI-009) needs a
    recorded cassette; until then DSI-009 stays draft."""

    @pytest.fixture
    def cross_match(self) -> CdsXMatchAdapter:
        return CdsXMatchAdapter(FakeXMatchService())


@pytest.mark.tier("T3")
def test_the_nearest_pair_is_kept_as_the_reference_does() -> None:
    left = frame([10.0, 20.0], [0.0, 10.0])
    right = frame([10.0 + 0.4 / 3600, 10.0 + 0.2 / 3600, 20.0], [0.0, 0.0, 10.0 + 0.9 / 3600])
    ours = CdsXMatchAdapter(FakeXMatchService()).match(left, right, 1 * u.arcsec)
    ref = AstropyCrossMatch().match(left, right, 1 * u.arcsec)
    assert list(ours.left_index) == list(ref.left_index) == [0, 1]
    assert list(ours.right_index) == list(ref.right_index) == [1, 2]
    assert ours.separation.to_value(u.arcsec) == pytest.approx(ref.separation.to_value(u.arcsec), abs=1e-5)


@pytest.mark.tier("T3")
def test_the_same_inputs_make_the_same_request() -> None:
    adapter = CdsXMatchAdapter(FakeXMatchService())
    left, right = frame([1.0], [1.0]), frame([1.0], [1.0])
    assert (
        adapter.request(left, right, 1 * u.arcsec, None).key() == adapter.request(left, right, 1 * u.arcsec, None).key()
    )


def schema_votable(columns: dict[str, str]) -> bytes:
    return to_votable(pa.table({"column_name": list(columns), "datatype": list(columns.values())}))


LIVE = {c.name: c.datatype for c in GAIA.descriptor.columns} | {"phot_variable_flag": "char"}


@pytest.mark.tier("T11")
@pytest.mark.req("DSI-008")
def test_drift_names_every_added_removed_and_retyped_column() -> None:
    current = dict(LIVE)
    current["ruwe_v2"] = current.pop("ruwe")
    current["parallax"] = "float"
    current["new_flag"] = "short"
    problems = schema_drift(GAIA, LIVE, current)
    assert problems == [
        "gaia: column new_flag added (short)",
        "gaia: column parallax retyped from double to float",
        "gaia: column ruwe removed or renamed",
        "gaia: column ruwe_v2 added (float)",
        "gaia: declared column ruwe is not in the archive's table",
    ]


@pytest.mark.tier("T11")
@pytest.mark.req("DSI-008")
def test_no_drift_is_silent_and_the_schema_query_parses() -> None:
    assert columns_from(schema_votable(LIVE)) == LIVE
    assert schema_drift(GAIA, LIVE, LIVE) == []
    assert b"TAP_SCHEMA.columns" in tap_schema_request(GAIA).body.replace(b"%2E", b".")


@pytest.mark.tier("T5")
def test_duckdb_reads_curated_parquet_through_the_verified_store(tmp_path: pytest.TempPathFactory) -> None:
    store = NasArtifactStore(tmp_path / "nas")  # type: ignore[operator]
    curated = load_dialects()["gaia-dr3"].curate(pa.Table.from_pylist(load_golden("gaia-dr3")["native"]))
    sink = io.BytesIO()
    pq.write_table(curated, sink)
    artifact = store.put(sink.getvalue(), "application/vnd.apache.parquet")
    catalogue = CuratedCatalogue(store)
    assert catalogue.register("gaia", [artifact]) == 3
    near = catalogue.cone("gaia", 45.5, -12.25, 0.01)
    assert near.column("source_key").to_pylist() == ["gaia:DR3:1000000000000001", "gaia:DR3:1000000000000003"]
    wide = catalogue.cone("gaia", 45.6, -12.4, 0.5)
    assert wide.num_rows == 3
    counts = catalogue.query("SELECT quality_class, count(*) AS n FROM gaia GROUP BY 1 ORDER BY 1")
    assert counts.to_pylist() == [
        {"quality_class": "good", "n": 1},
        {"quality_class": "marginal", "n": 1},
        {"quality_class": "unknown", "n": 1},
    ]
    with pytest.raises(ValueError):
        catalogue.register("bad name", [artifact])

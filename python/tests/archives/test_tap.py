"""T3/T5: the TAP gateway passes the ArchiveGateway contract live-shaped and from cassettes (DSI-001, DSI-007)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from sdp.adapters.archives import adql, votable
from sdp.adapters.archives.tap import TapArchiveAdapter, load_sources
from sdp.adapters.archives.transport import CassetteMissing, CassetteTransport, HttpRequest, HttpResponse
from sdp.kernel.domain.archive import Box, Filter
from sdp.testing.contracts.archive_gateway import ArchiveGatewayContract, GatewayCase
from sdp.testing.fakes.archives import apply_query

from .conftest import GAIA, FixtureTap, gaia_native, gaia_query

pytestmark = pytest.mark.tier("T3")


def primed(transport: Any, adapter: TapArchiveAdapter) -> None:
    native = gaia_native()
    for q in (gaia_query(), gaia_query(row_cap=201)):
        transport.prime(adapter, adapter.query(q), native)


class TestTapArchiveAdapter(ArchiveGatewayContract):
    @pytest.fixture
    def archive_case(self) -> GatewayCase:
        tap = FixtureTap()
        adapter = TapArchiveAdapter({"gaia": GAIA}, tap)
        primed(tap, adapter)
        expected = apply_query(gaia_native(), gaia_query()).num_rows
        return GatewayCase(adapter, gaia_query(), expected_rows=expected)


@pytest.mark.tier("T5")
@pytest.mark.req("DSI-007")
class TestTapArchiveAdapterFromCassettes(ArchiveGatewayContract):
    """The same contract replayed from cassettes the record mode wrote: CI's path."""

    @pytest.fixture
    def archive_case(self, tmp_path: Path) -> GatewayCase:
        tap = FixtureTap()
        recorder = CassetteTransport(tmp_path / "gaia", "record", inner=tap)
        adapter = TapArchiveAdapter({"gaia": GAIA}, recorder)
        primed(tap, adapter)
        for q in (gaia_query(), gaia_query(row_cap=201)):
            handle = adapter.query(q)
            list(adapter.fetch(handle))
        replay = TapArchiveAdapter({"gaia": GAIA}, CassetteTransport(tmp_path / "gaia", "replay"))
        return GatewayCase(replay, gaia_query(), expected_rows=apply_query(gaia_native(), gaia_query()).num_rows)


@pytest.mark.req("DSI-007")
def test_replay_never_falls_back_to_the_network(tmp_path: Path) -> None:
    adapter = TapArchiveAdapter({"gaia": GAIA}, CassetteTransport(tmp_path, "replay"))
    with pytest.raises(CassetteMissing, match="CI never calls a live archive"):
        list(adapter.fetch(adapter.query(gaia_query())))


def test_the_rendered_query_is_bounded_ordered_adql() -> None:
    q = gaia_query(
        columns=("source_id", "ra", "dec"), filters=(Filter("ruwe", "<", 1.4), Filter("parallax", "is_not_null"))
    )
    text = TapArchiveAdapter({"gaia": GAIA}, FixtureTap()).query(q).rendered_text
    assert text == (
        "SELECT TOP 200 source_id, ra, dec FROM gaiadr3.gaia_source WHERE "
        "1 = CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', 45.0, -12.0, 0.25)) "
        "AND ruwe < 1.4 AND parallax IS NOT NULL ORDER BY source_id"
    )


def test_chunks_page_with_offset_and_the_archives_row_limit() -> None:
    adapter = TapArchiveAdapter({"gaia": GAIA}, FixtureTap())
    handle = adapter.query(gaia_query(columns=("source_id",)))
    body = adapter.chunk_request(handle, 3).body.decode()
    assert "MAXREC=25" in body and "OFFSET+75" in body and "TOP+25" in body


def test_box_regions_and_literals_render_safely() -> None:
    assert (
        adql.region_clause(Box(10.0, 11.0, -1.0, 1.0), "ra", "dec")
        == "ra BETWEEN 10.0 AND 11.0 AND dec BETWEEN -1.0 AND 1.0"
    )
    assert adql.literal("O'Brien") == "'O''Brien'"
    assert adql.filter_clause(Filter("phot_variable_flag", "!=", "VARIABLE")) == "phot_variable_flag <> 'VARIABLE'"


@pytest.mark.req("DSI-001")
@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"release_label": "gaia:DR4"}, "serves gaia:DR3"),
        ({"columns": ("source_id", "secret")}, "not in the declared column list"),
        ({"chunk_rows": 60000, "row_cap": 60000}, "exceeds the archive"),
    ],
)
def test_queries_outside_the_declaration_are_refused(change: dict[str, Any], message: str) -> None:
    adapter = TapArchiveAdapter({"gaia": GAIA}, FixtureTap())
    with pytest.raises(ValueError, match=message):
        adapter.query(gaia_query(**change))


def test_a_source_that_cannot_page_needs_one_chunk() -> None:
    twomass = load_sources()["2mass"]
    adapter = TapArchiveAdapter({"2mass": twomass}, FixtureTap())
    q = replace(gaia_query(), source_id="2mass", release_label="2mass:psc", columns=("designation", "ra", "dec"))
    with pytest.raises(ValueError, match="cannot page"):
        adapter.query(q)
    adapter.query(replace(q, chunk_rows=q.row_cap))


@pytest.mark.req("DSI-001")
def test_every_packaged_source_declares_licence_citation_limits_and_a_dialect() -> None:
    from sdp.adapters.archives.dialect import load_dialects

    dialects = {d.source_release: d for d in load_dialects().values()}
    for source in load_sources().values():
        d = source.descriptor
        assert d.licence and d.citation and d.max_rows_per_call > 0 and d.min_seconds_between_calls > 0
        assert d.release_label in dialects and dialects[d.release_label].ref.dialect_id == d.dialect_id
        assert dialects[d.release_label].native_columns() <= set(d.column_names()), d.source_id
        assert source.service_url.startswith("https://")


class _FoldingTap:
    """A service that reports column names in lower case, as MAST does."""

    def __init__(self, inner: FixtureTap) -> None:
        self.inner = inner

    def send(self, request: HttpRequest) -> HttpResponse:
        response = self.inner.send(request)
        table = votable.parse(response.body)
        folded = table.rename_columns([n.lower() for n in table.column_names])
        return HttpResponse(response.status, votable.to_votable(folded), response.headers)


def test_column_names_folded_by_the_service_come_back_as_declared() -> None:
    tap = FixtureTap()
    adapter = TapArchiveAdapter({"gaia": GAIA}, tap)
    primed(tap, adapter)
    folding = TapArchiveAdapter({"gaia": GAIA}, _FoldingTap(tap))
    batches = list(folding.fetch(folding.query(gaia_query())))
    assert batches and batches[0].schema.names == list(gaia_query().columns)

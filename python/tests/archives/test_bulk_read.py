"""T3: runs read Gaia fields from a complete bulk copy, with the rows the archive would return (#72)."""

from __future__ import annotations

import math
from pathlib import Path

import pyarrow as pa
import pytest

from sdp.adapters.archives.bulk import BulkCatalogueGateway, BulkSweep
from sdp.adapters.archives.tap import TapArchiveAdapter, load_sources
from sdp.adapters.bootstrap import archive_gateways
from sdp.adapters.settings import Settings, SettingsError
from sdp.adapters.storage.nas import NasArtifactStore
from sdp.kernel.application.acquisition import CHECKSUM_METADATA_KEY
from sdp.kernel.domain.archive import Box, CatalogueQuery, Cone, Filter

from .test_bulk import COLUMNS, GAIA, ecsv, publish, star

pytestmark = pytest.mark.tier("T3")

#: stars either side of RA 0/360 and around a field at (120, 30), split over three files
POSITIONS = {
    "GaiaSource_000000-000001.csv.gz": [(359.9, 0.0), (359.95, 0.05), (0.02, -0.03), (0.1, 0.1), (1.5, 0.0)],
    "GaiaSource_000002-000003.csv.gz": [(120.0, 30.0), (120.3, 30.1), (119.6, 29.8), (121.2, 30.0), (120.0, 31.5)],
    "GaiaSource_000004-000005.csv.gz": [(250.0, -60.0), (250.5, -60.2)],
}


def stars() -> dict[str, list[dict[str, object]]]:
    out: dict[str, list[dict[str, object]]] = {}
    i = 0
    for name, positions in POSITIONS.items():
        out[name] = []
        for ra, dec in positions:
            out[name].append(star(i, ra=ra, dec=dec, parallax=float(i), phot_g_mean_mag=10.0 + i % 3))
            i += 1
    return out


def every_star() -> list[dict[str, object]]:
    return [s for rows in stars().values() for s in rows]


def separation_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    r1, d1, r2, d2 = (math.radians(v) for v in (ra1, dec1, ra2, dec2))
    c = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(r1 - r2)
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


@pytest.fixture
def store(tmp_path: Path) -> NasArtifactStore:
    files = {name: ecsv(rows) for name, rows in stars().items()}
    sweep = BulkSweep(
        store=NasArtifactStore(tmp_path / "store"),
        descriptor=GAIA,
        work_dir=tmp_path / "downloads",
        columns=COLUMNS,
        base_url=publish(tmp_path / "esa", files),
        sleep=lambda _s: None,
    )
    sweep.run(sweep.files())
    return sweep.store  # type: ignore[return-value]


def gateway(store: NasArtifactStore) -> BulkCatalogueGateway:
    return BulkCatalogueGateway(store, GAIA)


def query(region: Cone | Box, *filters: Filter, row_cap: int = 1000, chunk_rows: int = 1000) -> CatalogueQuery:
    return CatalogueQuery(
        "gaia", GAIA.release_label, region, ("source_id", "ra", "dec", "parallax"), filters, row_cap, chunk_rows
    )


def ids(g: BulkCatalogueGateway, q: CatalogueQuery, part: int = 0) -> list[int]:
    handle = g.query(q)
    if part:
        handle = type(handle)(handle.query, handle.rendered_text, handle.idempotency_key, handle.descriptor, part)
    batches = list(g.fetch(handle))
    assert all(CHECKSUM_METADATA_KEY in (b.schema.metadata or {}) for b in batches)
    return [int(v) for b in batches for v in b.column("source_id").to_pylist()]


@pytest.mark.req("DSI-014")
@pytest.mark.parametrize(
    "region",
    [Cone(120.0, 30.0, 0.5), Cone(0.0, 0.0, 0.2), Cone(359.99, 0.0, 2.0), Cone(250.2, -60.1, 0.4)],
)
def test_a_cone_returns_exactly_the_stars_inside_it_ordered_by_source_id(store: NasArtifactStore, region: Cone) -> None:
    expected = sorted(
        int(s["source_id"])  # type: ignore[call-overload]
        for s in every_star()
        if separation_deg(s["ra"], s["dec"], region.ra_deg, region.dec_deg) <= region.radius_deg  # type: ignore[arg-type]
    )
    assert expected  # every field holds stars
    assert ids(gateway(store), query(region)) == expected


@pytest.mark.req("DSI-014")
def test_a_box_is_plain_betweens_and_the_filters_apply_as_in_adql(store: NasArtifactStore) -> None:
    g = gateway(store)
    assert ids(g, query(Box(359.8, 360.0, -1.0, 1.0))) == [1000, 1001]
    # ADQL's box does not wrap: a negative RA bound reaches no star below 360
    assert ids(g, query(Box(-0.2, 0.5, -1.0, 1.0))) == [1002, 1003]
    near_zero = Box(0.0, 2.0, -1.0, 1.0)
    # parallax = i, so parallax >= 3 keeps the last two of the box
    assert ids(g, query(near_zero, Filter("parallax", ">=", 3.0))) == [1003, 1004]
    assert ids(g, query(near_zero, Filter("parallax", "!=", 3.0), Filter("phot_g_mean_mag", "is_not_null"))) == [
        1002,
        1004,
    ]


@pytest.mark.req("DSI-014")
def test_the_row_cap_and_chunks_page_as_the_archive_does(store: NasArtifactStore) -> None:
    g = gateway(store)
    field = Cone(120.0, 30.0, 2.0)
    assert ids(g, query(field)) == [1005, 1006, 1007, 1008, 1009]
    batches = list(g.fetch(g.query(query(field, row_cap=4, chunk_rows=2))))
    assert [b.num_rows for b in batches] == [2, 2]
    assert ids(g, query(field, row_cap=4, chunk_rows=2), part=1) == [1007, 1008]


@pytest.mark.req("DSI-014")
def test_an_empty_field_is_one_empty_batch_of_the_declared_columns(store: NasArtifactStore) -> None:
    batches = list(gateway(store).fetch(gateway(store).query(query(Cone(60.0, 60.0, 0.1)))))
    assert len(batches) == 1 and batches[0].num_rows == 0
    assert batches[0].schema.names == ["source_id", "ra", "dec", "parallax"]
    assert batches[0].schema.field("source_id").type == pa.int64()


@pytest.mark.req("DSI-014")
def test_the_query_text_is_marked_bulk_so_its_dataset_never_passes_for_an_archive_read(
    store: NasArtifactStore,
) -> None:
    tap = TapArchiveAdapter({"gaia": load_sources()["gaia"]}, transport=None)  # type: ignore[arg-type]
    q = query(Cone(120.0, 30.0, 0.5))
    bulk, archive = gateway(store).query(q), tap.query(q)
    assert bulk.rendered_text == "BULK " + archive.rendered_text
    assert bulk.idempotency_key != archive.idempotency_key


@pytest.mark.req("DSI-014")
def test_an_incomplete_copy_is_refused_and_auto_keeps_the_archive(tmp_path: Path, store: NasArtifactStore) -> None:
    env = {"SDP_DATABASE_URL": "postgresql://unused", "SDP_ARTIFACT_ROOT": str(tmp_path / "store")}
    assert isinstance(archive_gateways(Settings.from_env(env), store)["gaia"], BulkCatalogueGateway)
    tap_pinned = Settings.from_env({**env, "SDP_GAIA_SOURCE": "tap"})
    assert isinstance(archive_gateways(tap_pinned, store)["gaia"], TapArchiveAdapter)

    # drop one stored file: the copy is incomplete
    g = gateway(store)
    (tmp_path / "store" / "paths" / g.prefix / "GaiaSource_000004-000005.parquet").unlink()
    assert [f.name for f in g.missing()] == ["GaiaSource_000004-000005.csv.gz"]
    assert isinstance(archive_gateways(Settings.from_env(env), store)["gaia"], TapArchiveAdapter)
    forced = archive_gateways(Settings.from_env({**env, "SDP_GAIA_SOURCE": "bulk"}), store)["gaia"]
    with pytest.raises(ValueError, match="1 files missing"):
        list(forced.fetch(forced.query(query(Cone(120.0, 30.0, 0.5)))))
    with pytest.raises(SettingsError, match="SDP_GAIA_SOURCE"):
        Settings.from_env({**env, "SDP_GAIA_SOURCE": "nas"})

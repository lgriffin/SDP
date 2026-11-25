"""T1/T3: one file names every Gaia release SDP reads (DR4 readiness): DR3's names give exactly the queries
SDP always sent, DR4 is refused until its names are confirmed, and a release's renamed columns are read
under SDP's names. Primed fakes and invented files only, no archive data."""

from __future__ import annotations

import gzip
import hashlib
import io
from dataclasses import replace
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from sdp.adapters.archives import gaia_nss as nss
from sdp.adapters.archives import gaia_releases as gr
from sdp.adapters.archives.bulk import BulkSweep, gaia_publisher
from sdp.adapters.archives.tap import load_sources
from sdp.adapters.archives.transport import HttpRequest
from sdp.adapters.archives.vo import sync_request
from sdp.adapters.storage.nas import NasArtifactStore

from .vo_fakes import PrimedService

pytestmark = pytest.mark.tier("T1")


@pytest.mark.req("DSI-027")
def test_the_packaged_file_names_dr2_dr3_and_an_unconfirmed_dr4() -> None:
    releases = gr.load_releases()
    assert {"dr2", "dr3", "dr4"} <= set(releases)
    assert releases["dr3"].confirmed and releases["dr3"].table("gaia_source") == "gaiadr3.gaia_source"
    dr4 = releases["dr4"]
    assert dr4.label == "gaia:DR4" and not dr4.confirmed, "DR4 stays unconfirmed until release day"
    assert dr4.bulk is not None and dr4.bulk.endswith("/gdr4/gaia_source/")
    assert gr.release("gaia:DR4") is not None and gr.by_label("gaia:DR3").name == "dr3"


@pytest.mark.req("DSI-027")
def test_an_unconfirmed_release_is_refused_with_the_command_that_checks_it() -> None:
    with pytest.raises(gr.UnconfirmedRelease, match="sdp archive gaia-check --release dr4"):
        gr.confirmed_release("dr4")
    assert gr.confirmed_release("dr3").name == "dr3"
    with pytest.raises(KeyError):
        gr.release("dr9")


@pytest.mark.req("DSI-027")
def test_a_release_file_with_unsafe_or_incomplete_names_is_refused() -> None:
    good = gr.load_releases()["dr4"]
    with pytest.raises(ValueError, match=r"schema\.table"):
        replace(good, tables={"gaia_source": "gaiadr4.gaia_source; DROP"})
    with pytest.raises(ValueError, match="plain names"):
        replace(good, columns={"parallax": "plx AS x"})
    with pytest.raises(ValueError, match="confirmed_on"):
        replace(good, confirmed=True)
    with pytest.raises(ValueError, match="end in /"):
        replace(good, bulk="https://example.org/gdr4")


@pytest.mark.req("DSI-027")
def test_the_dr3_source_is_unchanged_and_dr4s_shares_its_columns_under_its_own_table() -> None:
    dr3 = load_sources()["gaia"]
    assert gr.gaia_source("dr3") == dr3
    dr4 = gr.gaia_source("dr4").descriptor
    assert dr4.release_label == "gaia:DR4" and dr4.table == "gaiadr4.gaia_source"
    assert dr4.column_names() == dr3.descriptor.column_names() and dr4.source_id == "gaia"
    assert dr4.dialect_id == "gaia-dr4"


@pytest.mark.req("DSI-027")
def test_dr3_orbit_queries_are_byte_for_byte_the_ones_sdp_always_sent() -> None:
    """Kept orbit parts and recordings are keyed on the query; the release file must not change DR3's."""
    q = nss.orbit_query(0, 10)
    assert q.startswith("SELECT n.source_id, n.nss_solution_type, ")
    assert "FROM gaiadr3.nss_two_body_orbit AS n JOIN gaiadr3.gaia_source AS g ON g.source_id = n.source_id " in q
    assert "LEFT OUTER JOIN gaiadr3.binary_masses AS b ON b.source_id = n.source_id " in q
    assert q.endswith("WHERE n.a_thiele_innes IS NOT NULL AND n.source_id BETWEEN 0 AND 10")
    assert " AS " not in q.split(" FROM ")[0], "no column is aliased for DR3"


@pytest.mark.req("DSI-027")
def test_a_dr4_orbit_fetch_names_dr4s_tables_renamed_columns_and_label() -> None:
    dr4 = replace(gr.load_releases()["dr4"], columns={"ruwe": "ruwe_new", "a_thiele_innes": "a_ti"})
    q = nss.orbit_query(0, 10, dr4)
    assert "FROM gaiadr4.nss_two_body_orbit AS n JOIN gaiadr4.gaia_source AS g" in q
    assert "g.ruwe_new AS ruwe" in q and "n.a_ti AS a_thiele_innes" in q and "WHERE n.a_ti IS NOT NULL" in q
    service = PrimedService()
    cols: dict[str, object] = {c: [1.0] for c in (*nss.ORBIT_COLUMNS, *nss.SOURCE_COLUMNS, *nss.MASS_COLUMNS)}
    cols |= {"source_id": [5], "nss_solution_type": ["Orbital"], "combination_method": ["Orbital+M1"], "flag": [""]}
    service.table(sync_request(dr4.tap, nss.orbit_query(*nss.ranges(1)[0], dr4), nss.MAXREC), cols)
    table = nss.fetch_orbits(service, parts=1, mode="sync", release=dr4)
    meta = table.schema.metadata
    assert meta[b"sdp.release"] == b"gaia:DR4" and b"gaiadr4.nss_two_body_orbit" in meta[b"sdp.query"]


@pytest.mark.req("DSI-028")
def test_the_check_names_every_missing_table_and_column_and_counts_the_bulk_files() -> None:
    dr4 = gr.load_releases()["dr4"]
    service = PrimedService()

    def columns_query(table: str) -> HttpRequest:
        return sync_request(dr4.tap, f"SELECT column_name FROM tap_schema.columns WHERE table_name = '{table}'", 5000)

    service.table(columns_query("gaiadr4.gaia_source"), {"column_name": ["source_id", "ra", "dec"]})
    service.table(columns_query("gaiadr4.nss_two_body_orbit"), {"column_name": []})
    listing = b"abc  GaiaSource_000000-003111.csv.gz\ndef  GaiaSource_003112-005263.csv.gz\n"
    result = gr.check_release(
        dr4,
        service,
        {"gaia_source": ["source_id", "ra", "parallax"], "nss_two_body_orbit": ["source_id"]},
        lambda url: listing,
    )
    doc = result.to_document()
    assert doc["ok"] is False and doc["bulk_files"] == 2
    assert "gaiadr4.gaia_source: no column 'parallax' (SDP reads it as 'parallax')" in doc["problems"]
    assert "gaiadr4.nss_two_body_orbit: the archive has no such table" in doc["problems"]

    ok = PrimedService()
    ok.table(columns_query("gaiadr4.gaia_source"), {"column_name": ["SOURCE_ID", "plx"]})
    renamed = replace(dr4, columns={"parallax": "plx"})
    assert gr.check_release(renamed, ok, {"gaia_source": ["source_id", "parallax"]}).problems == ()


@pytest.mark.req("DSI-027")
@pytest.mark.tier("T3")
def test_a_bulk_copy_of_a_release_that_renamed_a_column_stores_it_under_sdps_name(tmp_path: Path) -> None:
    dr4 = gr.gaia_source("dr4").descriptor
    names = [("plx" if c == "parallax" else c) for c in dr4.column_names()]
    special = {"source_id": "7", "astrometric_params_solved": "31", "duplicated_source": "False"}
    row = ",".join(special.get(c, "0.5") for c in dr4.column_names())
    data = gzip.compress(("# %ECSV 1.0\n# ---\n" + ",".join(names) + "\n" + row + "\n").encode())
    folder = tmp_path / "gdr4"
    folder.mkdir()
    (folder / "GaiaSource_1.csv.gz").write_bytes(data)
    md5 = hashlib.md5(data, usedforsecurity=False).hexdigest()
    (folder / "_MD5SUM.txt").write_text(f"{md5}  GaiaSource_1.csv.gz\n")
    store = NasArtifactStore(tmp_path / "store")
    sweep = BulkSweep(
        store=store,
        descriptor=dr4,
        work_dir=tmp_path / "downloads",
        columns=dr4.column_names(),
        publisher=gaia_publisher(folder.as_uri() + "/"),
        renames={"parallax": "plx"},
    )
    results = sweep.run(sweep.files())
    assert [r.status for r in results] == ["stored"], [r.error for r in results]
    assert sweep.prefix == "bulk/gaia/gaia_DR4", "a DR4 copy sits beside the DR3 one"
    artifact = store.resolve(sweep.parquet_path(sweep.files()[0]))
    assert artifact is not None
    table = pq.read_table(io.BytesIO(store.get(artifact)))
    assert "parallax" in table.column_names and "plx" not in table.column_names
    assert table.column("parallax").to_pylist() == [0.5]


@pytest.mark.req("DSI-027")
def test_a_renamed_source_id_is_used_in_the_joins_and_the_range() -> None:
    dr4 = replace(gr.load_releases()["dr4"], columns={"source_id": "sid"})
    q = nss.orbit_query(0, 10, dr4)
    assert "ON g.sid = n.sid" in q and "ON b.sid = n.sid" in q and "AND n.sid BETWEEN 0 AND 10" in q
    assert "n.sid AS source_id" in q and ".source_id " not in q
    assert "AND n.sid IN (5)" in nss.targets_query([5], dr4)


@pytest.mark.req("DSI-027")
def test_saved_orbit_parts_of_another_release_are_never_mixed_in(tmp_path: Path) -> None:
    dr4 = gr.load_releases()["dr4"]
    parts = tmp_path / "orbits.parts"
    parts.mkdir()
    (parts / "part-001-of-001.parquet").write_bytes(b"not read")
    with pytest.raises(ValueError, match="unrecorded release"):
        nss.fetch_orbits(PrimedService(), parts=1, parts_dir=parts, release=dr4)
    (parts / "release.txt").write_text("gaia:DR3\n", "utf-8")
    with pytest.raises(ValueError, match="holds parts of gaia:DR3, not gaia:DR4"):
        nss.fetch_orbits(PrimedService(), parts=1, parts_dir=parts, release=dr4)

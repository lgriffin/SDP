"""T3: the rvs bulk subset keeps the Gaia DR3 stars with a radial velocity, with its columns (DSI-021)."""

from __future__ import annotations

import gzip
import io
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from typer.testing import CliRunner

from sdp.adapters.archives.bulk import BulkCatalogueGateway, BulkSubset, BulkSweep, load_subsets
from sdp.adapters.storage.nas import NasArtifactStore
from sdp.cli import app as cli
from sdp.kernel.domain.archive import CatalogueQuery, ColumnDescriptor, Cone

from .test_bulk import COLUMNS, GAIA, publish

pytestmark = pytest.mark.tier("T3")

RVS = load_subsets()["rvs"]
RV_COLUMNS = (
    "radial_velocity",
    "radial_velocity_error",
    "rv_nb_transits",
    "rv_expected_sig_to_noise",
    "rv_template_teff",
    "grvs_mag",
    "phot_variable_flag",
)


def ecsv(rows: list[dict[str, object]]) -> bytes:
    """A gzipped ECSV like ESA's: every gaia_source column SDP keeps, the radial-velocity ones, and one it does not."""
    names = [*COLUMNS, *RV_COLUMNS, "designation"]
    lines = [",".join(names)] + [",".join("null" if r.get(n) is None else str(r.get(n)) for n in names) for r in rows]
    return gzip.compress(("# %ECSV 1.0\n# ---\n# delimiter: ','\n" + "\n".join(lines) + "\n").encode())


def star(i: int, rv: float | None, **over: object) -> dict[str, object]:
    row: dict[str, object] = dict.fromkeys(COLUMNS, 0.5)
    row.update(source_id=5000 + i, ra=120.0 + 0.01 * i, dec=30.0, astrometric_params_solved=31)
    row.update(duplicated_source="False", designation=f"Gaia DR3 {5000 + i}")
    if rv is not None:
        row.update(
            radial_velocity=rv,
            radial_velocity_error=1.5,
            rv_nb_transits=12,
            rv_expected_sig_to_noise=40.0,
            rv_template_teff=5500.0,
            grvs_mag=11.2,
            phot_variable_flag="NOT_AVAILABLE",
        )
    row.update(over)
    return row


FILES = {
    "GaiaSource_000000-003111.csv.gz": ecsv(
        [star(0, -12.5), star(1, None), star(2, 30.25, phot_variable_flag="VARIABLE")]
    ),
    "GaiaSource_003112-005263.csv.gz": ecsv([star(3, None), star(4, None)]),
}


def sweep_for(tmp_path: Path, subset: BulkSubset | None = RVS) -> BulkSweep:
    return BulkSweep(
        store=NasArtifactStore(tmp_path / "store"),
        descriptor=GAIA,
        work_dir=tmp_path / "downloads",
        columns=COLUMNS,
        base_url=publish(tmp_path / "esa", FILES),
        sleep=lambda _s: None,
        subset=subset,
    )


@pytest.mark.req("DSI-021")
def test_the_rvs_subset_declares_the_radial_velocity_columns_with_the_types_esa_publishes() -> None:
    assert RVS.parent == "gaia" and RVS.keep_rows_with == "radial_velocity"
    types = {c.name: (c.datatype, c.unit) for c in RVS.columns}
    assert types == {
        "radial_velocity": ("float", "km / s"),
        "radial_velocity_error": ("float", "km / s"),
        "rv_nb_transits": ("short", None),
        "rv_expected_sig_to_noise": ("float", None),
        "rv_template_teff": ("float", "K"),
        "grvs_mag": ("float", "mag"),
        "phot_variable_flag": ("char", None),
    }
    extended = RVS.descriptor(GAIA)
    assert extended.column_names() == (*COLUMNS, *RV_COLUMNS)
    assert GAIA.column_names() == COLUMNS  # the full copy's columns are unchanged


@pytest.mark.req("DSI-021")
def test_only_stars_with_a_radial_velocity_are_kept_beside_the_full_copy(tmp_path: Path) -> None:
    sweep = sweep_for(tmp_path)
    results = sweep.run(sweep.files())
    assert [(r.status, r.rows) for r in results] == [("stored", 2), ("stored", 0)]
    first = sweep.files()[0]
    assert sweep.parquet_path(first) == "bulk/gaia/gaia_DR3/rvs/GaiaSource_000000-003111.parquet"
    artifact = sweep.store.resolve(sweep.parquet_path(first))
    assert artifact is not None
    table = pq.read_table(io.BytesIO(sweep.store.get(artifact)))
    assert table.column_names == [*COLUMNS, *RV_COLUMNS]
    assert table.column("source_id").to_pylist() == [5000, 5002]
    assert table.column("radial_velocity").type == "float" and table.column("rv_nb_transits").type == "int16"
    assert table.column("radial_velocity").to_pylist() == [-12.5, 30.25]
    assert table.column("phot_variable_flag").to_pylist() == ["NOT_AVAILABLE", "VARIABLE"]
    status = sweep.status(sweep.files())
    assert (status["subset"], status["done"], status["rows"]) == ("rvs", 2, 2)
    record = sweep.store.resolve(sweep.record_path(first))
    assert record is not None and b'"file_rows": 3' in sweep.store.get(record)
    assert sweep_for(tmp_path, None).status(sweep.files())["done"] == 0  # the full copy is separate


@pytest.mark.req("DSI-021")
def test_the_rvs_gateway_reads_a_field_with_its_radial_velocities(tmp_path: Path) -> None:
    sweep = sweep_for(tmp_path)
    sweep.run(sweep.files())
    g = BulkCatalogueGateway(sweep.store, GAIA, subset=RVS)
    assert g.complete()
    q = CatalogueQuery(
        "gaia", GAIA.release_label, Cone(120.0, 30.0, 0.5), ("source_id", "parallax", "radial_velocity"), (), 100, 100
    )
    rows = [r for b in g.fetch(g.query(q)) for r in b.to_pylist()]
    assert rows == [
        {"source_id": 5000, "parallax": 0.5, "radial_velocity": -12.5},
        {"source_id": 5002, "parallax": 0.5, "radial_velocity": 30.25},
    ]
    with pytest.raises(ValueError, match="not in the declared column list"):
        BulkCatalogueGateway(sweep.store, GAIA).query(q)  # the full copy has no radial velocities
    with pytest.raises(ValueError, match=r"rvs subset\) is incomplete .*sdp archive bulk --subset rvs"):
        BulkCatalogueGateway(NasArtifactStore(tmp_path / "empty"), GAIA, subset=RVS)._read(q)


@pytest.mark.req("DSI-021")
def test_a_subset_cannot_redeclare_a_parent_column_or_keep_rows_by_a_column_it_lacks() -> None:
    col = ColumnDescriptor("parallax", "double", "mas")
    with pytest.raises(ValueError, match="already columns of gaia"):
        BulkSubset("x", "gaia", "parallax", (col,)).descriptor(GAIA)
    with pytest.raises(ValueError, match="keep_rows_with 'rv' is not one of its columns"):
        BulkSubset("x", "gaia", "rv", (col,))
    with pytest.raises(ValueError, match="extends 2mass"):
        BulkSubset("x", "2mass", "parallax", (col,)).descriptor(GAIA)


@pytest.mark.req("DSI-021")
def test_the_cli_builds_the_rvs_subset_and_refuses_an_unknown_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = publish(tmp_path / "esa", FILES)
    monkeypatch.setenv("SDP_ARTIFACT_ROOT", str(tmp_path / "store"))
    monkeypatch.setenv("SDP_MIN_FREE_BYTES", "0")
    runner = CliRunner()
    run = runner.invoke(cli.app, ["archive", "bulk", "--base-url", base, "--subset", "rvs"])
    assert run.exit_code == 0, run.output
    assert "GaiaSource_000000-003111.csv.gz stored 2 rows" in run.stderr
    assert '"subset": "rvs"' in run.stdout and '"rows": 2' in run.stdout
    bad = runner.invoke(cli.app, ["archive", "bulk", "--base-url", base, "--subset", "nope"])
    assert bad.exit_code != 0 and "unknown subset 'nope'" in bad.output


@pytest.mark.req("DSI-021")
def test_a_subset_read_never_shares_its_query_text_or_key_with_the_full_copy(tmp_path: Path) -> None:
    store = NasArtifactStore(tmp_path / "store")
    q = CatalogueQuery("gaia", GAIA.release_label, Cone(120.0, 30.0, 0.5), ("source_id", "ra"), (), 100, 100)
    full, rvs = BulkCatalogueGateway(store, GAIA).query(q), BulkCatalogueGateway(store, GAIA, subset=RVS).query(q)
    assert full.rendered_text.startswith("BULK SELECT")
    assert rvs.rendered_text.startswith("BULK SUBSET rvs SELECT")
    assert full.idempotency_key != rvs.idempotency_key


@pytest.mark.req("DSI-021")
def test_a_subset_field_can_be_ordered_by_a_column_it_does_not_select(tmp_path: Path) -> None:
    sweep = sweep_for(tmp_path)
    sweep.run(sweep.files())
    g = BulkCatalogueGateway(sweep.store, GAIA, subset=RVS)
    q = CatalogueQuery(
        "gaia", GAIA.release_label, Cone(120.0, 30.0, 0.5), ("source_id",), (), 100, 100, order_by="radial_velocity"
    )
    assert [r["source_id"] for b in g.fetch(g.query(q)) for r in b.to_pylist()] == [5000, 5002]


@pytest.mark.req("DSI-021")
@pytest.mark.parametrize("name", ["", ".", "..", "a/b", "rvs/..", "RVS"])
def test_a_subset_name_is_one_plain_path_segment(name: str) -> None:
    with pytest.raises(ValueError, match="one plain path segment"):
        BulkSubset(name, "gaia", "radial_velocity", RVS.columns)

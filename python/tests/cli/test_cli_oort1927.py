"""T4: `sdp oort1927` on a synthetic galaxy, from a bulk copy with its rvs subset and from Parquet. Not Gaia data."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from typer.testing import CliRunner

from sdp.adapters.archives.bulk import BulkCatalogueGateway, load_subsets
from sdp.adapters.archives.tap import load_sources
from sdp.adapters.bootstrap import artifact_store
from sdp.cli import app as cli
from sdp_stellar import oort1927 as o

pytestmark = pytest.mark.tier("T4")
runner = CliRunner()
GAIA = load_sources()["gaia"].descriptor
RVS = load_subsets()["rvs"]
PARQUET = "application/vnd.apache.parquet"
NAMES = ("GaiaSource_000000-000001.csv.gz", "GaiaSource_000002-000003.csv.gz")


def native(cols: dict[str, Any], rv: bool) -> pa.Table:
    declared = RVS.descriptor(GAIA).columns if rv else GAIA.columns
    n = cols["ra"].size
    filled: dict[str, object] = {}
    for c in declared:
        if c.name in cols:
            filled[c.name] = cols[c.name]
        elif c.name.endswith("_error"):
            filled[c.name] = np.full(n, 1.0, dtype=np.float32)
        elif c.name == "phot_variable_flag":
            filled[c.name] = ["NOT_AVAILABLE"] * n
        else:
            filled[c.name] = np.zeros(n)
    return pa.table(filled)


def store_copy(root: Path, cols: dict[str, Any], prefix: str, rv: bool) -> None:
    store = artifact_store(root)
    sums = "".join(f"{'0' * 32}  {name}\n" for name in NAMES)
    store.put(sums.encode(), "text/plain", logical_path=f"{prefix}/_MD5SUM.txt")
    half = cols["ra"].size // 2
    for name, part in zip(NAMES, (slice(0, half), slice(half, None)), strict=True):
        sink = pa.BufferOutputStream()
        pq.write_table(native({k: v[part] for k, v in cols.items()}, rv), sink)
        store.link(f"{prefix}/{name.removesuffix('.csv.gz')}.parquet", store.put(sink.getvalue().to_pybytes(), PARQUET))


@pytest.fixture(scope="module")
def galaxy() -> dict[str, Any]:
    cols = o.synthetic_galaxy(n=120_000, seed=23)
    cols["phot_g_mean_mag"] = cols["phot_g_mean_mag"].astype(np.float32)
    return cols


@pytest.fixture
def bulk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, galaxy: dict[str, Any]) -> Path:
    root = tmp_path / "artifacts"
    monkeypatch.setenv("SDP_ARTIFACT_ROOT", str(root))
    store_copy(root, {k: v for k, v in galaxy.items() if k != "radial_velocity"}, "bulk/gaia/gaia_DR3", rv=False)
    return root


@pytest.mark.req("STL-018")
def test_the_arithmetic_needs_no_data() -> None:
    result = runner.invoke(cli.app, ["oort1927", "arithmetic"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["radius_from_A"]["printed"]["R_pc_printed"] == 5900.0


@pytest.mark.req("STL-018")
def test_a_run_reads_the_whole_sky_from_the_bulk_copy_and_skips_rvs_until_it_is_built(
    bulk: Path, tmp_path: Path
) -> None:
    out = tmp_path / "uc04"
    result = runner.invoke(cli.app, ["oort1927", "run", "--out", str(out), "--baseline-only"])
    assert result.exit_code == 0, result.output
    report = json.loads((out / "report.json").read_text("utf-8"))
    truth = o.oort_constants(r0_kpc=8.0, v0_km_s=220.0, slope=0.0)
    assert report["proper_motions"]["baseline"]["A"]["value"] == pytest.approx(truth["A"], rel=0.07)
    assert "sdp archive bulk --subset rvs" in report["radial_velocities"]["not_run"]
    assert "quick look" in (out / "report.md").read_text("utf-8")


@pytest.mark.req("STL-018")
def test_a_built_rvs_subset_adds_oorts_own_method(bulk: Path, tmp_path: Path, galaxy: dict[str, Any]) -> None:
    store_copy(bulk, galaxy, "bulk/gaia/gaia_DR3/rvs", rv=True)
    out = tmp_path / "uc04"
    result = runner.invoke(cli.app, ["oort1927", "run", "--out", str(out), "--baseline-only"])
    assert result.exit_code == 0, result.output
    rv = json.loads((out / "report.json").read_text("utf-8"))["radial_velocities"]
    assert rv["components"] == ["r"]
    assert rv["baseline"]["A"]["value"] == pytest.approx(
        o.oort_constants(r0_kpc=8.0, v0_km_s=220.0, slope=0.0)["A"], rel=0.07
    )


@pytest.mark.req("STL-018")
def test_the_export_is_a_fixed_sample_that_runs_from_parquet(bulk: Path, tmp_path: Path) -> None:
    path = tmp_path / "sample.parquet"
    result = runner.invoke(cli.app, ["oort1927", "export", "--out", str(path), "--every", "3"])
    assert result.exit_code == 0, result.output
    sample = pq.read_table(path)
    ids = sample.column("source_id").to_numpy()
    assert ids.size > 0 and (ids % 3 == 0).all()
    out = tmp_path / "from-parquet"
    again = runner.invoke(cli.app, ["oort1927", "run", "--parquet", str(path), "--out", str(out), "--baseline-only"])
    assert again.exit_code == 0, again.output
    assert (
        "no radial velocities" in json.loads((out / "report.json").read_text("utf-8"))["radial_velocities"]["not_run"]
    )


@pytest.mark.req("DSI-022")
def test_a_scan_reads_every_file_with_its_filters_and_refuses_an_incomplete_copy(bulk: Path) -> None:
    g = BulkCatalogueGateway(artifact_store(bulk), GAIA)
    seen: list[tuple[int, int]] = []
    parts = list(g.scan(("source_id", "parallax"), [("parallax", ">", 1.0)], progress=lambda i, n: seen.append((i, n))))
    assert seen == [(1, 2), (2, 2)]
    assert all((p.column("parallax").to_numpy() > 1.0).all() for p in parts)
    assert parts[0].column_names == ["source_id", "parallax"]
    with pytest.raises(ValueError, match="not in the declared column list"):
        next(g.scan(("radial_velocity",)))
    with pytest.raises(ValueError, match=r"rvs subset\) is incomplete"):
        next(BulkCatalogueGateway(artifact_store(bulk), GAIA, subset=RVS).scan(("source_id",)))


def test_a_run_without_a_bulk_copy_says_what_to_do(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SDP_ARTIFACT_ROOT", str(tmp_path / "none"))
    result = runner.invoke(cli.app, ["oort1927", "run", "--out", str(tmp_path / "x")])
    assert result.exit_code != 0
    assert "finish `sdp archive bulk`" in (result.output + str(result.exception))


@pytest.mark.req("STL-018")
def test_a_copy_with_no_star_passing_the_cuts_reports_failed_fits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, galaxy: dict[str, Any]
) -> None:
    root = tmp_path / "artifacts"
    monkeypatch.setenv("SDP_ARTIFACT_ROOT", str(root))
    bad = {k: v for k, v in galaxy.items() if k != "radial_velocity"}
    bad["ruwe"] = np.full(bad["ra"].size, 2.0)
    store_copy(root, bad, "bulk/gaia/gaia_DR3", rv=False)
    out = tmp_path / "uc04"
    result = runner.invoke(cli.app, ["oort1927", "run", "--out", str(out), "--baseline-only"])
    assert result.exit_code == 0, result.output
    report = json.loads((out / "report.json").read_text("utf-8"))
    assert "fewer than the declared 200" in report["proper_motions"]["baseline"]["failed"]
    assert report["verdict"]["status"] == "inconclusive"


@pytest.mark.req("STL-018")
def test_the_export_writes_its_sample_file_by_file(bulk: Path, tmp_path: Path, galaxy: dict[str, Any]) -> None:
    path = tmp_path / "every-one.parquet"
    result = runner.invoke(cli.app, ["oort1927", "export", "--out", str(path), "--every", "1"])
    assert result.exit_code == 0, result.output
    sample = pq.read_table(path)
    assert sample.num_rows > 0 and pq.ParquetFile(path).metadata.num_row_groups == 2  # one per bulk file

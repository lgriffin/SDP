"""T4: `sdp oort1932` on a synthetic column, from a bulk copy with its rvs subset and from Parquet. Not Gaia data."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from typer.testing import CliRunner

from sdp.adapters.archives.bulk import load_subsets
from sdp.adapters.archives.tap import load_sources
from sdp.adapters.bootstrap import artifact_store
from sdp.cli import app as cli
from sdp_stellar import oort1932 as o

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
def column() -> dict[str, Any]:
    cols = o.synthetic_column(n=150_000, potential=o.sheet_potential(3.8e-9, 150.0), rv_error_km_s=2.0, seed=32)
    cols["phot_g_mean_mag"] = cols["phot_g_mean_mag"].astype(np.float32)
    return cols


@pytest.fixture
def bulk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, column: dict[str, Any]) -> Path:
    root = tmp_path / "artifacts"
    monkeypatch.setenv("SDP_ARTIFACT_ROOT", str(root))
    store_copy(
        root,
        {k: v for k, v in column.items() if k not in ("radial_velocity", "radial_velocity_error")},
        "bulk/gaia/gaia_DR3",
        rv=False,
    )
    return root


@pytest.mark.req("STL-029")
def test_the_arithmetic_needs_no_data() -> None:
    result = runner.invoke(cli.app, ["oort1932", "arithmetic"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["dK_dz_s2"]["printed"] == 5.62e-30


@pytest.mark.req("STL-029")
def test_a_run_without_velocities_says_how_to_build_them(bulk: Path, tmp_path: Path) -> None:
    result = runner.invoke(cli.app, ["oort1932", "run", "--out", str(tmp_path / "x"), "--baseline-only"])
    assert result.exit_code != 0
    assert "sdp archive bulk --subset rvs" in (result.output + str(result.exception))


@pytest.mark.req("STL-029")
def test_a_run_reads_the_density_from_the_bulk_copy_and_the_velocities_from_its_rvs_subset(
    bulk: Path, tmp_path: Path, column: dict[str, Any]
) -> None:
    store_copy(bulk, column, "bulk/gaia/gaia_DR3/rvs", rv=True)
    out = tmp_path / "uc05"
    result = runner.invoke(cli.app, ["oort1932", "run", "--out", str(out), "--baseline-only"])
    assert result.exit_code == 0, result.output
    report = json.loads((out / "report.json").read_text("utf-8"))
    base = report["baseline"]
    assert base["n_density"] > 0 and base["n_velocity"] > 0
    assert base["flattening"]["value"] < 0.5
    md = (out / "report.md").read_text("utf-8-sig")
    assert "quick look" in md and "| 200 |" in md


@pytest.mark.req("STL-029")
def test_the_export_writes_both_samples_and_they_run_from_parquet(
    bulk: Path, tmp_path: Path, column: dict[str, Any]
) -> None:
    store_copy(bulk, column, "bulk/gaia/gaia_DR3/rvs", rv=True)
    folder = tmp_path / "sample"
    result = runner.invoke(cli.app, ["oort1932", "export", "--out", str(folder)])
    assert result.exit_code == 0, result.output
    density, velocities = folder / "density.parquet", folder / "velocities.parquet"
    assert pq.ParquetFile(density).metadata.num_row_groups == 2  # one per bulk file
    assert "radial_velocity" in pq.read_table(velocities).column_names
    out = tmp_path / "from-parquet"
    again = runner.invoke(
        cli.app,
        [
            "oort1932",
            "run",
            "--parquet",
            str(density),
            "--rv-parquet",
            str(velocities),
            "--out",
            str(out),
            "--baseline-only",
        ],
    )
    assert again.exit_code == 0, again.output
    assert json.loads((out / "report.json").read_text("utf-8"))["baseline"]["n_density"] > 0


@pytest.mark.req("STL-029")
def test_a_column_with_no_star_passing_the_cuts_reports_a_failed_baseline(
    tmp_path: Path, column: dict[str, Any]
) -> None:
    bad = {k: np.asarray(v).copy() for k, v in column.items()}
    bad["ruwe"] = np.full(bad["ra"].size, 2.0)
    path = tmp_path / "bad.parquet"
    pq.write_table(pa.table(bad), path)
    out = tmp_path / "uc05"
    result = runner.invoke(
        cli.app, ["oort1932", "run", "--parquet", str(path), "--rv-parquet", str(path), "--out", str(out)]
    )
    assert result.exit_code == 0, result.output
    report = json.loads((out / "report.json").read_text("utf-8"))
    assert "0 density" in report["baseline"]["failed"]
    assert report["verdict"]["status"] == "inconclusive"
    assert "failed" in (out / "report.md").read_text("utf-8-sig")

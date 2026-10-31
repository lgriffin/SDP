"""T4: `sdp einstein1921` on a synthetic Plummer cluster, from a bulk copy and from Parquet. Not Gaia data."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from typer.testing import CliRunner

from sdp.adapters.archives.tap import load_sources
from sdp.adapters.bootstrap import artifact_store
from sdp.cli import app as cli
from sdp_relativity import einstein1921 as e

pytestmark = pytest.mark.tier("T4")
runner = CliRunner()
GAIA = load_sources()["gaia"].descriptor
PREFIX = "bulk/gaia/gaia_DR3"
PARQUET = "application/vnd.apache.parquet"


def native_cone() -> pa.Table:
    cols = e.synthetic_cluster(
        mass=3e5, a_pc=3.0, distance_pc=7000, n_cluster=8000, n_field=6000, mass_to_light=2.0, seed=21
    )
    n = cols["ra"].size
    filled: dict[str, object] = {}
    for c in GAIA.columns:
        if c.name in cols:
            filled[c.name] = cols[c.name]
        elif c.name == "source_id":
            filled[c.name] = np.arange(n, dtype=np.int64) + 10**15
        elif c.name == "ref_epoch":
            filled[c.name] = np.full(n, 2016.0)
        elif c.name.endswith("_error"):
            filled[c.name] = np.full(n, 0.05)
        else:
            filled[c.name] = np.zeros(n)
    return pa.table(filled)


def to_parquet(table: pa.Table) -> bytes:
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink)
    return bytes(sink.getvalue().to_pybytes())


@pytest.fixture
def bulk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "artifacts"
    monkeypatch.setenv("SDP_ARTIFACT_ROOT", str(root))
    store = artifact_store(root)
    name = "GaiaSource_000000-000001.csv.gz"
    store.put(f"{'0' * 32}  {name}\n".encode(), "text/plain", logical_path=f"{PREFIX}/_MD5SUM.txt")
    store.link(f"{PREFIX}/{name.removesuffix('.csv.gz')}.parquet", store.put(to_parquet(native_cone()), PARQUET))
    return tmp_path


@pytest.mark.req("REL-011")
def test_the_arithmetic_command_needs_no_data() -> None:
    result = runner.invoke(cli.app, ["einstein1921", "arithmetic"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["A_printed"] == 0.6 and report["A_plummer"] == pytest.approx(3 * np.pi / 32)


@pytest.mark.req("REL-016")
def test_a_run_reads_the_predeclared_cone_from_the_bulk_copy_and_writes_both_reports(bulk: Path) -> None:
    out = bulk / "uc03"
    result = runner.invoke(cli.app, ["einstein1921", "run", "--out", str(out), "--resamples", "2", "--baseline-only"])
    assert result.exit_code == 0, result.output
    report = json.loads((out / "report.json").read_text("utf-8"))
    assert report["quick_look"] is True
    assert report["selection"]["stars_in"] == native_cone().num_rows
    # planted 2.0; the baseline subtracts a -0.017 mas zero-point the synthetic parallaxes lack, so it reads closer
    assert 1.2 < report["baseline"]["physical"]["mass_to_light"] < 2.4
    summary = (out / "report.md").read_text("utf-8")
    assert summary.startswith("# UC-03: Einstein (1921) rerun with Gaia\n")
    assert "| As printed | 1,2 | 6.5e+17 cm = 0.65 ly | 65 ly |" in summary
    assert "A quick look (fewer resamples or no scenarios): this is not the protocol's verdict." in summary


@pytest.mark.req("REL-016")
def test_a_run_from_parquet_matches_the_bulk_copy(bulk: Path) -> None:
    path = bulk / "cone.parquet"
    pq.write_table(native_cone(), path)
    out = bulk / "from-parquet"
    result = runner.invoke(
        cli.app,
        ["einstein1921", "run", "--parquet", str(path), "--out", str(out), "--resamples", "0", "--baseline-only"],
    )
    assert result.exit_code == 0, result.output
    report = json.loads((out / "report.json").read_text("utf-8"))
    assert report["verdict"]["status"] == "inconclusive"  # no bootstrap band, so no verdict
    assert 1.2 < report["baseline"]["physical"]["mass_to_light"] < 2.4


@pytest.mark.req("REL-016")
def test_an_incomplete_bulk_copy_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SDP_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    result = runner.invoke(cli.app, ["einstein1921", "run", "--out", str(tmp_path / "x")])
    assert result.exit_code != 0
    assert str(result.exception) == (
        "the bulk Gaia copy is missing or incomplete; finish `sdp archive bulk`, or pass --parquet"
    )


@pytest.mark.req("REL-019")
def test_the_corrections_command_writes_an_exploratory_report_beside_uc03s(bulk: Path) -> None:
    out = bulk / "uc03"
    path = bulk / "cone.parquet"
    pq.write_table(native_cone(), path)
    result = runner.invoke(
        cli.app, ["einstein1921", "corrections", "--parquet", str(path), "--out", str(out), "--resamples", "1"]
    )
    assert result.exit_code == 0, result.output
    report = json.loads((out / "corrections.json").read_text("utf-8"))
    assert report["quick_look"] is True
    assert report["reading"]["exploratory"] is True
    assert report["reading"]["status"] == "mixed"  # one refit of the declared 100 decides nothing
    assert len(report["corrections"]) == 108
    assert report["baseline"]["corrected"]["distance_pc"] == 7420.0
    summary = (out / "corrections.md").read_text("utf-8")
    assert summary.startswith("# UC-03b: corrections to the Einstein (1921) rerun (exploratory)\n")
    assert "UC-03's own verdict is unchanged." in summary
    assert "| 6' | 17 |" in summary
    assert "A quick look (fewer resamples): this is not the protocol's reading." in summary

"""T4: `sdp isochrones` on invented isochrones in MIST's layout (tests/stellar/mist_toy.py): reading a
tarball into the grid, fitting clusters from a stars CSV into MESA-style outputs, and field-star masses
(STL-036). Not MIST models and not Gaia data."""

from __future__ import annotations

import copy
import csv
import io
import json
import tarfile
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq
import pytest
from tests.stellar.mist_toy import cluster, toy_cmd_text
from typer.testing import CliRunner

from sdp.cli import app as cli
from sdp.cli import isochrones as cmd
from sdp_stellar import isochrones as iso

pytestmark = pytest.mark.tier("T4")
runner = CliRunner()
AGES = tuple(round(7.5 + 0.05 * i, 2) for i in range(46))


def tarball(path: Path, fehs: tuple[float, ...] = (-0.25, 0.0)) -> Path:
    with tarfile.open(path, "w:xz") as tar:
        for feh in fehs:
            data = toy_cmd_text(feh, AGES).encode()
            tag = f"{'m' if feh < 0 else 'p'}{abs(feh):.2f}"
            info = tarfile.TarInfo(
                f"MIST_v1.2_vvcrit0.4_UBVRIplus/MIST_v1.2_feh_{tag}_afe_p0.0_vvcrit0.4_UBVRIplus.iso.cmd"
            )
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        readme = b"not an isochrone"
        info = tarfile.TarInfo("MIST_v1.2_vvcrit0.4_UBVRIplus/README")
        info.size = len(readme)
        tar.addfile(info, io.BytesIO(readme))
    return path


ORIGINAL = iso.load_settings


def quick() -> dict[str, Any]:
    s = copy.deepcopy(ORIGINAL())
    s["fit"].update({"feh_min": -0.25, "av_max": 1.0})
    return s


@pytest.fixture
def data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SDP_DATA", str(tmp_path / "data"))
    monkeypatch.setattr(iso, "load_settings", quick)
    return tmp_path / "data"


def fetched(data: Path, tmp_path: Path) -> Path:
    result = runner.invoke(cli.app, ["isochrones", "fetch", "--file", str(tarball(tmp_path / "MIST.txz"))])
    assert result.exit_code == 0, result.output
    return data / "isochrones" / cmd.GRID_FILE


@pytest.mark.req("STL-036")
def test_fetch_reads_a_mist_tarball_and_records_its_provenance(data: Path, tmp_path: Path) -> None:
    path = fetched(data, tmp_path)
    table = pq.read_table(path)
    meta = {k.decode(): v.decode() for k, v in (table.schema.metadata or {}).items()}
    assert meta["sdp.mist_version"] == "1.2" and meta["sdp.mesa_revision"] == "7503"
    assert len(meta["sdp.source_sha256"]) == 64 and meta["sdp.source_url"] == "MIST.txz"
    assert meta["sdp.files"].count(".iso.cmd") == 2
    grid = cmd.load_grid()
    assert grid.fehs() == [-0.25, 0.0] and len(grid.ages(0.0)) == len(AGES)


@pytest.mark.req("STL-036")
def test_fetch_downloads_the_settings_url_once_and_refuses_a_tarball_of_something_else(
    data: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def fake(url: str, target: Path) -> None:
        calls.append(url)
        tarball(target)

    monkeypatch.setattr(cmd, "download", fake)
    for _ in range(2):
        result = runner.invoke(cli.app, ["isochrones", "fetch"])
        assert result.exit_code == 0, result.output
    assert calls == [quick()["source"]["url"]]
    assert cmd.load_grid().meta["source_url"] == quick()["source"]["url"]
    empty = tmp_path / "empty.txz"
    with tarfile.open(empty, "w:xz"):
        pass
    refused = runner.invoke(cli.app, ["isochrones", "fetch", "--file", str(empty)])
    assert refused.exit_code != 0 and "no .iso.cmd" in str(refused.exception)
    missing = runner.invoke(
        cli.app, ["isochrones", "fit", "--stars", str(tmp_path / "x.csv"), "--grid", str(tmp_path / "none")]
    )
    assert missing.exit_code != 0 and "sdp isochrones fetch" in str(missing.exception)


def stars_csv(path: Path) -> Path:
    s = quick()
    rng = np.random.default_rng(2)
    rows: list[dict[str, Any]] = []
    for name, age, n in (("Melotte_22", 8.05, 120), ("NGC_2682", 8.5, 5)):
        colour, g, _ = cluster(0.0, age, 0.2, 5.6, n, seed=9, settings=s)
        for i in range(n):
            rows.append(
                {
                    "cluster": name,
                    "source_id": 1000 * len(rows) + i,
                    "phot_g_mean_mag": round(float(g[i]), 5),
                    "bp_rp": round(float(colour[i]), 5),
                    "parallax": round(float(rng.normal(7.585 - 0.017, 0.05)), 5),
                    "parallax_error": 0.05,
                }
            )
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return path


@pytest.mark.req("STL-036")
def test_a_fit_writes_a_header_a_row_per_cluster_a_table_per_cluster_and_its_plots(data: Path, tmp_path: Path) -> None:
    fetched(data, tmp_path)
    result = runner.invoke(
        cli.app, ["isochrones", "fit", "--stars", str(stars_csv(tmp_path / "stars.csv")), "--name", "t"]
    )
    assert result.exit_code == 0, result.output
    out = data / "isochrones" / "t"
    summary = json.loads((out / "summary.json").read_text("utf-8"))
    assert summary["status"] == "EXPLORATORY" and summary["clusters"] == 2 and summary["fitted"] == 1
    assert summary["grid"]["mist_version"] == "1.2" and len(summary["settings_sha256"]) == 64
    (check,) = summary["known_answers"]
    assert check["cluster"] == "Melotte 22" and check["inside"] and summary["known_answers_verified"] is False
    with (out / "clusters.csv").open(encoding="utf-8") as handle:
        rows = {r["cluster_id"]: r for r in csv.DictReader(handle)}
    pleiades = rows["Melotte_22"]
    assert abs(float(pleiades["log_age"]) - 8.05) <= 0.1 and "inside" in pleiades["known_answer"]
    assert float(pleiades["log_age_lo"]) <= float(pleiades["log_age"]) <= float(pleiades["log_age_hi"])
    assert "fewer than" in rows["NGC_2682"]["note"] and not rows["NGC_2682"].get("known_answer")  # M67, not fitted
    with (out / "members" / "Melotte_22.csv").open(encoding="utf-8") as handle:
        members = list(csv.DictReader(handle))
    assert len(members) == 120 and members[0]["phase"] in {"main sequence", "red giant branch"}
    assert float(members[0]["star_mass_msun"]) > 0
    svg = (out / "plots" / "Melotte_22.svg").read_text("utf-8")
    assert svg.startswith("<svg") and "<polyline" in svg and "best cost by age" in svg
    assert not (out / "plots" / "NGC_2682.svg").exists()
    assert svg.count("<polyline") == 2  # the main sequence and the giant branch, not joined across the phase change
    assert "known answer: Melotte 22" in result.stderr
    fits = json.loads((out / "fits.json").read_text("utf-8"))
    assert fits["Melotte_22"]["schema"] == iso.SCHEMA


@pytest.mark.req("STL-036")
def test_a_fit_needs_exactly_one_source_of_clusters_and_the_stars_columns(data: Path, tmp_path: Path) -> None:
    fetched(data, tmp_path)
    none = runner.invoke(cli.app, ["isochrones", "fit"])
    assert none.exit_code != 0 and "exactly one" in str(none.exception)
    bad = tmp_path / "bad.csv"
    bad.write_text("cluster,source_id\nA,1\n", "utf-8")
    short = runner.invoke(cli.app, ["isochrones", "fit", "--stars", str(bad)])
    assert short.exit_code != 0 and "lacks columns" in str(short.exception)


@pytest.mark.req("STL-036")
def test_field_stars_get_a_mass_and_an_age_range_each(data: Path, tmp_path: Path) -> None:
    fetched(data, tmp_path)
    grid = cmd.load_grid()
    one = grid.isochrone(0.0, 8.5)
    rows = [30, 90]
    path = tmp_path / "field.csv"
    lines = ["source_id,phot_g_mean_mag,bp_rp,parallax,a_v"]
    for i, r in enumerate(rows):
        lines.append(f"{i + 1},{one['g'][r] + 5.0:.5f},{one['bp'][r] - one['rp'][r]:.5f},{10.0 - 0.017:.5f},")
    lines.append("3,12.0,0.8,-1.0,0.0")
    path.write_text("\n".join(lines) + "\n", "utf-8")
    out = tmp_path / "masses.csv"
    result = runner.invoke(cli.app, ["isochrones", "stars", "--stars", str(path), "--out", str(out)])
    assert result.exit_code == 0, result.output
    with out.open(encoding="utf-8") as handle:
        got = list(csv.DictReader(handle))
    for row, r in zip(got[:2], rows, strict=True):
        assert float(row["initial_mass_msun_p50"]) == pytest.approx(one["initial_mass"][r], rel=0.1)
        assert row["off_grid"] == "False" and row["phase"] == "main sequence"
    assert got[2]["off_grid"] == "True" and got[2]["initial_mass_msun_p50"] == ""
    report = json.loads(result.stdout)
    assert (
        report["stars"] == 3 and report["grid"]["mist_version"] == "1.2" and len(report["grid"]["source_sha256"]) == 64
    )
    assert json.loads(out.with_suffix(".json").read_text("utf-8")) == report


@pytest.mark.req("STL-036")
def test_a_grid_built_with_other_columns_is_refused_until_it_is_built_again(data: Path, tmp_path: Path) -> None:
    path = fetched(data, tmp_path)
    table = pq.read_table(path)
    meta = dict(table.schema.metadata or {})
    stale = json.loads(meta[b"sdp.columns"])
    stale.update({"g": "Gaia_G_DR2Rev", "bp": "Gaia_BP_DR2Rev", "rp": "Gaia_RP_DR2Rev"})
    meta[b"sdp.columns"] = json.dumps(stale).encode()
    pq.write_table(table.replace_schema_metadata(meta), path)
    refused = runner.invoke(cli.app, ["isochrones", "fit", "--stars", str(stars_csv(tmp_path / "s.csv"))])
    assert refused.exit_code != 0 and "other MIST columns" in str(refused.exception)
    pq.write_table(table.replace_schema_metadata({k: v for k, v in meta.items() if k != b"sdp.columns"}), path)
    unrecorded = runner.invoke(cli.app, ["isochrones", "fit", "--stars", str(tmp_path / "s.csv")])
    assert unrecorded.exit_code != 0 and "not recorded" in str(unrecorded.exception)
    fetched(data, tmp_path)  # built again from the same tarball, it is read
    assert cmd.load_grid().meta["columns"] == iso.columns_key(quick())


@pytest.mark.req("STL-037")
def test_clusters_fitted_side_by_side_give_the_same_results_as_one_at_a_time(data: Path, tmp_path: Path) -> None:
    fetched(data, tmp_path)
    stars = str(stars_csv(tmp_path / "stars.csv"))
    one = runner.invoke(cli.app, ["isochrones", "fit", "--stars", stars, "--name", "one", "--workers", "1"])
    two = runner.invoke(cli.app, ["isochrones", "fit", "--stars", stars, "--name", "two", "--workers", "2"])
    assert one.exit_code == 0 and two.exit_code == 0, one.output + two.output
    assert "in 2 processes" in two.stderr and "processes" not in one.stderr
    for name in ("clusters.csv", "fits.json", "members/Melotte_22.csv"):
        assert (data / "isochrones" / "one" / name).read_bytes() == (data / "isochrones" / "two" / name).read_bytes()
    with (data / "isochrones" / "two" / "members" / "Melotte_22.csv").open(encoding="utf-8") as handle:
        assert "g_offset_mag" in next(csv.DictReader(handle))

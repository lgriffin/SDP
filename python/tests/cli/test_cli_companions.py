"""T4: `sdp companions` on invented stars: the field list from an orbit table, and a cluster screen over
a bulk Gaia copy with sweep tiles (STL-023). Not Gaia data."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from typer.testing import CliRunner

from sdp.adapters.archives import gaia_nss as nss
from sdp.adapters.archives.tap import load_sources
from sdp.adapters.bootstrap import artifact_store
from sdp.cli import app as cli
from sdp.cli import companions as cmd

pytestmark = pytest.mark.tier("T4")
runner = CliRunner()
GAIA = load_sources()["gaia"].descriptor
PARQUET = "application/vnd.apache.parquet"
NAMES = ("GaiaSource_000000-000001.csv.gz", "GaiaSource_000002-000003.csv.gz")


def stars(rng: np.random.Generator) -> dict[str, np.ndarray]:
    nf, nc = 30_000, 200
    cl = {
        "source_id": np.arange(1, nc + 1, dtype=np.int64),
        "ra": 60.0 + rng.normal(0.0, 0.4, nc),
        "dec": 20.0 + rng.normal(0.0, 0.4, nc),
        "parallax": rng.normal(5.0, 0.03, nc),
        "pmra": rng.normal(10.0, 0.3, nc),
        "pmdec": rng.normal(-5.0, 0.3, nc),
        "phot_g_mean_mag": rng.uniform(10.0, 18.0, nc),
        "ruwe": 10 ** rng.normal(0.0, 0.03, nc),
    }
    cl["ruwe"][150:160] = 3.0
    fd = {
        "source_id": np.arange(10_000_000, 10_000_000 + nf, dtype=np.int64),
        "ra": rng.uniform(40.0, 80.0, nf),
        "dec": rng.uniform(0.0, 40.0, nf),
        "parallax": rng.uniform(0.5, 10.0, nf),
        "pmra": rng.normal(0.0, 30.0, nf),
        "pmdec": rng.normal(0.0, 30.0, nf),
        "phot_g_mean_mag": rng.uniform(10.0, 18.0, nf),
        "ruwe": 10 ** rng.normal(0.0, 0.03, nf),
    }
    return {k: np.concatenate([fd[k], cl[k]]) for k in fd}


def native(cols: dict[str, Any]) -> pa.Table:
    n = cols["ra"].size
    filled: dict[str, object] = {}
    for c in GAIA.columns:
        if c.name in cols:
            filled[c.name] = cols[c.name]
        elif c.name.endswith("_error"):
            filled[c.name] = np.full(n, 0.03, dtype=np.float32)
        elif c.name == "phot_variable_flag":
            filled[c.name] = ["NOT_AVAILABLE"] * n
        elif c.name == "duplicated_source":
            filled[c.name] = np.zeros(n, dtype=bool)
        else:
            filled[c.name] = np.zeros(n)
    return pa.table(filled)


def store_copy(root: Path, cols: dict[str, Any]) -> None:
    store = artifact_store(root)
    prefix = "bulk/gaia/gaia_DR3"
    store.put(
        "".join(f"{'0' * 32}  {name}\n" for name in NAMES).encode(), "text/plain", logical_path=f"{prefix}/_MD5SUM.txt"
    )
    half = cols["ra"].size // 2
    for name, part in zip(NAMES, (slice(0, half), slice(half, None)), strict=True):
        sink = pa.BufferOutputStream()
        pq.write_table(native({k: v[part] for k, v in cols.items()}), sink)
        store.link(f"{prefix}/{name.removesuffix('.csv.gz')}.parquet", store.put(sink.getvalue().to_pybytes(), PARQUET))


def orbit_table(ids: list[int], m1: list[float], m2: list[float], plx: list[float], dark: list[bool]) -> pa.Table:
    m1a, m2a, p = np.asarray(m1), np.asarray(m2), np.full(len(ids), 600.0)
    a0 = np.cbrt((m1a + m2a) * (p / 365.25) ** 2) * m2a / (m1a + m2a) * np.asarray(plx)
    n = len(ids)
    cols: dict[str, object] = {
        c: np.full(n, 0.01) for c in (*nss.ORBIT_COLUMNS, *nss.SOURCE_COLUMNS, *nss.MASS_COLUMNS)
    }
    cols |= {
        "source_id": pa.array(ids, type=pa.int64()),
        "nss_solution_type": ["Orbital"] * n,
        "combination_method": ["Orbital+M1"] * n,
        "flag": [""] * n,
        "period": p,
        "a_thiele_innes": a0,
        "b_thiele_innes": np.zeros(n),
        "f_thiele_innes": np.zeros(n),
        "g_thiele_innes": a0,
        "parallax": np.asarray(plx),
        "significance": np.full(n, 30.0),
        "phot_g_mean_mag": np.full(n, 13.0),
        "m1": m1a,
        "m2_lower": m2a * 0.95,
        "fluxratio_upper": np.where(dark, 0.0, 0.5),
    }
    return pa.table(cols).replace_schema_metadata({b"sdp.release": b"gaia:DR3"})


@pytest.fixture
def data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SDP_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("SDP_ARTIFACT_ROOT", str(tmp_path / "data" / "artifacts"))
    cols = stars(np.random.default_rng(17))
    store_copy(tmp_path / "data" / "artifacts", cols)
    target = tmp_path / "data" / "companions" / cmd.ORBITS_FILE
    target.parent.mkdir(parents=True)
    pq.write_table(
        orbit_table(
            [155, 10_000_003, 4373465352415301632],
            [1.0, 1.0, 0.95],
            [3.0, 0.3, 12.0],
            [5.0, 4.0, 2.1],
            [True, False, True],
        ),
        target,
    )
    tiles = tmp_path / "data" / "sweep" / "gaia-dr3" / "tiles"
    tiles.mkdir(parents=True)
    candidate = {
        "candidate_id": "near-b057-t010-c0",
        "status": "potentially_new",
        "n_members": 150,
        "mean_parallax_mas": 5.0,
        "member_keys": [f"gaia:DR3:{i}" for i in range(1, 151)],
        "crossmatch": [],
    }
    (tiles / "near-b057-t010.json").write_text(
        json.dumps({"tile": {"scale": "near"}, "candidates": [candidate]}), "utf-8"
    )
    return tmp_path / "data"


@pytest.mark.req("STL-023")
def test_the_field_list_flags_gaia_bh1_and_says_so(data: Path) -> None:
    result = runner.invoke(cli.app, ["companions", "field"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["gaia_bh1"]["verdict"] == "neutron-star-or-black-hole"
    assert report["flagged"] == {"neutron-star-or-black-hole": 2}
    assert "Gaia BH1 is flagged" in result.stderr
    assert (data / "companions" / "field.csv").read_text("utf-8").startswith("source_id,verdict")


@pytest.mark.req("STL-023")
def test_a_screen_finds_the_dark_partner_of_a_member_the_sweep_left_out(data: Path) -> None:
    result = runner.invoke(cli.app, ["companions", "screen", "--sweep", "gaia-dr3"])
    assert result.exit_code == 0, result.output
    summary = json.loads(result.stdout)
    assert summary["status"] == "EXPLORATORY"
    assert summary["clusters_screened"] == 1 and summary["flagged_in_clusters"] == 1
    (top,) = summary["top"]
    assert top["source_id"] == 155 and top["verdict"] == "neutron-star-or-black-hole" and not top["seed_member"]
    clusters = (data / "companions" / "clusters" / "clusters.csv").read_text("utf-8")
    assert "near-b057-t010-c0" in clusters


@pytest.mark.req("STL-023")
def test_a_screen_needs_one_source_of_clusters_and_a_fetched_orbit_table(data: Path, tmp_path: Path) -> None:
    both = runner.invoke(cli.app, ["companions", "screen"])
    assert both.exit_code != 0 and "exactly one" in str(both.exception)
    elsewhere = runner.invoke(cli.app, ["companions", "field", "--orbits", str(tmp_path / "none.parquet")])
    assert elsewhere.exit_code != 0 and "sdp companions fetch" in str(elsewhere.exception)


@pytest.mark.req("STL-023")
def test_fetch_writes_the_orbit_table_through_the_transport(data: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def fake(transport: object, progress: Any = None, parts_dir: Path | None = None) -> pa.Table:
        calls.append(1)
        return orbit_table([1], [1.0], [0.5], [3.0], [False])

    monkeypatch.setattr(nss, "fetch_orbits", fake)
    monkeypatch.setattr(cmd, "transport", lambda: None)
    out = data / "companions" / "fresh.parquet"
    result = runner.invoke(cli.app, ["companions", "fetch", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert calls == [1] and pq.read_table(out).num_rows == 1


@pytest.mark.req("STL-023")
def test_the_cluster_table_keeps_every_column_when_some_clusters_are_not_screened(data: Path, tmp_path: Path) -> None:
    listed = tmp_path / "members.csv"
    listed.write_text(
        "cluster,source_id\n" + "".join(f"A,{i}\n" for i in range(1, 151)) + "gone,999999999\ngone,888888888\n",
        "utf-8",
    )
    result = runner.invoke(cli.app, ["companions", "screen", "--members", str(listed)])
    assert result.exit_code == 0, result.output
    with (data / "companions" / "clusters" / "clusters.csv").open(newline="", encoding="utf-8") as handle:
        table = list(csv.DictReader(handle))
    assert {r["cluster_id"] for r in table} == {"A", "gone"}
    assert "wobblers_field_p" in table[0] and "note" in table[0]


@pytest.mark.req("STL-023")
def test_the_field_list_fails_when_gaia_bh1_is_not_flagged(data: Path, tmp_path: Path) -> None:
    without = tmp_path / "no-bh1.parquet"
    pq.write_table(orbit_table([155], [1.0], [3.0], [5.0], [True]), without)
    result = runner.invoke(cli.app, ["companions", "field", "--orbits", str(without), "--out", str(tmp_path)])
    assert result.exit_code != 0 and "Gaia BH1" in str(result.exception)
    assert (tmp_path / "field.csv").exists()

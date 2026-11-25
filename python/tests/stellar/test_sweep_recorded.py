"""T8R: whole tiles of the discovery sweep on real Gaia DR3 stars, run again and checked against what the sweep
wrote for them on Leigh's PC (TST-011, STL-009).

Each tile folder is exported by ``tools/realdata_slice.py sweep-tile`` from the PC's sweep: the exact stars the
sweep handed the tile, the stored known-cluster catalogues, and the sweep's own ``tile.json`` (the known answer).
Folders committed under ``tests/cassettes/gaia-sweep-tiles`` run on every pull request; folders under
``<SDP_REALDATA>/realdata/sweep-tiles`` run with ``-m realdata`` (harness/run-realdata.ps1 exports them).

The tiles chosen are the ones holding the nearby clusters issue #170 lists (NGC 2547, IC 2391, NGC 6475). The test
records what the sweep does with them today, rejected or not: #170 is parked, so a fix shows up here as a changed
answer, which is then re-recorded with the fix.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import pytest
from tests.conftest import realdata_root
from tests.stellar.test_sweep import SYN_B, benchmarks, owner, praesepe_field, tile_rows

from sdp.adapters.bootstrap import clusterers
from sdp_stellar import sweep
from sdp_stellar.benchmark import benchmark_from

pytestmark = [pytest.mark.tier("T8"), pytest.mark.scientific]

COMMITTED = Path(__file__).resolve().parents[1] / "cassettes" / "gaia-sweep-tiles"
#: settings the tiles' guard adds beside the sweep's own
GUARD_ONLY = ("benchmarks_used", "prepared_files")


def folders(root: Path | None) -> list[Path]:
    if root is None or not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if (p / "manifest.json").is_file())


def realdata_folders() -> list[Path]:
    root = realdata_root()
    return folders(None if root is None else root / "realdata" / "sweep-tiles")


def summary(candidates: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    """What a person reads off a tile: each candidate's id, status, size, CMD coherence and catalogue names."""
    return sorted(
        (
            c["candidate_id"],
            c["status"],
            c["n_members"],
            None if c.get("cmd_coherence") is None else round(float(c["cmd_coherence"]), 6),
            tuple(sorted({m["name"] for x in c.get("crossmatch", []) for m in x["matches"]})),
        )
        for c in candidates
    )


def check_tile(folder: Path) -> None:
    manifest = json.loads((folder / "manifest.json").read_text("utf-8"))
    for name, digest in manifest["sha256"].items():
        assert hashlib.sha256((folder / name).read_bytes()).hexdigest() == digest, name

    swept = json.loads((folder / "sweep-settings.json").read_text("utf-8"))
    swept = {k: v for k, v in swept.items() if k not in GUARD_ONLY}
    settings = sweep.load_settings()
    assert swept == json.loads(json.dumps(settings)), (
        f"{folder.name} was swept under other settings than this SDP's; export it again from a fresh sweep"
    )

    recorded = json.loads((folder / "tile.json").read_text("utf-8"))
    tile_id = manifest["tile_id"]
    name = recorded["tile"]["scale"]
    scale = next((s for s in sweep.scales(settings) if s.name == name), None) if name else None
    tile_deg = scale.tile_deg if scale else float(settings["tile_deg"])
    t = sweep.tile(recorded["tile"]["band"], recorded["tile"]["index"], tile_deg, name)
    assert t.tile_id == tile_id
    stars = pq.read_table(folder / "stars.parquet")
    benchmarks = [benchmark_from(b, pq.read_table(folder / f"benchmark-{b}.parquet")) for b in manifest["benchmarks"]]
    again = sweep.sweep_tile(stars, t, settings, clusterers(), benchmarks, int(settings["seed"]), scale)

    assert again["owned_stars"] == recorded["owned_stars"]
    assert again["skipped"] == recorded["skipped"]
    assert summary(again["candidates"]) == summary(recorded["candidates"])
    assert (again["null_fields"], again["null_fields_with_survivors"]) == (
        recorded["null_fields"],
        recorded["null_fields_with_survivors"],
    )


@pytest.mark.req("TST-011")
@pytest.mark.parametrize("folder", folders(COMMITTED), ids=lambda p: p.name)
def test_a_committed_tile_gives_back_what_the_pc_sweep_wrote(folder: Path) -> None:
    check_tile(folder)


@pytest.mark.realdata
@pytest.mark.req("TST-011")
@pytest.mark.parametrize("folder", realdata_folders(), ids=lambda p: p.name)
def test_an_exported_tile_gives_back_what_the_pc_sweep_wrote(folder: Path) -> None:
    check_tile(folder)


def export_synthetic(folder: Path, mutate: bool = False) -> Path:
    """A tile folder in the exporter's layout, built from the invented Praesepe field: the check's own test."""
    from sdp.cli.discover import _jsonable

    settings = sweep.load_settings()
    t = owner(*SYN_B)
    rows = tile_rows(t, praesepe_field())
    marks = benchmarks()
    result = sweep.sweep_tile(rows, t, settings, clusterers(), marks, int(settings["seed"]))
    if mutate:
        result["candidates"][0]["n_members"] += 1
    target = folder / t.tile_id
    target.mkdir(parents=True)
    pq.write_table(rows, target / "stars.parquet")
    for b in marks:
        pq.write_table(b.table, target / f"benchmark-{b.benchmark_id}.parquet")
    (target / "tile.json").write_text(json.dumps(result, sort_keys=True, default=_jsonable), "utf-8")
    guard = {**settings, "benchmarks_used": {}, "prepared_files": "0"}
    (target / "sweep-settings.json").write_text(json.dumps(guard, sort_keys=True), "utf-8")
    files = sorted(target.iterdir())
    manifest = {
        "tile_id": t.tile_id,
        "benchmarks": [b.benchmark_id for b in marks],
        "sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
    }
    (target / "manifest.json").write_text(json.dumps(manifest), "utf-8")
    return target


@pytest.mark.req("TST-011")
def test_the_tile_check_passes_on_a_tiles_own_result_and_fails_on_a_changed_one(tmp_path: Path) -> None:
    check_tile(export_synthetic(tmp_path / "same"))
    with pytest.raises(AssertionError):
        check_tile(export_synthetic(tmp_path / "changed", mutate=True))


@pytest.mark.req("TST-011")
def test_the_sweep_misses_ic_2391_although_its_members_are_in_the_tile() -> None:
    # documents a known miss (#170): the near-shell tile holding IC 2391 swept on 2026-10-31 gave no candidate at
    # all, while ~180 stars sit at hr23's IC 2391 position, motion and parallax. When the sweep is fixed this goes
    # red; re-export the tile from the fixed sweep and replace this test with the new answer.
    import numpy as np

    folder = COMMITTED / "near-b003-t008"
    recorded = json.loads((folder / "tile.json").read_text("utf-8"))
    assert recorded["candidates"] == []
    hr23 = pq.read_table(folder / "benchmark-hr23.parquet").to_pylist()
    ic = next(r for r in hr23 if r["name"] == "IC_2391")
    s = pq.read_table(folder / "stars.parquet")
    col = {
        n: s.column(n).to_numpy() for n in ("ra_deg", "dec_deg", "pm_ra_cosdec_masyr", "pm_dec_masyr", "parallax_mas")
    }
    near = (np.abs(col["dec_deg"] - ic["dec_deg"]) < 1.5) & (
        np.abs((col["ra_deg"] - ic["ra_deg"]) * np.cos(np.radians(ic["dec_deg"]))) < 1.5
    )
    moving = (
        np.hypot(col["pm_ra_cosdec_masyr"] - ic["pm_ra_cosdec_masyr"], col["pm_dec_masyr"] - ic["pm_dec_masyr"]) < 2
    )
    distance = np.abs(col["parallax_mas"] - ic["parallax_mas"]) < 1
    assert (near & moving & distance).sum() >= 150

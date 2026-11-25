"""tools/realdata_slice.py sweep-cluster: a known cluster's tile exported from a sweep the CLI ran, then run again
by the T8R tile check (TST-011). Invented stars; on the PC the same path runs on the real sweep."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pyarrow.parquet as pq
import pytest
from tests.cli.test_cli_discover import benchmark_tables, bulk, invoke
from tests.stellar.test_sweep_recorded import check_tile

pytestmark = pytest.mark.tier("T4")
__all__ = ["bulk"]  # the fixture, used by name

TOOL = Path(__file__).resolve().parents[2] / "tools" / "realdata_slice.py"


def tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sdp_realdata_slice", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.req("TST-011")
def test_a_tile_exports_and_its_check_gives_back_the_sweeps_answer(bulk: Path, tmp_path: Path) -> None:
    invoke("discover", "prepare", "--workers", "1")
    invoke("discover", "sweep", "--bands", "54", "--workers", "2")  # Dec 18..20, where SYN-B sits
    report = invoke("discover", "report")
    tile_id = str(report["top"][0]["candidate_id"]).rsplit("-c", 1)[0]
    t = tool()
    manifest = t.sweep_tile(tile_id, tmp_path / "out")
    folder = tmp_path / "out" / tile_id
    assert manifest["stars"] == pq.read_table(folder / "stars.parquet").num_rows > 0
    recorded = json.loads((folder / "tile.json").read_text("utf-8"))
    assert recorded["tile"]["tile_id"] == tile_id
    assert "potentially_new" in {c["status"] for c in recorded["candidates"]}
    check_tile(folder)
    # the export is byte-for-byte repeatable
    assert t.sweep_tile(tile_id, tmp_path / "again")["sha256"] == manifest["sha256"]


def test_a_known_cluster_is_found_in_its_own_shells_tile(bulk: Path) -> None:
    from sdp_stellar import sweep

    row = next(r for r in benchmark_tables()["hr23"].to_pylist() if r["name"] == "SYN-A")
    tile_id = tool().cluster_tile("syn a")  # names are matched ignoring case, spaces and punctuation
    settings = sweep.load_settings()
    scale = next(s for s in sweep.scales(settings) if s.owns(np.array([row["parallax_mas"]]))[0])
    shell, band, index = tile_id.rsplit("-", 2)
    t = sweep.tile(int(band[1:]), int(index[1:]), scale.tile_deg, shell)
    assert shell == scale.name and t.contains(row["ra_deg"], row["dec_deg"])
    with pytest.raises(SystemExit, match="no stored known-cluster catalogue"):
        tool().cluster_tile("NGC 0")

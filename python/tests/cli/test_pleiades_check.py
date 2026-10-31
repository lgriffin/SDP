"""tools/check_pleiades.py: the band 57 check before a whole-sky sweep passes only on one known Pleiades."""

from __future__ import annotations

import csv
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

pytestmark = pytest.mark.tier("T4")
TOOL = Path(__file__).resolve().parents[2] / "tools" / "check_pleiades.py"

FIELDS = [
    "candidate_id", "status", "score", "ra_deg", "dec_deg", "pm_ra_cosdec_masyr", "pm_dec_masyr",
    "parallax_mas", "members", "stability", "cmd_coherence", "significance_sigma", "matches", "reasons",
]  # fmt: skip


@pytest.fixture
def tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sdp_pleiades_check", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def pleiades(**over: Any) -> dict[str, Any]:
    row = {
        "candidate_id": "near-b009-t010-c0", "status": "known", "score": 0.9, "ra_deg": 56.6, "dec_deg": 24.2,
        "pm_ra_cosdec_masyr": 19.8, "pm_dec_masyr": -45.2, "parallax_mas": 7.3, "members": 812,
        "stability": 1.0, "cmd_coherence": 0.95, "significance_sigma": 21.4, "matches": "Melotte_22", "reasons": "",
    }  # fmt: skip
    return {**row, **over}


FIELD_STAR_GROUP = pleiades(
    candidate_id="far-b057-t080-c1", status="rejected", ra_deg=58.0, dec_deg=25.0, parallax_mas=0.4,
    pm_ra_cosdec_masyr=1.0, pm_dec_masyr=-2.0, members=14, significance_sigma=1.2, matches="",
)  # fmt: skip


def write(tmp_path: Path, rows: list[dict[str, Any]], fields: list[str] = FIELDS) -> Path:
    path = tmp_path / "candidates.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path


def run(tool: ModuleType, path: Path, *extra: str) -> int:
    return int(tool.main(["--csv", str(path), *extra]))


def test_one_known_significant_pleiades_passes(tool: ModuleType, tmp_path: Path, capsys: Any) -> None:
    assert run(tool, write(tmp_path, [FIELD_STAR_GROUP, pleiades()])) == 0
    out = capsys.readouterr().out
    assert "FAIL" not in out
    assert "21.4 sigma" in out and "812 members" in out


def test_a_missing_pleiades_fails(tool: ModuleType, tmp_path: Path, capsys: Any) -> None:
    assert run(tool, write(tmp_path, [FIELD_STAR_GROUP])) == 1
    assert "FAIL  found once: no candidate" in capsys.readouterr().out


def test_a_pleiades_listed_twice_fails(tool: ModuleType, tmp_path: Path, capsys: Any) -> None:
    copy = pleiades(candidate_id="mid-b028-t014-c0", ra_deg=57.4)
    assert run(tool, write(tmp_path, [pleiades(), copy])) == 1
    assert "2 candidates match" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("over", "message"),
    [
        ({"status": "potentially_new", "matches": ""}, "FAIL  labelled known: status potentially_new"),
        ({"matches": ""}, "FAIL  matched in a catalogue"),
        ({"significance_sigma": 2.5}, "FAIL  significance above 3 sigma: 2.5 sigma"),
        ({"significance_sigma": ""}, "FAIL  significance above 3 sigma: not tested"),
        ({"members": 30}, "FAIL  at least 50 members: 30 members"),
    ],
)
def test_each_failed_property_is_named(
    tool: ModuleType, tmp_path: Path, capsys: Any, over: dict[str, Any], message: str
) -> None:
    assert run(tool, write(tmp_path, [pleiades(**over)])) == 1
    assert message in capsys.readouterr().out


def test_a_candidate_at_another_distance_or_motion_is_not_the_pleiades(tool: ModuleType) -> None:
    def text(row: dict[str, Any]) -> dict[str, str]:
        return {k: str(v) for k, v in row.items()}

    assert tool.is_pleiades(text(pleiades()))
    assert not tool.is_pleiades(text(pleiades(parallax_mas=5.4)))  # Praesepe's distance
    assert not tool.is_pleiades(text(pleiades(pm_ra_cosdec_masyr=-36.0, pm_dec_masyr=-12.9)))
    assert not tool.is_pleiades(text(pleiades(ra_deg=66.75, dec_deg=15.87)))  # the Hyades' place
    assert not tool.is_pleiades(text(pleiades(parallax_mas="")))


def test_csv_from_before_the_significance_test_fails(tool: ModuleType, tmp_path: Path, capsys: Any) -> None:
    old = [f for f in FIELDS if f != "significance_sigma"]
    assert run(tool, write(tmp_path, [pleiades()], old)) == 1
    assert "predates #149" in capsys.readouterr().out


def test_without_benchmarks_only_asks_that_it_is_not_rejected(tool: ModuleType, tmp_path: Path, capsys: Any) -> None:
    path = write(tmp_path, [pleiades(status="inconclusive", matches="")])
    assert run(tool, path, "--without-benchmarks") == 0
    assert run(tool, write(tmp_path, [pleiades(status="rejected")]), "--without-benchmarks") == 1
    assert "FAIL  not rejected: status rejected" in capsys.readouterr().out


def test_default_path_is_the_gaia_dr3_sweep_under_sdp_data(tool: ModuleType, tmp_path: Path) -> None:
    assert tool.default_csv({"SDP_DATA": str(tmp_path)}) == tmp_path / "sweep" / "gaia-dr3" / "candidates.csv"
    root = tmp_path / "artifacts"
    assert tool.default_csv({"SDP_ARTIFACT_ROOT": str(root)}) == tmp_path / "sweep" / "gaia-dr3" / "candidates.csv"
    with pytest.raises(SystemExit):
        tool.default_csv({})


def test_a_missing_csv_says_which_commands_make_it(tool: ModuleType, tmp_path: Path, capsys: Any) -> None:
    assert run(tool, tmp_path / "nothing.csv") == 1
    assert "sdp discover report" in capsys.readouterr().out

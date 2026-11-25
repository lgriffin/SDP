"""tools/realdata.py: the real-data ratchet moves marks only the better way and fails a worse value (TST-012)."""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.tier("T0")

ROOT = Path(__file__).resolve().parents[3]
TOOL = ROOT / "python" / "tools" / "realdata.py"


def tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sdp_realdata_tool", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # the tool's dataclass looks its module up
    spec.loader.exec_module(module)
    return module


def baselines(tmp_path: Path, marks: dict[str, dict[str, object]]) -> Path:
    path = tmp_path / "baselines.json"
    path.write_text(json.dumps({"marks": marks}), "utf-8")
    return path


def measured(tmp_path: Path, values: dict[str, float]) -> Path:
    path = tmp_path / "measured.json"
    path.write_text(json.dumps(values), "utf-8")
    return path


MARKS: dict[str, dict[str, object]] = {
    "known": {"better": "higher", "value": 14},
    "null": {"better": "lower", "value": 0.006, "tolerance": 0.0005},
    "bh": {"better": "exact", "value": 136},
    "fresh": {"better": "lower", "value": None},
}


@pytest.mark.req("TST-012")
@pytest.mark.parametrize(
    ("values", "worse"),
    [
        ({"known": 13}, "known"),
        ({"null": 0.0066}, "null"),
        ({"bh": 135}, "bh"),
        ({"bh": 137}, "bh"),
        ({"known": float("nan")}, "known"),
    ],
)
def test_a_value_worse_than_its_mark_fails_the_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], values: dict[str, float], worse: str
) -> None:
    t = tool()
    code = t.main(["check", str(measured(tmp_path, values)), "--baselines", str(baselines(tmp_path, MARKS))])
    assert code == 1
    assert f"worse than the mark: {worse}" in capsys.readouterr().out


@pytest.mark.req("TST-012")
def test_values_at_or_better_than_their_marks_pass_and_unmeasured_ones_are_listed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    t = tool()
    values = {"known": 14, "null": 0.0064, "bh": 136, "extra": 1}
    assert t.main(["check", str(measured(tmp_path, values)), "--baselines", str(baselines(tmp_path, MARKS))]) == 0
    out = capsys.readouterr().out
    assert "not-measured fresh" in out and "unknown      extra" in out


@pytest.mark.req("TST-012")
def test_with_require_all_a_mark_left_unmeasured_fails_the_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    t = tool()
    path = baselines(tmp_path, MARKS)
    partial = measured(tmp_path, {"known": 14, "bh": 136})
    assert t.main(["check", str(partial), "--baselines", str(path)]) == 0
    assert t.main(["check", str(partial), "--baselines", str(path), "--require-all"]) == 1
    # an unseeded mark has nothing to hold the run to
    assert "not measured: null" in capsys.readouterr().out


@pytest.mark.req("TST-012")
def test_update_moves_marks_only_the_better_way_and_seeds_empty_ones(tmp_path: Path) -> None:
    t = tool()
    path = baselines(tmp_path, MARKS)
    values = {"known": 20, "null": 0.0062, "bh": 136, "fresh": 0.04}
    assert t.main(["update", str(measured(tmp_path, values)), "--baselines", str(path), "--source", "test"]) == 0
    marks = json.loads(path.read_text("utf-8"))["marks"]
    assert marks["known"]["value"] == 20 and marks["known"]["source"] == "test"
    assert marks["null"]["value"] == 0.006  # within tolerance: not better, so not moved
    assert marks["fresh"]["value"] == 0.04
    # and a worse run refuses to write anything
    before = path.read_text("utf-8")
    worse = measured(tmp_path, {"known": 19})
    assert t.main(["update", str(worse), "--baselines", str(path), "--source", "test"]) == 1
    assert path.read_text("utf-8") == before


@pytest.mark.req("TST-012")
def test_the_committed_baselines_load_and_every_mark_says_where_it_came_from() -> None:
    t = tool()
    doc = t.load(ROOT / "realdata" / "baselines.json")
    for name, mark in doc["marks"].items():
        assert mark["source"] and mark["measured_on"], name


def test_measure_reads_the_field_list_and_the_screen_summary(tmp_path: Path) -> None:
    t = tool()
    field = tmp_path / "field.csv"
    with field.open("w", newline="", encoding="utf-8") as handle:
        w = csv.writer(handle)
        w.writerow(["source_id", "verdict"])
        w.writerows([[1, "neutron-star-or-black-hole"], [2, "dark-partner"], [3, "dark-partner"]])
    screen = tmp_path / "summary.json"
    screen.write_text(json.dumps({"clusters_screened": 52, "flagged_in_clusters": 0}), "utf-8")
    out = tmp_path / "m.json"
    assert t.main(["measure", str(out), "--field", str(field), "--screen", str(screen)]) == 0
    assert json.loads(out.read_text("utf-8")) == {
        "companions.dr3_screen.clusters_screened": 52.0,
        "companions.dr3_screen.member_dark_partners": 0.0,
        "companions.field_dr3.compact": 1.0,
        "companions.field_dr3.dark_partner": 2.0,
    }

"""tools/stress_pull.py: fields in, one report line per run out, with a fake runner (#60)."""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.tier("T4")
TOOL = Path(__file__).resolve().parents[2] / "tools" / "stress_pull.py"


@pytest.fixture(scope="module")
def tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sdp_stress_tool", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


FIELDS = """label,source,region,row_cap,columns,profile
pleiades-gaia,gaia,"icrs;circle(56.75,24.12,1.0)",200000,source_id ra dec,
pleiades,,"icrs;circle(56.75,24.12,0.5)",,,../profiles/moving-group-search.yaml
bad-box,gaia,"icrs;box(1,2,3,4)",,,
"""


def fields(tool: ModuleType, tmp_path: Path) -> list[object]:
    path = tmp_path / "fields.csv"
    path.write_text(FIELDS)
    return tool.read_fields(path)  # type: ignore[no-any-return]


def test_both_shapes_of_field_become_their_sdp_commands(tool: ModuleType, tmp_path: Path) -> None:
    gaia, run, box = fields(tool, tmp_path)
    describe = lambda _s: ("gaia:DR3", ("source_id", "ra", "dec", "parallax"))  # noqa: E731
    command = tool.command_for(gaia, describe)
    assert command[1:7] == ["archive", "acquire", "--source", "gaia", "--release", "gaia:DR3"]
    assert command[7:] == [
        "--ra", "56.75", "--dec", "24.12", "--radius", "1.0", "--row-cap", "200000",
        "--column", "source_id", "--column", "ra", "--column", "dec",
    ]  # fmt: skip
    assert tool.command_for(run, describe)[1:] == [
        "investigate", "--region", "icrs;circle(56.75,24.12,0.5)",
        "--profile", "../profiles/moving-group-search.yaml", "--label", "pleiades",
    ]  # fmt: skip
    with pytest.raises(ValueError, match="takes a cone"):
        tool.command_for(box, describe)


def test_a_fields_file_without_a_profile_or_source_is_refused(tool: ModuleType, tmp_path: Path) -> None:
    path = tmp_path / "fields.csv"
    path.write_text("label,region\nx,icrs;circle(1,2,3)\n")
    with pytest.raises(ValueError, match="line 2"):
        tool.read_fields(path)


def test_each_pass_of_each_field_is_one_report_line(tool: ModuleType, tmp_path: Path) -> None:
    gaia, run, box = fields(tool, tmp_path)
    calls: list[list[str]] = []

    def runner(command: list[str]) -> object:
        calls.append(command)
        if command[1] == "archive":
            out = {"rows": 200000, "stored_bytes": 9_000_000, "row_cap_reached": len(calls) == 1}
            return tool.Outcome(0, json.dumps(out), "", 8.5, 512_000_000)
        return tool.Outcome(1, "", "Traceback ...\nTransportError: HTTP 504\n", 61.0, None)

    describe = lambda _s: ("gaia:DR3", ("source_id", "ra", "dec"))  # noqa: E731
    lines = tool.run_fields(
        [gaia, run, box], repeat=2, parallel=2, runner=runner, commands=lambda f: tool.command_for(f, describe)
    )
    assert [(line["label"], line["pass"]) for line in lines] == [
        ("pleiades-gaia", 1), ("pleiades", 1), ("bad-box", 1),
        ("pleiades-gaia", 2), ("pleiades", 2), ("bad-box", 2),
    ]  # fmt: skip
    assert len(calls) == 4  # the box never runs
    first = lines[0]
    assert (first["rows"], first["stored_bytes"], first["seconds"], first["peak_memory_bytes"]) == (
        200000, 9_000_000, 8.5, 512_000_000
    )  # fmt: skip
    assert first["row_cap_reached"] == "gaia" and lines[3]["row_cap_reached"] == ""
    assert lines[1]["exit_status"] == 1 and lines[1]["error"] == "TransportError: HTTP 504"
    assert lines[2]["exit_status"] == 2 and "takes a cone" in lines[2]["error"]

    report = tool.write_report(lines, tmp_path / "reports" / "stress.csv")
    with report.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert list(rows[0]) == list(tool.REPORT_COLUMNS)
    assert rows[1]["peak_memory_bytes"] == "" and rows[0]["rows"] == "200000"


def test_a_run_summary_sums_the_stage_bytes(tool: ModuleType, tmp_path: Path) -> None:
    _, run, _ = fields(tool, tmp_path)
    doc = {
        "objects_examined": 412,
        "row_cap_reached": {"gaia": 5000},
        "timing": {"identify": {"stored_bytes": 100}, "retrieve": {"stored_bytes": 2000}, "test": {}},
    }
    line = tool.summarise(run, 1, ["sdp", "investigate"], tool.Outcome(0, json.dumps(doc), "", 3.0, None))
    assert (line["rows"], line["stored_bytes"], line["row_cap_reached"]) == (412, 2100, "gaia")


def test_a_real_command_is_timed_and_its_output_kept(tool: ModuleType) -> None:
    outcome = tool.run_command([sys.executable, "-c", "import json; print(json.dumps({'rows': 3}))"])
    assert outcome.exit_status == 0 and json.loads(outcome.stdout) == {"rows": 3}
    assert outcome.seconds > 0
    assert outcome.peak_memory_bytes is None or outcome.peak_memory_bytes >= 0

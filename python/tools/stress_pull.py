"""Run a list of fields through SDP one after another and write a CSV of rows, bytes, time and memory (#60).

    cd python
    uv run python tools/stress_pull.py fields.csv                 # each field once
    uv run python tools/stress_pull.py fields.csv --repeat 2      # the second pass times reuse of the cache
    uv run python tools/stress_pull.py fields.csv --parallel 3    # three fields at the same time

In the SDP Command Prompt, ``python tools\\stress_pull.py fields.csv`` does the same. The fields file is a
CSV with a header row, in either shape (both may be mixed, blank cells are fine):

    label,region,profile                      a run: sdp investigate --region ... --profile ...
    pleiades,"icrs;circle(56.75,24.12,1.0)",../profiles/moving-group-search.yaml

    label,source,region,row_cap,columns       an archive read: sdp archive acquire (cones only)
    pleiades-gaia,gaia,"icrs;circle(56.75,24.12,1.0)",200000,

``columns`` is space-separated native column names; blank reads every column the source declares.
The report goes to ``reports/stress/<date>-<time>.csv`` (or ``--out``) with one line per run: the field,
pass, command, exit status, rows, bytes stored, seconds, peak memory of the run (blank without psutil),
whether a row cap was reached, and the error text. The exit status is 1 if any run failed.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PYTHON = Path(__file__).resolve().parents[1]
REPO = PYTHON.parent
REPORTS = REPO / "reports" / "stress"
#: The venv's own tools, so it works the same under ``uv run`` on Linux, macOS and Windows.
BIN = Path(sys.executable).parent
CONE = re.compile(r"^\s*(?:icrs;)?\s*circle\(\s*([-+\d.eE]+)\s*,\s*([-+\d.eE]+)\s*,\s*([-+\d.eE]+)\s*\)\s*$")
REPORT_COLUMNS = (
    "label",
    "pass",
    "kind",
    "region",
    "exit_status",
    "rows",
    "stored_bytes",
    "seconds",
    "peak_memory_bytes",
    "row_cap_reached",
    "error",
    "command",
)


@dataclass(frozen=True)
class FieldSpec:
    label: str
    region: str
    profile: str = ""
    source: str = ""
    row_cap: int | None = None
    columns: tuple[str, ...] = ()

    @property
    def kind(self) -> str:
        return "investigate" if self.profile else "acquire"


@dataclass
class Outcome:
    exit_status: int
    stdout: str
    stderr: str
    seconds: float
    peak_memory_bytes: int | None


Runner = Callable[[list[str]], Outcome]


def read_fields(path: Path) -> list[FieldSpec]:
    """The fields file: each row is a run (``profile``) or an archive read (``source``)."""
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    fields = []
    for number, row in enumerate(rows, start=2):
        cell = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
        label, region = cell.get("label", ""), cell.get("region", "")
        if not region:
            raise ValueError(f"{path} line {number}: no region")
        if not cell.get("profile") and not cell.get("source"):
            raise ValueError(f"{path} line {number}: give a profile (a run) or a source (an archive read)")
        fields.append(
            FieldSpec(
                label=label or f"field-{number - 1}",
                region=region,
                profile=cell.get("profile", ""),
                source=cell.get("source", ""),
                row_cap=int(cell["row_cap"]) if cell.get("row_cap") else None,
                columns=tuple(cell.get("columns", "").split()),
            )
        )
    return fields


def declared_source(source_id: str) -> tuple[str, tuple[str, ...]]:
    """A packaged source's release label and its declared columns."""
    from sdp.adapters.archives.tap import load_sources

    sources = load_sources()
    if source_id not in sources:
        raise ValueError(f"unknown source {source_id!r}; packaged: {', '.join(sorted(sources))}")
    descriptor = sources[source_id].descriptor
    return descriptor.release_label, descriptor.column_names()


def command_for(
    field: FieldSpec, describe: Callable[[str], tuple[str, tuple[str, ...]]] = declared_source
) -> list[str]:
    sdp = str(BIN / "sdp")
    if field.kind == "investigate":
        return [sdp, "investigate", "--region", field.region, "--profile", field.profile, "--label", field.label]
    cone = CONE.match(field.region)
    if cone is None:
        raise ValueError(f"{field.label}: an archive read takes a cone, icrs;circle(ra,dec,radius)")
    release, declared = describe(field.source)
    command = [sdp, "archive", "acquire", "--source", field.source, "--release", release]
    command += ["--ra", cone[1], "--dec", cone[2], "--radius", cone[3]]
    if field.row_cap is not None:
        command += ["--row-cap", str(field.row_cap)]
    for column in field.columns or declared:
        command += ["--column", column]
    return command


def run_command(command: list[str]) -> Outcome:
    """Run one command, sampling the resident memory of it and its children (psutil, when installed)."""
    try:
        import psutil  # type: ignore[import-untyped,unused-ignore]
    except ImportError:
        psutil = None
    started = time.monotonic()
    process = subprocess.Popen(
        command, cwd=PYTHON, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=os.environ.copy()
    )
    peak: list[int | None] = [None]
    done = threading.Event()

    def sample() -> None:
        if psutil is None:
            return
        peak[0] = 0
        try:
            watched = psutil.Process(process.pid)
            while not done.is_set():
                rss = watched.memory_info().rss
                rss += sum(c.memory_info().rss for c in watched.children(recursive=True))
                peak[0] = max(peak[0] or 0, rss)
                done.wait(0.2)
        except psutil.Error:  # the run finished between samples
            return

    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    stdout, stderr = process.communicate()
    done.set()
    sampler.join()
    return Outcome(
        process.returncode,
        stdout.decode("utf-8", "replace"),
        stderr.decode("utf-8", "replace"),
        time.monotonic() - started,
        peak[0],
    )


def summarise(field: FieldSpec, pass_number: int, command: list[str], outcome: Outcome) -> dict[str, Any]:
    """One report line from a run's JSON output: rows, bytes stored and any row cap reached."""
    rows = stored = None
    capped = ""
    error = ""
    if outcome.exit_status == 0:
        try:
            doc = json.loads(outcome.stdout)
        except json.JSONDecodeError:
            doc, error = {}, "the output was not JSON"
        if field.kind == "acquire":
            rows, stored = doc.get("rows"), doc.get("stored_bytes")
            capped = field.source if doc.get("row_cap_reached") else ""
        else:
            rows = doc.get("objects_examined")
            stored = sum(int(t.get("stored_bytes") or 0) for t in doc.get("timing", {}).values())
            capped = " ".join(sorted(doc.get("row_cap_reached", {})))
    else:
        lines = [line for line in outcome.stderr.strip().splitlines() if line.strip()]
        error = lines[-1] if lines else f"exit status {outcome.exit_status}"
    return {
        "label": field.label,
        "pass": pass_number,
        "kind": field.kind,
        "region": field.region,
        "exit_status": outcome.exit_status,
        "rows": rows,
        "stored_bytes": stored,
        "seconds": round(outcome.seconds, 2),
        "peak_memory_bytes": outcome.peak_memory_bytes,
        "row_cap_reached": capped,
        "error": error,
        "command": " ".join(command[1:]),
    }


def run_fields(
    fields: Sequence[FieldSpec],
    *,
    repeat: int = 1,
    parallel: int = 1,
    runner: Runner = run_command,
    commands: Callable[[FieldSpec], list[str]] = command_for,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    """Every field, ``repeat`` passes, ``parallel`` at a time; a pass starts when the one before ends,
    so the second pass of a field always finds the first one's data stored."""
    lines: list[dict[str, Any]] = []
    lock = threading.Lock()

    def one(field: FieldSpec, pass_number: int) -> dict[str, Any]:
        try:
            command = commands(field)
        except ValueError as exc:
            line = summarise(field, pass_number, [""], Outcome(2, "", str(exc), 0.0, None))
        else:
            line = summarise(field, pass_number, command, runner(command))
        with lock:
            lines.append(line)
            if progress is not None:
                progress(line)
        return line

    for pass_number in range(1, repeat + 1):
        with ThreadPoolExecutor(max_workers=max(1, parallel)) as pool:
            list(pool.map(one, fields, [pass_number] * len(fields)))
    order = {f.label: i for i, f in enumerate(fields)}
    return sorted(lines, key=lambda line: (line["pass"], order.get(line["label"], 0)))


def write_report(lines: Sequence[dict[str, Any]], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=REPORT_COLUMNS)
        writer.writeheader()
        for line in lines:
            writer.writerow({k: "" if line.get(k) is None else line[k] for k in REPORT_COLUMNS})
    return path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("fields", type=Path, help="CSV of fields: label,region,profile or label,source,region,...")
    parser.add_argument("--repeat", type=int, default=1, help="Passes over the list (2 times cache reuse)")
    parser.add_argument("--parallel", type=int, default=1, help="Fields run at the same time")
    parser.add_argument("--out", type=Path, help="Report path; default reports/stress/<date>-<time>.csv")
    args = parser.parse_args(argv)
    fields = read_fields(args.fields)
    out = args.out or REPORTS / f"{datetime.now(UTC):%Y-%m-%d-%H%M%S}.csv"

    def show(line: dict[str, Any]) -> None:
        state = "ok" if line["exit_status"] == 0 else f"FAILED: {line['error']}"
        print(f"{line['label']} pass {line['pass']}: {line['rows']} rows, {line['seconds']} s, {state}", flush=True)

    lines = run_fields(fields, repeat=args.repeat, parallel=args.parallel, progress=show)
    print(f"report: {write_report(lines, out)}")
    return 0 if all(line["exit_status"] == 0 for line in lines) else 1


if __name__ == "__main__":
    sys.exit(main())

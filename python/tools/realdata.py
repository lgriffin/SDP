"""The real-data ratchet (TST-012): measured values from real data against the marks in realdata/baselines.json.

A mark says which way is better (``higher``, ``lower`` or ``exact``), where its value came from, and when.
``check`` fails when any measured value is worse than its mark (or, for ``exact``, different). ``update``
moves a mark only in the better direction, like the mutation high-water marks: a mark is never lowered and
never written by hand. A mark with no value yet is seeded by the first ``update`` that measures it.

    uv run python tools/realdata.py check  measured.json [--baselines ../realdata/baselines.json]
    uv run python tools/realdata.py update measured.json --source "<what ran, where>"
    uv run python tools/realdata.py measure measured.json --sweep <tiles dir> --field <field.csv> --screen <summary.json>

``measured.json`` is ``{"<metric>": <number>, ...}``, written by harness/run-realdata.ps1 or by hand from a
run's own summary. A metric measured but not in the baselines is reported and ignored; a metric in the
baselines but not measured is reported as not checked.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

BASELINES = Path(__file__).resolve().parents[2] / "realdata" / "baselines.json"
DIRECTIONS = ("higher", "lower", "exact")


@dataclass(frozen=True)
class Outcome:
    metric: str
    status: str  # "ok", "better", "worse", "unseeded", "not-measured", "unknown"
    mark: float | None
    measured: float | None

    @property
    def failed(self) -> bool:
        return self.status == "worse"

    def line(self) -> str:
        def show(x: float | None) -> str:
            return "-" if x is None else f"{x:g}"

        return f"{self.status:<12} {self.metric}: mark {show(self.mark)}, measured {show(self.measured)}"


def load(path: Path) -> dict[str, Any]:
    doc: dict[str, Any] = json.loads(path.read_text("utf-8"))
    for name, mark in doc["marks"].items():
        if mark.get("better") not in DIRECTIONS:
            raise ValueError(f"{name}: 'better' must be one of {DIRECTIONS}")
        if mark.get("value") is not None and not math.isfinite(float(mark["value"])):
            raise ValueError(f"{name}: value must be a finite number or null")
    return doc


def judge(mark: dict[str, Any], measured: float) -> str:
    """How one measured value stands against its mark."""
    value = mark.get("value")
    if value is None:
        return "unseeded"
    tol = float(mark.get("tolerance", 0.0))
    value = float(value)
    better = mark["better"]
    if better == "exact":
        return "ok" if abs(measured - value) <= tol else "worse"
    gain = measured - value if better == "higher" else value - measured
    if gain < -tol:
        return "worse"
    return "better" if gain > tol else "ok"


def compare(doc: dict[str, Any], measured: dict[str, float]) -> list[Outcome]:
    out: list[Outcome] = []
    for name, mark in sorted(doc["marks"].items()):
        mv = mark.get("value")
        if name not in measured:
            out.append(Outcome(name, "not-measured", mv, None))
            continue
        x = float(measured[name])
        if not math.isfinite(x):
            out.append(Outcome(name, "worse", mv, x))
            continue
        out.append(Outcome(name, judge(mark, x), mv, x))
    out.extend(
        Outcome(name, "unknown", None, float(measured[name])) for name in sorted(set(measured) - set(doc["marks"]))
    )
    return out


def update(doc: dict[str, Any], measured: dict[str, float], source: str, today: str) -> list[str]:
    """Move marks that improved, and seed unseeded ones; never lower one. Returns the metrics moved."""
    moved: list[str] = []
    for o in compare(doc, measured):
        if o.status in ("better", "unseeded") and o.measured is not None:
            mark = doc["marks"][o.metric]
            mark.update(value=o.measured, source=source, measured_on=today)
            moved.append(o.metric)
    return moved


def measure_sweep(tiles: Path) -> dict[str, float]:
    """The sweep's counts from its tile files, the way `sdp discover report` sums them."""
    from sdp_stellar.sweep import summarise

    results = [json.loads(p.read_text("utf-8")) for p in sorted(tiles.glob("*b[0-9][0-9][0-9]-t*.json"))]
    if not results:
        raise SystemExit(f"no swept tiles in {tiles}")
    s = summarise(results)
    out = {f"sweep.{k}": float(n) for k, n in s["candidates"].items()}
    out.setdefault("sweep.known", 0.0)
    if s["null_false_positive_rate"] is not None:
        out["sweep.null_false_positive_rate"] = float(s["null_false_positive_rate"])
    return out


def measure_field(field_csv: Path) -> dict[str, float]:
    """Counts of the field list `sdp companions field` writes."""
    with field_csv.open(newline="", encoding="utf-8") as handle:
        verdicts = [row["verdict"] for row in csv.DictReader(handle)]
    return {
        "companions.field_dr3.compact": float(verdicts.count("neutron-star-or-black-hole")),
        "companions.field_dr3.dark_partner": float(verdicts.count("dark-partner")),
    }


def measure_screen(summary_json: Path) -> dict[str, float]:
    """From the summary.json `sdp companions screen` writes."""
    s = json.loads(summary_json.read_text("utf-8"))
    return {
        "companions.dr3_screen.clusters_screened": float(s["clusters_screened"]),
        "companions.dr3_screen.member_dark_partners": float(s["flagged_in_clusters"]),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=("check", "update", "measure"))
    parser.add_argument("measured", type=Path)
    parser.add_argument("--sweep", type=Path, help="measure: a sweep's tiles folder")
    parser.add_argument("--field", type=Path, help="measure: the field.csv of `sdp companions field`")
    parser.add_argument("--screen", type=Path, help="measure: the summary.json of `sdp companions screen`")
    parser.add_argument("--baselines", type=Path, default=BASELINES)
    parser.add_argument("--source", default="", help="update: what ran, on which commit and machine")
    args = parser.parse_args(argv)

    if args.action == "measure":
        measured_now: dict[str, float] = {}
        for path, fn in ((args.sweep, measure_sweep), (args.field, measure_field), (args.screen, measure_screen)):
            if path is not None:
                measured_now.update(fn(path))
        args.measured.write_text(json.dumps(measured_now, indent=2, sort_keys=True) + "\n", "utf-8")
        print(f"{len(measured_now)} value(s) -> {args.measured}")
        return 0

    doc = load(args.baselines)
    measured = {k: float(v) for k, v in json.loads(args.measured.read_text("utf-8")).items()}
    outcomes = compare(doc, measured)
    for o in outcomes:
        print(o.line())
    worse = [o.metric for o in outcomes if o.failed]
    if worse:
        print(f"FAILED: {len(worse)} real-data value(s) worse than the mark: {', '.join(worse)}")
        return 1
    if args.action == "update":
        if not args.source:
            parser.error("update needs --source")
        moved = update(doc, measured, args.source, date.today().isoformat())
        args.baselines.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", "utf-8")
        print(f"moved {len(moved)} mark(s): {', '.join(moved) or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

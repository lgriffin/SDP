"""Synthetic fields with planted clusters, and synthetic benchmark tables. Recovery fixtures for the
module's plumbing and scientific tests; they are not Gaia data and their metadata says so.

    python -m sdp_stellar.synthetic <out-dir> [--seed 7] [--stars 1500]

writes ``field-<target>.parquet`` per predeclared region and ``benchmark-<id>.parquet`` per benchmark.
"""

from __future__ import annotations

import argparse
import io
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from sdp_stellar.benchmark import with_metadata

RELEASE = "synthetic:fields-v1"
#: projected core radius of a planted cluster; small enough that a 100-member centre lands well inside
#: the predeclared 0.1 deg match radius even at Pleiades distance
CORE_PC = 1.0

SCHEMA = pa.schema(
    [
        ("source_key", pa.string()),
        ("ra_deg", pa.float64()),
        ("dec_deg", pa.float64()),
        ("epoch_jyear", pa.float64()),
        ("parallax_mas", pa.float64()),
        ("parallax_mas_err", pa.float64()),
        ("pm_ra_cosdec_masyr", pa.float64()),
        ("pm_ra_cosdec_masyr_err", pa.float64()),
        ("pm_dec_masyr", pa.float64()),
        ("pm_dec_masyr_err", pa.float64()),
        ("astrometric_corr", pa.list_(pa.float64())),
        ("g_mag", pa.float64()),
        ("bp_rp", pa.float64()),
        ("ruwe", pa.float64()),
        ("quality_flags", pa.string()),
        ("quality_class", pa.string()),
        ("zero_point_corrected", pa.bool_()),
        ("source_release", pa.string()),
    ]
)


@dataclass(frozen=True)
class PlantedCluster:
    name: str
    target_id: str
    #: offset of the centre from the region centre, degrees
    d_ra_deg: float
    d_dec_deg: float
    pm: tuple[float, float]
    distance_pc: float
    members: int
    benchmarks: tuple[str, ...]


PLANTED: tuple[PlantedCluster, ...] = (
    PlantedCluster("SYN-A", "pleiades", 0.2, -0.1, (20.0, -45.0), 135.0, 120, ("hr23", "cga20")),
    PlantedCluster("SYN-B", "praesepe", -0.4, 0.3, (-36.0, -13.0), 185.0, 80, ()),
    PlantedCluster("SYN-C", "h-chi-persei", 0.1, 0.2, (-0.7, -1.1), 2300.0, 90, ("hr23",)),
)


def _region(target: Mapping[str, Any]) -> dict[str, float]:
    s = target["spec"]
    return {"ra_deg": float(s["ra_deg"]), "dec_deg": float(s["dec_deg"]), "radius_deg": float(s["radius_deg"])}


def _cone(rng: np.random.Generator, region: Mapping[str, float], n: int) -> tuple[np.ndarray, np.ndarray]:
    r = region["radius_deg"] * np.sqrt(rng.uniform(size=n))
    theta = rng.uniform(0, 2 * np.pi, n)
    dec = region["dec_deg"] + r * np.sin(theta)
    ra = (region["ra_deg"] + r * np.cos(theta) / np.cos(np.radians(dec))) % 360.0
    return ra, dec


def _rows(
    rng: np.random.Generator,
    key: str,
    ra: np.ndarray,
    dec: np.ndarray,
    pmra: np.ndarray,
    pmdec: np.ndarray,
    plx_true: np.ndarray,
    g: np.ndarray,
    bp_rp: np.ndarray,
) -> list[dict[str, Any]]:
    n = len(ra)
    plx_err = 0.015 + 0.02 * 10 ** (0.2 * (g - 15.0))
    pm_err = 1.5 * plx_err
    ruwe = np.abs(rng.normal(1.0, 0.12, n)) + np.where(rng.uniform(size=n) < 0.04, 1.0, 0.0)
    solved = np.where(rng.uniform(size=n) < 0.9, 31, 95)
    out = []
    for i in range(n):
        out.append(
            {
                "source_key": f"{key}:{i}",
                "ra_deg": float(ra[i]),
                "dec_deg": float(dec[i]),
                "epoch_jyear": 2016.0,
                "parallax_mas": float(plx_true[i] + rng.normal(0, plx_err[i])),
                "parallax_mas_err": float(plx_err[i]),
                "pm_ra_cosdec_masyr": float(pmra[i] + rng.normal(0, pm_err[i])),
                "pm_ra_cosdec_masyr_err": float(pm_err[i]),
                "pm_dec_masyr": float(pmdec[i] + rng.normal(0, pm_err[i])),
                "pm_dec_masyr_err": float(pm_err[i]),
                "astrometric_corr": None,
                "g_mag": float(g[i]),
                "bp_rp": float(bp_rp[i]),
                "ruwe": float(ruwe[i]),
                "quality_flags": json.dumps(
                    {"astrometric_params_solved": int(solved[i]), "ruwe": float(ruwe[i])}, sort_keys=True
                ),
                "quality_class": "good" if ruwe[i] < 1.4 else "poor",
                "zero_point_corrected": False,
                "source_release": RELEASE,
            }
        )
    return out


def make_field(
    target: Mapping[str, Any], seed: int, stars: int = 1500, planted: Sequence[PlantedCluster] = PLANTED
) -> pa.Table:
    region = _region(target)
    tid = str(target["target_id"])
    rng = np.random.default_rng([seed, sum(map(ord, tid))])
    ra, dec = _cone(rng, region, stars)
    plx = np.clip(rng.lognormal(np.log(0.9), 0.6, stars), 0.15, 12.0)
    # the field covers the planted clusters' magnitudes, so no magnitude bin holds only cluster members
    g = rng.uniform(6.0, 18.5, stars)
    rows = _rows(
        rng,
        f"synthetic:{tid}:field",
        ra,
        dec,
        rng.normal(2.0, 7.0, stars),
        rng.normal(-3.0, 7.0, stars),
        plx,
        g,
        rng.uniform(0.3, 2.6, stars),
    )
    for c in (p for p in planted if p.target_id == tid):
        core = np.degrees(CORE_PC / c.distance_pc)
        dec0 = region["dec_deg"] + c.d_dec_deg
        ra0 = region["ra_deg"] + c.d_ra_deg / np.cos(np.radians(dec0))
        n = c.members
        cdec = dec0 + rng.normal(0, core, n)
        cra = (ra0 + rng.normal(0, core, n) / np.cos(np.radians(dec0))) % 360.0
        abs_g = rng.uniform(0.5, 8.0, n)
        colour = 0.35 + 0.28 * (abs_g - 0.5) + rng.normal(0, 0.03, n)
        app_g = abs_g + 5 * np.log10(c.distance_pc / 10.0)
        disp = 0.3 if c.distance_pc < 500 else 0.08
        rows += _rows(
            rng,
            f"synthetic:{tid}:{c.name}",
            cra,
            cdec,
            c.pm[0] + rng.normal(0, disp, n),
            c.pm[1] + rng.normal(0, disp, n),
            np.full(n, 1000.0 / c.distance_pc),
            app_g,
            colour,
        )
    table = pa.Table.from_pylist(rows, schema=SCHEMA)
    return table.replace_schema_metadata(
        {"sdp.synthetic": "Synthetic field with planted clusters; not Gaia data.", "sdp.seed": str(seed)}
    )


def make_benchmark(
    benchmark_id: str, targets: Sequence[Mapping[str, Any]], release: str, planted: Sequence[PlantedCluster] = PLANTED
) -> pa.Table:
    by_target = {str(t["target_id"]): _region(t) for t in targets}
    rows = []
    for c in planted:
        if benchmark_id not in c.benchmarks or c.target_id not in by_target:
            continue
        region = by_target[c.target_id]
        dec0 = region["dec_deg"] + c.d_dec_deg
        rows.append(
            {
                "name": c.name,
                "ra_deg": float((region["ra_deg"] + c.d_ra_deg / math.cos(math.radians(dec0))) % 360.0),
                "dec_deg": float(dec0),
                "pm_ra_cosdec_masyr": c.pm[0],
                "pm_dec_masyr": c.pm[1],
                "parallax_mas": 1000.0 / c.distance_pc,
            }
        )
    schema = pa.schema(
        [("name", pa.string())]
        + [(k, pa.float64()) for k in ("ra_deg", "dec_deg", "pm_ra_cosdec_masyr", "pm_dec_masyr", "parallax_mas")]
    )
    table = pa.Table.from_pylist(rows, schema=schema)
    return with_metadata(
        table, release, "CC0-1.0 (synthetic)", f"SDP synthetic benchmark {benchmark_id}; not the published catalogue"
    )


def parquet(table: pa.Table) -> bytes:
    sink = io.BytesIO()
    pq.write_table(table, sink)
    return sink.getvalue()


def main(argv: list[str] | None = None) -> None:
    from sdp_stellar.protocol import load_predeclaration

    parser = argparse.ArgumentParser(prog="python -m sdp_stellar.synthetic")
    parser.add_argument("out", type=Path)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--stars", type=int, default=1500)
    args = parser.parse_args(argv)
    pre = load_predeclaration()
    targets = list(pre["targets"])
    args.out.mkdir(parents=True, exist_ok=True)
    for t in targets:
        (args.out / f"field-{t['target_id']}.parquet").write_bytes(parquet(make_field(t, args.seed, args.stars)))
    for label in pre["parameters"]["benchmarks"]:
        bid = label.split(":")[0]
        (args.out / f"benchmark-{bid}.parquet").write_bytes(parquet(make_benchmark(bid, targets, label)))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()

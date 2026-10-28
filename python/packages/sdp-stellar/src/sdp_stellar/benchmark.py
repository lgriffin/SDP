"""BenchmarkCrossMatch: candidates against registered benchmark catalogues (STL-002, STL-005, STL-G-06).

A benchmark input is a Parquet table of clusters (name, ra_deg, dec_deg, pm_ra_cosdec_masyr,
pm_dec_masyr, parallax_mas) whose schema metadata names its release, licence and citation. A
candidate matches a benchmark cluster when their centres are within the predeclared radius and
their mean proper motions within the predeclared window. This compares a handful of cluster
centres with a proper-motion window, which the positional CrossMatchService does not express, so
it is computed here with astropy (deviation D14).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import astropy.units as u
import numpy as np
import pyarrow as pa
from astropy.coordinates import SkyCoord

from sdp_stellar.quality import column

META = ("release", "licence", "citation")
REQUIRED_COLUMNS = ("name", "ra_deg", "dec_deg", "pm_ra_cosdec_masyr", "pm_dec_masyr")


@dataclass(frozen=True)
class Benchmark:
    benchmark_id: str
    release: str
    licence: str
    citation: str
    table: pa.Table

    def problems(self, expected_release: str) -> list[str]:
        out = [f"{self.benchmark_id}: no {k}" for k in META if not getattr(self, k).strip()]
        if self.release != expected_release:
            out.append(
                f"{self.benchmark_id}: release {self.release or '(none)'} is not the predeclared {expected_release}"
            )
        missing = self.missing_columns()
        if missing:
            out.append(f"{self.benchmark_id}: table lacks {missing}")
        return out

    def missing_columns(self) -> list[str]:
        return sorted(set(REQUIRED_COLUMNS) - set(self.table.column_names))


def benchmark_from(benchmark_id: str, table: pa.Table) -> Benchmark:
    meta = {k.decode(): v.decode() for k, v in (table.schema.metadata or {}).items()}
    return Benchmark(
        benchmark_id,
        meta.get("sdp.benchmark.release", ""),
        meta.get("sdp.benchmark.licence", ""),
        meta.get("sdp.benchmark.citation", ""),
        table,
    )


def with_metadata(table: pa.Table, release: str, licence: str, citation: str) -> pa.Table:
    return table.replace_schema_metadata(
        {"sdp.benchmark.release": release, "sdp.benchmark.licence": licence, "sdp.benchmark.citation": citation}
    )


def centre(table: pa.Table, rows: np.ndarray) -> dict[str, float]:
    ra = np.radians(column(table, "ra_deg")[rows])
    dec = np.radians(column(table, "dec_deg")[rows])
    x, y, z = np.mean(np.cos(dec) * np.cos(ra)), np.mean(np.cos(dec) * np.sin(ra)), np.mean(np.sin(dec))
    return {
        "ra_deg": float(np.degrees(np.arctan2(y, x)) % 360.0),
        "dec_deg": float(np.degrees(np.arctan2(z, np.hypot(x, y)))),
        "pm_ra_cosdec_masyr": float(np.median(column(table, "pm_ra_cosdec_masyr")[rows])),
        "pm_dec_masyr": float(np.median(column(table, "pm_dec_masyr")[rows])),
        "parallax_mas": float(np.median(column(table, "parallax_mas")[rows])),
    }


def cross_match(
    c: Mapping[str, float], benchmark: Benchmark, radius_deg: float, pm_window_masyr: float
) -> dict[str, Any]:
    t = benchmark.table
    if t.num_rows == 0:
        return {"benchmark": benchmark.benchmark_id, "release": benchmark.release, "matches": []}
    here = SkyCoord(c["ra_deg"] * u.deg, c["dec_deg"] * u.deg)
    there = SkyCoord(column(t, "ra_deg") * u.deg, column(t, "dec_deg") * u.deg)
    sep = here.separation(there).deg
    dpm = np.hypot(
        column(t, "pm_ra_cosdec_masyr") - c["pm_ra_cosdec_masyr"], column(t, "pm_dec_masyr") - c["pm_dec_masyr"]
    )
    hit = (sep <= radius_deg) & (dpm <= pm_window_masyr)
    names = t.column("name").to_pylist()
    matches = [
        {"name": names[i], "separation_deg": float(sep[i]), "pm_offset_masyr": float(dpm[i])}
        for i in sorted(np.nonzero(hit)[0].tolist(), key=lambda i: (sep[i], names[i]))
    ]
    return {"benchmark": benchmark.benchmark_id, "release": benchmark.release, "matches": matches}


def recovered(
    benchmark: Benchmark,
    centres: list[Mapping[str, float]],
    region: Mapping[str, float],
    radius_deg: float,
    pm_window_masyr: float,
) -> dict[str, bool]:
    """Which benchmark clusters inside the region footprint some candidate matches."""
    t = benchmark.table
    names = t.column("name").to_pylist()
    rc = SkyCoord(region["ra_deg"] * u.deg, region["dec_deg"] * u.deg)
    inside = (
        rc.separation(SkyCoord(column(t, "ra_deg") * u.deg, column(t, "dec_deg") * u.deg)).deg <= region["radius_deg"]
    )
    out: dict[str, bool] = {}
    for i in np.nonzero(inside)[0].tolist():
        single = Benchmark(
            benchmark.benchmark_id, benchmark.release, benchmark.licence, benchmark.citation, t.slice(i, 1)
        )
        out[names[i]] = any(cross_match(c, single, radius_deg, pm_window_masyr)["matches"] for c in centres)
    return out

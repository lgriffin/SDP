"""Canonical product formats: the small CSV files product dialects write and Investigate tests read.

- ``lightcurve``: ``mjd,mag,mag_err`` (one row per epoch, magnitudes in one band)
- ``astrometry``: ``epoch_jyear,ra_deg,dec_deg,pos_err_mas`` (one row per epoch, ICRS)
"""

from __future__ import annotations

import csv
import io
import math
from dataclasses import dataclass

import numpy as np

LIGHTCURVE = "lightcurve"
ASTROMETRY = "astrometry"


@dataclass(frozen=True)
class LightCurve:
    mjd: np.ndarray
    mag: np.ndarray
    mag_err: np.ndarray


@dataclass(frozen=True)
class PositionHistory:
    epoch_jyear: np.ndarray
    ra_deg: np.ndarray
    dec_deg: np.ndarray
    pos_err_mas: np.ndarray


def _rows(data: bytes, columns: tuple[str, ...]) -> list[dict[str, float]]:
    reader = csv.DictReader(io.StringIO(data.decode("utf-8")))
    missing = [c for c in columns if c not in (reader.fieldnames or [])]
    if missing:
        raise ValueError(f"product lacks columns {missing}")
    out = []
    for row in reader:
        values = {c: float(row[c]) for c in columns}
        if all(math.isfinite(v) for v in values.values()):
            out.append(values)
    return out


def parse_lightcurve(data: bytes) -> LightCurve:
    rows = _rows(data, ("mjd", "mag", "mag_err"))
    rows.sort(key=lambda r: r["mjd"])
    return LightCurve(
        np.array([r["mjd"] for r in rows]), np.array([r["mag"] for r in rows]), np.array([r["mag_err"] for r in rows])
    )


def parse_positions(data: bytes) -> PositionHistory:
    rows = _rows(data, ("epoch_jyear", "ra_deg", "dec_deg", "pos_err_mas"))
    rows.sort(key=lambda r: r["epoch_jyear"])
    return PositionHistory(
        np.array([r["epoch_jyear"] for r in rows]),
        np.array([r["ra_deg"] for r in rows]),
        np.array([r["dec_deg"] for r in rows]),
        np.array([r["pos_err_mas"] for r in rows]),
    )


def lightcurve_csv(mjd: np.ndarray, mag: np.ndarray, mag_err: np.ndarray) -> bytes:
    lines = [
        "mjd,mag,mag_err",
        *(f"{float(a)!r},{float(b)!r},{float(c)!r}" for a, b, c in zip(mjd, mag, mag_err, strict=True)),
    ]
    return ("\n".join(lines) + "\n").encode()


def positions_csv(epoch: np.ndarray, ra: np.ndarray, dec: np.ndarray, err_mas: np.ndarray) -> bytes:
    lines = [
        "epoch_jyear,ra_deg,dec_deg,pos_err_mas",
        *(
            f"{float(a)!r},{float(b)!r},{float(c)!r},{float(d)!r}"
            for a, b, c, d in zip(epoch, ra, dec, err_mas, strict=True)
        ),
    ]
    return ("\n".join(lines) + "\n").encode()

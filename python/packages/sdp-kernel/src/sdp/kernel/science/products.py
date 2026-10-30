"""Canonical product formats: the small CSV files product dialects write and Investigate tests read.

- ``lightcurve``: ``mjd,mag,mag_err`` (one row per epoch, magnitudes in one band), optionally followed
  by ``ra_deg,dec_deg,ra_err_arcsec,dec_err_arcsec,beam_arcsec``: where each exposure saw the source
  (ICRS, its 1-sigma errors) and the instrument's resolution, from an archive that measures a position
  at every epoch (WISE), so a test can reconcile the exposures with Gaia
- ``astrometry``: ``epoch_jyear,ra_deg,dec_deg,pos_err_mas`` (one row per epoch, ICRS)

Lines starting with ``#`` are comments; the Retrieve stage stamps one naming the dialect and raw
parent, so identical rows curated from different raw products stay distinct artifacts.
"""

from __future__ import annotations

import csv
import math
from collections.abc import Sequence
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
class Exposures:
    """Where each epoch of a light curve saw its source, in time order."""

    mjd: np.ndarray
    ra_deg: np.ndarray
    dec_deg: np.ndarray
    ra_err_arcsec: np.ndarray
    dec_err_arcsec: np.ndarray
    beam_arcsec: np.ndarray


EXPOSURE_COLUMNS = ("ra_deg", "dec_deg", "ra_err_arcsec", "dec_err_arcsec", "beam_arcsec")


@dataclass(frozen=True)
class PositionHistory:
    epoch_jyear: np.ndarray
    ra_deg: np.ndarray
    dec_deg: np.ndarray
    pos_err_mas: np.ndarray


def _rows(data: bytes, columns: tuple[str, ...]) -> list[dict[str, float]]:
    lines = [line for line in data.decode().splitlines() if not line.startswith("#")]
    reader = csv.DictReader(lines)
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


def parse_exposures(data: bytes) -> Exposures | None:
    """The light curve's per-epoch positions, or None for a light curve that has none."""
    header = next((line for line in data.decode().splitlines() if not line.startswith("#")), "")
    if not set(EXPOSURE_COLUMNS) <= {c.strip() for c in header.split(",")}:
        return None
    rows = _rows(data, ("mjd", *EXPOSURE_COLUMNS))
    rows.sort(key=lambda r: r["mjd"])
    return Exposures(*(np.array([r[c] for r in rows]) for c in ("mjd", *EXPOSURE_COLUMNS)))


def parse_positions(data: bytes) -> PositionHistory:
    rows = _rows(data, ("epoch_jyear", "ra_deg", "dec_deg", "pos_err_mas"))
    rows.sort(key=lambda r: r["epoch_jyear"])
    return PositionHistory(
        np.array([r["epoch_jyear"] for r in rows]),
        np.array([r["ra_deg"] for r in rows]),
        np.array([r["dec_deg"] for r in rows]),
        np.array([r["pos_err_mas"] for r in rows]),
    )


def lightcurve_csv(
    mjd: np.ndarray, mag: np.ndarray, mag_err: np.ndarray, exposures: Sequence[np.ndarray] | None = None
) -> bytes:
    """The canonical light curve; ``exposures`` adds each epoch's position, as the ``EXPOSURE_COLUMNS``."""
    columns = [mjd, mag, mag_err, *(exposures or ())]
    if exposures is not None and len(exposures) != len(EXPOSURE_COLUMNS):
        raise ValueError(f"exposures are the columns {EXPOSURE_COLUMNS}")
    names = ["mjd", "mag", "mag_err", *(EXPOSURE_COLUMNS if exposures is not None else ())]
    lines = [",".join(names), *(",".join(f"{float(v)!r}" for v in row) for row in zip(*columns, strict=True))]
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

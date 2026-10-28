"""FeatureSpacePolicy: versioned maps from canonical astrometry to a feature matrix (STL-002).

``fsp-v1``: gnomonic projection of positions about the region centre; proper motions; parallax.
Each pair (the two positions, the two proper motions) is divided by one shared robust scale and
parallax by its own, so the matrix is dimensionless, a rigid rotation of the field only rotates
the position pair, and a change of proper-motion units leaves it unchanged. Clustering accepts a
``FeatureMatrix``, never raw columns: five raw Euclidean axes never reach a gate.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pyarrow as pa

from sdp_stellar.quality import column

MAD_TO_SIGMA = 1.4826


class PolicyError(ValueError):
    pass


@dataclass(frozen=True)
class FeatureMatrix:
    values: np.ndarray
    policy_id: str
    axes: tuple[str, ...]
    scales: tuple[float, ...]

    def __post_init__(self) -> None:
        if self.policy_id not in POLICIES:
            raise PolicyError(f"feature matrix from unregistered policy {self.policy_id!r}")
        if self.values.ndim != 2 or self.values.shape[1] != len(self.axes):
            raise PolicyError("feature matrix shape does not match its axes")


def tangent_plane(ra_deg: np.ndarray, dec_deg: np.ndarray, ra0: float, dec0: float) -> tuple[np.ndarray, np.ndarray]:
    """Gnomonic projection in degrees about (ra0, dec0)."""
    ra, dec, a0, d0 = (np.radians(v) for v in (ra_deg, dec_deg, ra0, dec0))
    cos_c = np.sin(d0) * np.sin(dec) + np.cos(d0) * np.cos(dec) * np.cos(ra - a0)
    xi = np.cos(dec) * np.sin(ra - a0) / cos_c
    eta = (np.cos(d0) * np.sin(dec) - np.sin(d0) * np.cos(dec) * np.cos(ra - a0)) / cos_c
    return np.degrees(xi), np.degrees(eta)


def _robust(values: np.ndarray) -> tuple[float, float]:
    centre = float(np.median(values))
    scale = MAD_TO_SIGMA * float(np.median(np.abs(values - centre)))
    if not np.isfinite(scale) or scale <= 0:
        raise PolicyError("a feature axis has no spread; the region is degenerate")
    return centre, scale


def _pair(a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    ca, cb = float(np.median(a)), float(np.median(b))
    r = np.hypot(a - ca, b - cb)
    scale = MAD_TO_SIGMA * float(np.median(r)) / np.sqrt(2 * np.log(2))  # median of a 2-D Gaussian radius
    if not np.isfinite(scale) or scale <= 0:
        raise PolicyError("a feature pair has no spread; the region is degenerate")
    return (a - ca) / scale, (b - cb) / scale, scale


def fsp_v1(table: pa.Table, ra0: float, dec0: float) -> FeatureMatrix:
    xi, eta = tangent_plane(column(table, "ra_deg"), column(table, "dec_deg"), ra0, dec0)
    pmra, pmdec = column(table, "pm_ra_cosdec_masyr"), column(table, "pm_dec_masyr")
    plx = column(table, "parallax_mas")
    if not np.all(np.isfinite(np.column_stack([xi, eta, pmra, pmdec, plx]))):
        raise PolicyError("fsp-v1 needs finite positions, proper motions and parallaxes; apply the quality cuts first")
    # Positions are not centred on their median: the region centre is the declared origin.
    r = np.hypot(xi, eta)
    pos_scale = MAD_TO_SIGMA * float(np.median(r)) / np.sqrt(2 * np.log(2))
    if pos_scale <= 0:
        raise PolicyError("positions have no spread")
    u, v, pm_scale = _pair(pmra, pmdec)
    p_centre, p_scale = _robust(plx)
    values = np.column_stack([xi / pos_scale, eta / pos_scale, u, v, (plx - p_centre) / p_scale])
    return FeatureMatrix(
        values,
        "fsp-v1",
        ("xi", "eta", "pm_ra_cosdec", "pm_dec", "parallax"),
        (pos_scale, pos_scale, pm_scale, pm_scale, p_scale),
    )


POLICIES: dict[str, Callable[[pa.Table, float, float], FeatureMatrix]] = {"fsp-v1": fsp_v1}


def apply_policy(policy_id: str, table: pa.Table, ra0: float, dec0: float) -> FeatureMatrix:
    if policy_id not in POLICIES:
        raise PolicyError(f"unknown feature-space policy {policy_id!r}; registered: {sorted(POLICIES)}")
    return POLICIES[policy_id](table, ra0, dec0)

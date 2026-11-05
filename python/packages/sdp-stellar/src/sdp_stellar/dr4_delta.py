"""The DR4 delta engine (Discovery Roadmap C5): what does a new Gaia release change for a named cluster set?

The predeclared questions, asked the same way of every cluster in the seed set (``dr4_delta.json``):

1. how many members the same selection finds in each release;
2. how far the members' mean parallax, and so the cluster's distance, moves;
3. how far their mean proper motion moves;
4. how many earlier members lose membership, and how many later members are new.

**The same selection in every release.** A star is a member when it lies within the cluster's tidal
radius ``rt`` of the Hunt & Reffert 2023 (HR23) centre, is brighter than ``g_max``, has a parallax
measured to ``parallax_over_error_min``, has a parallax within ``parallax_sigma`` of HR23's cluster
parallax (its own error and the cluster's spread added in quadrature), and has a proper motion whose
chi-squared from HR23's mean (errors and spreads in quadrature, two degrees of freedom) is at most
``pm_chi2_max`` (11.83, the 3-sigma level). The cluster parameters stay HR23's in every release, so a
change is the release's, not the selection's.

**Stars across releases.** A DR2 source is matched to DR3 through Gaia's own ``dr2_neighbourhood``
table (the nearest neighbour); DR3 to DR4 is by source_id until Gaia publishes a neighbourhood table.
An earlier member with no counterpart is counted as unmatched, not lost.

**Sanity check.** In DR3 the selection is compared with HR23's own members: the share of HR23 members
the selection finds, and the share of the selection HR23 lists.

Every result before the predeclaration is frozen is a dry run (DR2 to DR3), EXPLORATORY.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib import resources
from typing import Any

import numpy as np
from numpy.typing import NDArray

SETTINGS_FILE = "dr4_delta.json"
STAR_COLUMNS = (
    "source_id",
    "ra",
    "dec",
    "parallax",
    "parallax_error",
    "pmra",
    "pmra_error",
    "pmdec",
    "pmdec_error",
    "phot_g_mean_mag",
)
Array = NDArray[np.float64]


def settings_text() -> str:
    return resources.files("sdp_stellar").joinpath(SETTINGS_FILE).read_text("utf-8")


def load_settings() -> dict[str, Any]:
    document: dict[str, Any] = json.loads(settings_text())
    return document


def settings_sha256() -> str:
    return hashlib.sha256(settings_text().encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Cluster:
    """One seed cluster as HR23 gives it (degrees, mas, mas/yr)."""

    name: str
    ra: float
    dec: float
    rt: float
    plx: float
    s_plx: float
    pmra: float
    s_pmra: float
    pmdec: float
    s_pmdec: float

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> Cluster:
        keys = {
            "name": "Name",
            "ra": "RA_ICRS",
            "dec": "DE_ICRS",
            "rt": "rt",
            "plx": "Plx",
            "s_plx": "s_Plx",
            "pmra": "pmRA",
            "s_pmra": "s_pmRA",
            "pmdec": "pmDE",
            "s_pmdec": "s_pmDE",
        }
        missing = [v for v in keys.values() if row.get(v) is None or str(row.get(v)).strip() == ""]
        if missing:
            raise ValueError(f"cluster row {row.get('Name')!r} lacks {', '.join(missing)}")
        values: dict[str, Any] = {k: float(row[v]) for k, v in keys.items() if k != "name"}
        return cls(name=str(row["Name"]).strip(), **values)


def fetch_parallax_window(cluster: Cluster, settings: Mapping[str, Any]) -> tuple[float, float]:
    """The parallax range a release's cone is asked for. It holds every parallax that can pass the local
    cut, so the local cut alone decides membership. A member has e <= plx / q (q the minimum
    parallax_over_error) and |plx - c| <= k sqrt(e^2 + s^2); with e at its largest that is
    (plx - c)^2 <= (k / q)^2 plx^2 + k^2 s^2, a quadratic in plx whose roots bound the window. The
    window is never narrower than the configured minimum half-width."""
    s = settings["selection"]
    c, spread = cluster.plx, cluster.s_plx
    k, q = float(s["parallax_sigma"]), float(s["parallax_over_error_min"])
    half = max(float(s["fetch_parallax_halfwidth_min_mas"]), float(s["fetch_parallax_halfwidth_spreads"]) * spread)
    lo, hi = c - half, c + half
    a = 1.0 - (k / q) ** 2
    if a <= 0:
        raise ValueError("parallax_sigma must be below parallax_over_error_min, or no window bounds the cut")
    disc = c * c - a * (c * c - (k * spread) ** 2)
    root = math.sqrt(max(disc, 0.0))
    return min(lo, (c - root) / a), max(hi, (c + root) / a)


def angular_distance_deg(ra0: float, dec0: float, ra: Array, dec: Array) -> Array:
    """Great-circle distance (haversine, stable at small separations)."""
    r0, d0 = math.radians(ra0), math.radians(dec0)
    r, d = np.radians(np.asarray(ra, float)), np.radians(np.asarray(dec, float))
    h = np.sin((d - d0) / 2) ** 2 + math.cos(d0) * np.cos(d) * np.sin((r - r0) / 2) ** 2
    out: Array = np.degrees(2 * np.arcsin(np.sqrt(np.clip(h, 0.0, 1.0))))
    return out


def members(stars: Mapping[str, Array], cluster: Cluster, settings: Mapping[str, Any]) -> NDArray[np.bool_]:
    """Which stars pass the predeclared selection (module docstring)."""
    s = settings["selection"]
    if s["radius"] != "rt":
        raise ValueError(f"unknown radius rule {s['radius']!r}")
    plx = np.asarray(stars["parallax"], dtype=float)
    e_plx = np.asarray(stars["parallax_error"], dtype=float)
    with np.errstate(invalid="ignore", divide="ignore"):
        inside = (
            angular_distance_deg(
                cluster.ra, cluster.dec, np.asarray(stars["ra"], float), np.asarray(stars["dec"], float)
            )
            <= cluster.rt
        )
        bright = np.asarray(stars["phot_g_mean_mag"], float) <= float(s["g_max"])
        measured = plx / e_plx >= float(s["parallax_over_error_min"])
        near = np.abs(plx - cluster.plx) <= float(s["parallax_sigma"]) * np.sqrt(e_plx**2 + cluster.s_plx**2)
        chi2 = (np.asarray(stars["pmra"], float) - cluster.pmra) ** 2 / (
            np.asarray(stars["pmra_error"], float) ** 2 + cluster.s_pmra**2
        ) + (np.asarray(stars["pmdec"], float) - cluster.pmdec) ** 2 / (
            np.asarray(stars["pmdec_error"], float) ** 2 + cluster.s_pmdec**2
        )
        moving = chi2 <= float(s["pm_chi2_max"])
    out: NDArray[np.bool_] = inside & bright & measured & near & moving
    return out


def weighted_mean(values: Array, errors: Array) -> tuple[float, float]:
    """Inverse-variance mean and its error; NaN for no stars."""
    if values.size == 0:
        return math.nan, math.nan
    w = 1.0 / np.asarray(errors, float) ** 2
    return float(np.sum(w * values) / np.sum(w)), float(1.0 / math.sqrt(float(np.sum(w))))


def describe(stars: Mapping[str, Array], chosen: NDArray[np.bool_]) -> dict[str, float | int]:
    """Count, mean parallax, distance and mean proper motion of the chosen stars."""
    plx, e_plx = weighted_mean(
        np.asarray(stars["parallax"], float)[chosen], np.asarray(stars["parallax_error"], float)[chosen]
    )
    pmra, e_pmra = weighted_mean(
        np.asarray(stars["pmra"], float)[chosen], np.asarray(stars["pmra_error"], float)[chosen]
    )
    pmdec, e_pmdec = weighted_mean(
        np.asarray(stars["pmdec"], float)[chosen], np.asarray(stars["pmdec_error"], float)[chosen]
    )
    return {
        "n": int(np.count_nonzero(chosen)),
        "parallax_mas": plx,
        "parallax_error_mas": e_plx,
        "distance_pc": 1000.0 / plx if plx > 0 else math.nan,
        "pmra_masyr": pmra,
        "pmra_error_masyr": e_pmra,
        "pmdec_masyr": pmdec,
        "pmdec_error_masyr": e_pmdec,
    }


def compare(
    cluster: Cluster,
    earlier: Mapping[str, Array],
    later: Mapping[str, Array],
    counterpart: Mapping[int, int] | None,
    settings: Mapping[str, Any],
) -> dict[str, Any]:
    """The predeclared deltas for one cluster, from an earlier release to a later one.
    ``counterpart`` maps an earlier source_id to the later one (None: the ids are the same)."""
    a = members(earlier, cluster, settings)
    b = members(later, cluster, settings)
    da, db = describe(earlier, a), describe(later, b)
    a_ids = [int(x) for x in np.asarray(earlier["source_id"])[a]]
    b_ids = {int(x) for x in np.asarray(later["source_id"])[b]}
    mapped = {i: (i if counterpart is None else counterpart.get(i)) for i in a_ids}
    unmatched = sum(v is None for v in mapped.values())
    lost = sum(v is not None and v not in b_ids for v in mapped.values())
    # each earlier member counts once, even when two of them match the same later star
    kept = sum(v is not None and v in b_ids for v in mapped.values())
    reached = {v for v in mapped.values() if v is not None and v in b_ids}
    return {
        "cluster": cluster.name,
        "earlier": da,
        "later": db,
        "delta_n": db["n"] - da["n"],
        "delta_parallax_mas": db["parallax_mas"] - da["parallax_mas"],
        "delta_parallax_error_mas": math.hypot(da["parallax_error_mas"], db["parallax_error_mas"]),
        "delta_distance_pc": db["distance_pc"] - da["distance_pc"],
        "delta_pmra_masyr": db["pmra_masyr"] - da["pmra_masyr"],
        "delta_pmdec_masyr": db["pmdec_masyr"] - da["pmdec_masyr"],
        "kept": kept,
        "lost": lost,
        "unmatched": unmatched,
        "new": len(b_ids - reached),
    }


def agreement_with_catalogue(
    stars: Mapping[str, Array], chosen: NDArray[np.bool_], catalogue_ids: Sequence[int]
) -> dict[str, Any]:
    """How the selection in one release compares with a published member list of the same release."""
    ours = {int(x) for x in np.asarray(stars["source_id"])[chosen]}
    theirs = {int(x) for x in catalogue_ids}
    both = len(ours & theirs)
    return {
        "selected": len(ours),
        "catalogue": len(theirs),
        "both": both,
        "catalogue_found_share": round(both / len(theirs), 4) if theirs else None,
        "selected_in_catalogue_share": round(both / len(ours), 4) if ours else None,
    }


def flat(row: Mapping[str, Any]) -> dict[str, Any]:
    """A compare() result as one CSV row."""
    out: dict[str, Any] = {}
    for k, v in row.items():
        if isinstance(v, Mapping):
            out |= {f"{k}_{kk}": vv for kk, vv in v.items()}
        else:
            out[k] = v
    return out

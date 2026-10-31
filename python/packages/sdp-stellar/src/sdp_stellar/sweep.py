"""SkySweep: the UC-01 candidate pipeline run tile by tile over the whole sky (STL-009).

The predeclared UC-01 experiment searches four regions and issues a verdict. A sweep is its
exploratory companion: the same quality cuts, feature policy, clustering, pass marks and benchmark
cross-match, applied to every tile of a fixed tiling of the sky, so the complete local Gaia copy can
be searched. It never issues a verdict: every result is labelled exploratory, and a promising
candidate becomes a region of a new predeclaration before anything is claimed.

Tiles are declination bands of ``tile_deg`` cut into RA steps of about ``tile_deg`` on the sky. A
tile is clustered with a ``margin_deg`` rim of its neighbours' stars, so a cluster on a boundary is
seen whole, and it keeps only candidates whose centre lies inside it, so no candidate is counted
twice. Each tile also runs ``null_fields_per_tile`` null fields (the whole pipeline on shuffled
kinematics). Without a significance test a potentially new candidate from a tile whose null field
produced a survivor is inconclusive, as in the predeclared run; with one (STL-011) it stays
potentially new only when it is more significant than every such survivor.

Distance scales (STL-012): a cluster's angular size shrinks with distance, so one tile size cannot
suit both a cluster 150 pc away (several degrees across) and one at 3 kpc (a few arcminutes). With
``scales`` in the settings the sky is swept once per parallax shell, each with its own tile size and
rim: large tiles for the nearest shell, small ones for the farthest, so the tile spans a similar
physical size in every shell. A shell is clustered over its parallax range widened by
``parallax_rim`` on each side, so a cluster on a shell boundary is seen whole, and it keeps only
candidates whose members' mean parallax lies inside its own half-open range and counts only its own
stars, so no star is counted in two shells. Each shell sees a boundary cluster cut at its own window,
so the two shells' means can fall on either side of the boundary; a cluster both shells keep (the
smaller shares at least half its members with the larger) is reported once, by the shell that found
more of it. Without ``scales`` the sweep is the single scale of ``tile_deg`` and ``margin_deg`` it
always was.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib import resources
from typing import Any

import numpy as np
import pyarrow as pa

from sdp.kernel.ports.compute import ClusterSearchAlgorithm
from sdp_stellar import analysis
from sdp_stellar import benchmark as bm
from sdp_stellar.features import PolicyError, apply_policy
from sdp_stellar.null import null_field
from sdp_stellar.quality import QualityCuts, column
from sdp_stellar.search import configurations, labelling_document, run

LABEL = "EXPLORATORY"
SCHEMA = "sdp-stellar/sweep-tile@1"


def load_settings() -> dict[str, Any]:
    """The sweep's settings: the UC-01 predeclaration's cuts, policy, pass marks and cross-match
    window, plus the sweep's own tiling, grid and null-field count (``sweep.json``)."""
    from sdp_stellar.protocol import load_predeclaration

    declared = load_predeclaration()["parameters"]
    own: dict[str, Any] = json.loads(resources.files("sdp_stellar").joinpath("sweep.json").read_text("utf-8"))
    shared = (
        "quality_cuts",
        "feature_space_policy",
        "candidate_checks",
        "crossmatch_radius_deg",
        "pm_window_masyr",
        "benchmarks",
    )
    return {**{k: declared[k] for k in shared}, **own}


@dataclass(frozen=True)
class Scale:
    """One parallax shell of the sweep and its tiling (STL-012). ``name`` is empty for the single
    legacy scale, whose tile ids carry no prefix and which owns every parallax."""

    name: str
    tile_deg: float
    margin_deg: float
    parallax_min: float = -math.inf  # mas, inclusive
    parallax_max: float = math.inf  # mas, exclusive
    parallax_rim: float = 0.0  # share of each boundary the search window reaches past it

    def owns(self, parallax: np.ndarray) -> np.ndarray:
        p = np.asarray(parallax, dtype=float)
        return np.asarray((p >= self.parallax_min) & (p < self.parallax_max), dtype=bool)

    @property
    def window(self) -> tuple[float, float]:
        """The parallax range searched: the shell's own range widened by the rim."""
        lo = self.parallax_min * (1.0 - self.parallax_rim) if math.isfinite(self.parallax_min) else -math.inf
        hi = self.parallax_max * (1.0 + self.parallax_rim) if math.isfinite(self.parallax_max) else math.inf
        return lo, hi

    def searched(self, parallax: np.ndarray) -> np.ndarray:
        lo, hi = self.window
        p = np.asarray(parallax, dtype=float)
        return np.asarray((p >= lo) & (p <= hi), dtype=bool)


def scales(settings: Mapping[str, Any]) -> list[Scale]:
    """The settings' distance scales, nearest first, refused unless their parallax ranges meet end to
    end without gaps or overlaps; without ``scales``, the single legacy scale."""
    doc = settings.get("scales")
    if doc is None:
        return [Scale("", float(settings["tile_deg"]), float(settings["margin_deg"]))]
    if not isinstance(doc, list) or not doc:
        raise ValueError("scales must be a non-empty list")
    out: list[Scale] = []
    for entry in doc:
        name = entry.get("name")
        if not isinstance(name, str) or not name.isalpha() or not name.islower():
            raise ValueError(f"a scale's name must be lower-case letters, not {name!r}")
        lo = entry.get("parallax_min_mas")
        hi = entry.get("parallax_max_mas")
        numbers: dict[str, float] = {}
        for key in ("tile_deg", "margin_deg", "parallax_rim"):
            value = entry.get(key)
            if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value) or value < 0:
                raise ValueError(f"scale {name}: {key} must be a number of at least 0, not {value!r}")
            numbers[key] = float(value)
        if numbers["parallax_rim"] >= 1:
            raise ValueError(f"scale {name}: parallax_rim must be below 1")
        bands(numbers["tile_deg"])
        for key, value in (("parallax_min_mas", lo), ("parallax_max_mas", hi)):
            if value is not None and (isinstance(value, bool) or not isinstance(value, int | float) or value < 0):
                raise ValueError(f"scale {name}: {key} must be a parallax of at least 0 or null, not {value!r}")
        out.append(
            Scale(
                name,
                numbers["tile_deg"],
                numbers["margin_deg"],
                -math.inf if lo is None else float(lo),
                math.inf if hi is None else float(hi),
                numbers["parallax_rim"],
            )
        )
    if len({s.name for s in out}) != len(out):
        raise ValueError("scale names must be unique")
    # nearest first: each shell's lower parallax bound is the next shell's upper one
    if out[0].parallax_max != math.inf or out[-1].parallax_min != -math.inf:
        raise ValueError("the nearest scale needs no upper parallax and the farthest no lower one")
    for near, far in itertools.pairwise(out):
        if near.parallax_min != far.parallax_max or not near.parallax_min > 0:
            raise ValueError(f"scales {near.name} and {far.name} must meet at one positive parallax")
    return out


@dataclass(frozen=True)
class Tile:
    band: int
    index: int
    ra_min: float
    ra_max: float
    dec_min: float
    dec_max: float
    scale: str = ""

    @property
    def tile_id(self) -> str:
        base = f"b{self.band:03d}-t{self.index:03d}"
        return f"{self.scale}-{base}" if self.scale else base

    @property
    def ra_deg(self) -> float:
        return (self.ra_min + self.ra_max) / 2.0

    @property
    def dec_deg(self) -> float:
        return (self.dec_min + self.dec_max) / 2.0

    def contains(self, ra_deg: float, dec_deg: float) -> bool:
        """Half-open on both axes, so every point of the sky is in exactly one tile; the north pole
        belongs to the top band."""
        ra = ra_deg % 360.0
        in_dec = self.dec_min <= dec_deg < self.dec_max or (dec_deg == 90.0 and self.dec_max == 90.0)
        return in_dec and self.ra_min <= ra < self.ra_max


def bands(tile_deg: float) -> int:
    n = round(180.0 / tile_deg)
    if n < 1 or not math.isclose(n * tile_deg, 180.0):
        raise ValueError(f"tile_deg {tile_deg} does not divide 180 degrees of declination")
    return n


def band_of(dec_deg: np.ndarray, tile_deg: float) -> np.ndarray:
    n = bands(tile_deg)
    return np.clip(np.floor((np.asarray(dec_deg, dtype=float) + 90.0) / tile_deg), 0, n - 1).astype(np.int64)


def tiles_in_band(band: int, tile_deg: float) -> int:
    """RA steps in a band: about ``tile_deg`` wide at the band's widest edge, at least one."""
    dec_lo, dec_hi = -90.0 + band * tile_deg, -90.0 + (band + 1) * tile_deg
    nearest_equator = 0.0 if dec_lo < 0.0 < dec_hi else min(abs(dec_lo), abs(dec_hi))
    return max(1, math.ceil(360.0 * math.cos(math.radians(nearest_equator)) / tile_deg))


def tile(band: int, index: int, tile_deg: float, scale: str = "") -> Tile:
    n = tiles_in_band(band, tile_deg)
    if not 0 <= index < n:
        raise ValueError(f"band {band} has tiles 0..{n - 1}, not {index}")
    step = 360.0 / n
    dec_lo = -90.0 + band * tile_deg
    return Tile(band, index, index * step, (index + 1) * step, dec_lo, dec_lo + tile_deg, scale)


def tiles(tile_deg: float, scale: str = "") -> list[Tile]:
    return [tile(b, i, tile_deg, scale) for b in range(bands(tile_deg)) for i in range(tiles_in_band(b, tile_deg))]


def neighbourhood(t: Tile, margin_deg: float, ra_deg: np.ndarray, dec_deg: np.ndarray) -> np.ndarray:
    """Rows within the tile's box widened by ``margin_deg`` on the sky (all RA near a pole)."""
    dec_lo, dec_hi = t.dec_min - margin_deg, t.dec_max + margin_deg
    dec = np.asarray(dec_deg, dtype=float)
    inside = (dec >= dec_lo) & (dec <= dec_hi)
    if dec_lo <= -90.0 or dec_hi >= 90.0:
        return inside
    widen = margin_deg / math.cos(math.radians(max(abs(dec_lo), abs(dec_hi))))
    if (t.ra_max - t.ra_min) + 2 * widen >= 360.0:
        return inside
    lo, hi = (t.ra_min - widen) % 360.0, (t.ra_max + widen) % 360.0
    ra = np.asarray(ra_deg, dtype=float) % 360.0
    in_ra = (ra >= lo) & (ra <= hi) if lo <= hi else (ra >= lo) | (ra <= hi)
    return inside & in_ra


def tile_seed(tile_id: str, seed: int) -> int:
    """A tile's own seed, from the sweep seed and the tile id alone, so a tile reruns identically
    whatever order or worker it runs in."""
    digest = hashlib.sha256(f"{seed}:{tile_id}".encode()).digest()
    return int.from_bytes(digest[:4], "big")


def _labellings(
    table: pa.Table, t: Tile, settings: Mapping[str, Any], clusterers: Mapping[str, ClusterSearchAlgorithm], seed: int
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    """The feature matrix the clustering saw, and one labelling per grid point."""
    fm = apply_policy(settings["feature_space_policy"], table, t.ra_deg, t.dec_deg)
    out = []
    for config in configurations(settings["algorithms"], settings["hyperparameter_grid"]):
        impl = clusterers[config.algorithm_id]
        out.append(labelling_document(run(impl, fm, config, seed), config, str(getattr(impl, "backend", "cpu"))))
    return fm.values, out


def _with_mean_parallax(candidates: list[dict[str, Any]], table: pa.Table) -> list[dict[str, Any]]:
    """Record each candidate's arithmetic mean member parallax, the statistic its shell is chosen by."""
    parallax = column(table, "parallax_mas")
    row = {k: i for i, k in enumerate(table.column("source_key").to_pylist())}
    for c in candidates:
        rows = [row[k] for k in c["member_keys"]]
        c["mean_parallax_mas"] = float(np.mean(parallax[rows])) if rows else None
    return candidates


def _owned(candidates: Sequence[dict[str, Any]], t: Tile, scale: Scale) -> list[dict[str, Any]]:
    def in_shell(c: Mapping[str, Any]) -> bool:
        mean = c.get("mean_parallax_mas")
        return bool(scale.owns(np.array([math.nan if mean is None else mean]))[0])

    return [
        c
        for c in candidates
        if c["centre"] is not None
        and t.contains(c["centre"]["ra_deg"], c["centre"]["dec_deg"])
        and (not scale.name or in_shell(c))
    ]


def _owned_stars(kept: pa.Table, t: Tile, scale: Scale) -> int:
    """Stars that passed the cuts inside the tile itself, not its rim, and in the scale's own parallax
    range: each star counts in one tile of one scale."""
    ra, dec = ra_dec(kept)
    ra = ra % 360.0
    in_dec = ((t.dec_min <= dec) & (dec < t.dec_max)) | ((dec == 90.0) & (t.dec_max == 90.0))
    own = scale.owns(column(kept, "parallax_mas")) if scale.name else np.ones(kept.num_rows, dtype=bool)
    return int(np.count_nonzero(in_dec & (t.ra_min <= ra) & (ra < t.ra_max) & own))


def sweep_tile(
    table: pa.Table,
    t: Tile,
    settings: Mapping[str, Any],
    clusterers: Mapping[str, ClusterSearchAlgorithm],
    benchmarks: Sequence[bm.Benchmark],
    seed: int,
    scale: Scale | None = None,
) -> dict[str, Any]:
    """One tile: cuts, clustering over the sweep grid, candidates owned by the tile, null fields.

    ``table`` is canonical astrometry for the tile and its rim; benchmarks missing columns are left
    out, so with fewer than two usable ones nothing is called potentially new (STL-005). ``scale``
    is the tile's distance scale (STL-012): only stars in its parallax window are searched, and only
    candidates and stars in its own range are kept."""
    scale = scale or Scale("", 0.0, 0.0)
    if scale.name != t.scale:
        raise ValueError(f"tile {t.tile_id} is not a tile of scale {scale.name or '(single)'}")
    # unknown or malformed settings are refused even for a tile that is skipped
    analysis.cmd_policy(settings)
    with_significance = analysis.significance_settings(settings) is not None
    usable = [b for b in benchmarks if not b.missing_columns()]
    s = tile_seed(t.tile_id, seed)
    kept, cuts = QualityCuts.from_parameters(settings["quality_cuts"]).apply(table)
    if scale.name:
        kept = kept.filter(pa.array(scale.searched(column(kept, "parallax_mas"))))
    result: dict[str, Any] = {
        "schema": SCHEMA,
        "label": LABEL,
        "tile": {
            "tile_id": t.tile_id,
            "ra_deg": t.ra_deg,
            "dec_deg": t.dec_deg,
            "band": t.band,
            "index": t.index,
            "scale": t.scale,
        },
        "seed": s,
        "benchmarks": [b.benchmark_id for b in usable],
        "cuts": cuts,
        "owned_stars": _owned_stars(kept, t, scale),
        "candidates": [],
        "null_fields": 0,
        "null_fields_with_survivors": 0,
        "skipped": None,
    }
    least = 2 * int(settings["candidate_checks"]["min_members"])
    if kept.num_rows < least:
        result["skipped"] = f"{kept.num_rows} stars pass the cuts, fewer than {least}"
        return result
    most = settings.get("max_tile_stars")
    if most is not None and kept.num_rows > int(most):
        result["skipped"] = f"{kept.num_rows} stars pass the cuts, more than max_tile_stars {int(most)}"
        return result
    try:
        features, labellings = _labellings(kept, t, settings, clusterers, s)
    except PolicyError as exc:
        result["skipped"] = str(exc)
        return result
    found = analysis._assess(t.tile_id, kept, labellings, usable, settings, s, features)
    found = _owned(_with_mean_parallax(found, kept) if scale.name else found, t, scale)

    survivors = 0
    survivor_sigmas: list[float | None] = []
    for k in range(int(settings["null_fields_per_tile"])):
        shuffled = null_field(kept, s + 1 + k)
        try:
            null_features, null_labellings = _labellings(shuffled, t, settings, clusterers, s + 1 + k)
        except PolicyError:
            continue
        null_settings = {**settings, "pm_window_masyr": 0}
        nulls = analysis._assess(t.tile_id + "-null", shuffled, null_labellings, [], null_settings, s, null_features)
        nulls = _owned(_with_mean_parallax(nulls, shuffled) if scale.name else nulls, t, scale)
        surviving = [c for c in nulls if c["status"] != "rejected"]
        survivors += bool(surviving)
        survivor_sigmas += [c.get("significance_sigma") for c in surviving]
    result["null_fields"] = int(settings["null_fields_per_tile"])
    result["null_fields_with_survivors"] = int(survivors)
    if with_significance:
        result["null_survivor_sigmas"] = survivor_sigmas
    for c in found:
        c["label"] = LABEL
        if c["status"] != "potentially_new":
            continue
        if result["null_fields"] == 0:
            c["status"] = "inconclusive"
            c["reasons"].append("no null field was run for this tile")
        elif with_significance:
            _against_null(c, survivor_sigmas)
        elif survivors:
            c["status"] = "inconclusive"
            c["reasons"].append(
                f"{survivors} of {result['null_fields']} null fields of this tile produced surviving candidates"
            )
    result["candidates"] = found
    return result


def _against_null(c: dict[str, Any], survivor_sigmas: Sequence[float | None]) -> None:
    """With the significance test (STL-011), a potentially new candidate is held to the tile's own
    false candidates one by one: it stays potentially new only when it is more significant than every
    candidate that survived a null field. A survivor whose significance could not be assessed beats
    every candidate, as the tile-wide rule did."""
    if not survivor_sigmas:
        return
    if any(v is None for v in survivor_sigmas):
        c["status"] = "inconclusive"
        c["reasons"].append(
            "a null field of this tile produced a surviving candidate the significance test could not assess"
        )
        return
    worst = max(float(v) for v in survivor_sigmas if v is not None)
    sigma = c.get("significance_sigma")
    if sigma is None or float(sigma) <= worst:
        c["status"] = "inconclusive"
        c["reasons"].append(
            f"significance {float(sigma or 0.0):.1f} sigma is no higher than a false candidate's {worst:.1f} sigma "
            "in this tile's null fields"
        )


#: the share of the smaller candidate's members two shells' candidates must have in common to be one cluster
SAME_CLUSTER = 0.5


def distinct(results: Sequence[Mapping[str, Any]]) -> tuple[list[Mapping[str, Any]], int]:
    """Every candidate once, and how many were dropped as another shell's copy of the same cluster.

    Candidates are taken largest first (ties to the nearer, higher mean parallax), and one is dropped
    when a candidate already taken from another shell holds at least ``SAME_CLUSTER`` of its members.
    Within one shell tiles own disjoint centres, so only copies across shells are looked for."""
    pool = [(str(r.get("tile", {}).get("scale", "")), c) for r in results for c in r["candidates"]]

    def order(sc: tuple[str, Mapping[str, Any]]) -> tuple[int, float]:
        mean = sc[1].get("mean_parallax_mas")
        return -int(sc[1]["n_members"]), math.inf if mean is None else -float(mean)

    pool.sort(key=order)
    holder: dict[str, list[int]] = {}  # member key -> indices into taken
    taken: list[tuple[str, Mapping[str, Any]]] = []
    dropped = 0
    for shell, c in pool:
        keys = c["member_keys"]
        shared: dict[int, int] = {}
        for k in keys:
            for j in holder.get(k, ()):
                if taken[j][0] != shell:
                    shared[j] = shared.get(j, 0) + 1
        if keys and any(n >= SAME_CLUSTER * len(keys) for n in shared.values()):
            dropped += 1
            continue
        for k in keys:
            holder.setdefault(k, []).append(len(taken))
        taken.append((shell, c))
    return [c for _, c in taken], dropped


def summarise(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Counts across tiles; the null false-positive rate is per null field, as STL-G-04 counts it.
    A cluster two shells both kept counts once (``duplicates_across_scales`` says how many were dropped)."""
    statuses: dict[str, int] = {}
    candidates, dropped = distinct(results)
    for c in candidates:
        statuses[c["status"]] = statuses.get(c["status"], 0) + 1
    nulls = sum(int(r["null_fields"]) for r in results)
    with_survivors = sum(int(r["null_fields_with_survivors"]) for r in results)
    return {
        "label": LABEL,
        "tiles": len(results),
        "tiles_skipped": sum(1 for r in results if r["skipped"]),
        "stars_searched": sum(int(r["owned_stars"]) for r in results if not r["skipped"]),
        "candidates": dict(sorted(statuses.items())),
        "duplicates_across_scales": dropped,
        "null_fields": nulls,
        "null_false_positive_rate": with_survivors / nulls if nulls else None,
    }


def candidate_rows(results: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One flat row per distinct candidate, potentially new first, then by score: what a person reads first."""
    from sdp_stellar.ranking import score

    order = {"potentially_new": 0, "inconclusive": 1, "known": 2, "rejected": 3}
    rows = []
    for c in distinct(results)[0]:
        centre = c["centre"] or {}
        known = sorted({m["name"] for x in c["crossmatch"] for m in x["matches"]})
        rows.append(
            {
                "candidate_id": c["candidate_id"],
                "status": c["status"],
                "score": round(score(c), 6),
                "ra_deg": centre.get("ra_deg"),
                "dec_deg": centre.get("dec_deg"),
                "pm_ra_cosdec_masyr": centre.get("pm_ra_cosdec_masyr"),
                "pm_dec_masyr": centre.get("pm_dec_masyr"),
                "parallax_mas": centre.get("parallax_mas"),
                "members": c["n_members"],
                "stability": c["stability"],
                "cmd_coherence": c["cmd_coherence"],
                "significance_sigma": c.get("significance_sigma"),
                "matches": ", ".join(known),
                "reasons": "; ".join(c["reasons"]),
            }
        )
    rows.sort(key=lambda row: (order.get(row["status"], 9), -row["score"], row["candidate_id"]))
    return rows


def ra_dec(table: pa.Table) -> tuple[np.ndarray, np.ndarray]:
    return column(table, "ra_deg"), column(table, "dec_deg")

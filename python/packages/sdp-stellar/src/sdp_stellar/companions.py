"""Hidden companions of star-cluster members: the Gaia DR3 trial of the dark-companion screen (EXPLORATORY).

A star with an unseen partner wobbles. Gaia DR3 published an orbit for about 169,000 stars whose wobble
it could fit (``nss_two_body_orbit`` rows with Thiele-Innes elements). From an orbit's size on the sky,
its period and the star's distance, Kepler's third law gives the astrometric mass function

    f = a1^3 / P^2 = M2^3 / (M1 + M2)^2      (a1 in au, P in years, masses in solar masses)

where a1 is the orbit of the visible star about the pair's centre of mass. Taking the photocentre orbit
as a1 assumes the partner gives no light; if it does give light the photocentre moves less than the star,
so the mass found is a lower bound on the partner's mass either way. With the visible star's mass M1
(Gaia's own ``binary_masses``) the equation is solved for M2.

Gaia's own ``binary_masses`` table says, for each orbit combined with the star's mass (``Orbital+M1``),
which flux ratios between partner and star fit two main-sequence stars. When none does
(``fluxratio_upper`` = 0), or Gaia's AMRF test puts the orbit in class III, the partner cannot be an
ordinary star: it is dark. Gaia's ``m2_lower`` (the 16% level of the partner's mass) then sorts it:

* neutron star or black hole: a dark partner above the Chandrasekhar mass, so not one white dwarf;
* dark partner: a dark partner below it (a white dwarf, or a close pair of faint stars).

SDP recomputes the mass function and the partner's mass at Gaia's central M1 beside Gaia's figures, so
a reader can see the two agree (Gaia BH1 gives 12.8 solar masses both ways).

In a cluster the members share one distance, so the cluster's mean parallax replaces each star's own
(as cmd_policy ``cluster-distance`` does for the CMD). Members are found again here, blind to RUWE:
the sweep's catalogue drops stars with a high RUWE, which are exactly the ones that wobble.

The null is the field: for every (G, parallax) bin, the share of all Gaia stars that are wobblers or
that carry a flagged orbit. A cluster's expected count is the sum of its members' field shares, and a
Poisson tail says how often the field alone gives as many. DR4's epoch astrometry will let SDP fit
the orbits and their errors itself instead of taking Gaia's.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from importlib import resources
from typing import Any

import numpy as np
from numpy.typing import NDArray
from scipy import stats
from scipy.spatial import cKDTree

SETTINGS_FILE = "companions.json"
DAYS_PER_YEAR = 365.25
#: the verdicts, from the most to the least remarkable
COMPACT = "neutron-star-or-black-hole"
DARK = "dark-partner"
ORDINARY = "ordinary"
NO_MASS = "no-mass"
UNUSABLE = "orbit-not-used"
VERDICTS = (COMPACT, DARK, ORDINARY, NO_MASS, UNUSABLE)
FLAGGED = (COMPACT, DARK)

Array = NDArray[np.float64]


def settings_text() -> str:
    return resources.files("sdp_stellar").joinpath(SETTINGS_FILE).read_text("utf-8")


def load_settings() -> dict[str, Any]:
    document: dict[str, Any] = json.loads(settings_text())
    return document


def settings_sha256() -> str:
    return hashlib.sha256(settings_text().encode("utf-8")).hexdigest()


# --- orbits to masses -----------------------------------------------------------------------------


def photocentre_semimajor_mas(a: Array, b: Array, f: Array, g: Array) -> Array:
    """The semi-major axis of the orbit on the sky from the Thiele-Innes elements A, B, F, G (same unit)."""
    a, b, f, g = (np.asarray(x, dtype=float) for x in (a, b, f, g))
    u = 0.5 * (a**2 + b**2 + f**2 + g**2)
    v = a * g - b * f
    return np.sqrt(u + np.sqrt(np.clip((u + v) * (u - v), 0.0, None)))


def mass_function_msun(a0_mas: Array, parallax_mas: Array, period_days: Array) -> Array:
    """f = a1^3 / P^2 in solar masses, with a1 = a0 / parallax in au and P in years."""
    a1_au = np.asarray(a0_mas, dtype=float) / np.asarray(parallax_mas, dtype=float)
    p_yr = np.asarray(period_days, dtype=float) / DAYS_PER_YEAR
    out: Array = a1_au**3 / p_yr**2
    return out


def companion_mass(mass_function: Array, m1: Array, iterations: int = 40) -> Array:
    """M2 solving M2^3 / (M1 + M2)^2 = f; NaN where f or M1 is not a finite non-negative number.

    The cubic g(x) = x^3 - f (x + M1)^2 has one positive root, above f, and is convex beyond f/3, so
    Newton's method started above the root falls to it monotonically."""
    f = np.asarray(mass_function, dtype=float)
    m1 = np.broadcast_to(np.asarray(m1, dtype=float), f.shape)
    good = np.isfinite(f) & np.isfinite(m1) & (f >= 0) & (m1 >= 0)
    ff = np.where(good, f, 0.0)
    mm = np.where(good, m1, 0.0)
    x = ff + 2.0 * mm + 1.0
    while True:  # doubled until g(x) >= 0, so the start is at or above the root
        below = x**3 - ff * (x + mm) ** 2 < 0
        if not below.any():
            break
        x = np.where(below, 2.0 * x, x)
    for _ in range(iterations):
        g = x**3 - ff * (x + mm) ** 2
        dg = 3.0 * x**2 - 2.0 * ff * (x + mm)
        step = np.where(dg > 0, g / np.where(dg > 0, dg, 1.0), 0.0)
        x = x - step
    return np.where(good, x, np.nan)


def _col(orbits: Mapping[str, Any], name: str, n: int) -> Array:
    value = orbits.get(name)
    if value is None:
        return np.full(n, np.nan)
    return np.asarray(value, dtype=float)


def companion_masses(
    orbits: Mapping[str, Any],
    settings: Mapping[str, Any],
    parallax_mas: Array | None = None,
    parallax_error_mas: Array | None = None,
) -> dict[str, Array]:
    """For each orbit: the photocentre size, the mass function, the partner mass at Gaia's M1 if it is
    dark, and the lower bound on the partner's mass.

    The lower bound is Gaia's own ``m2_lower`` from ``binary_masses``, which carries the full covariance
    of the orbit (the Thiele-Innes elements are strongly correlated, so their quoted errors drawn apart
    would be far too wide). ``parallax_mas`` replaces the orbit's own parallax by a cluster's: the mass
    function at Gaia's lower bound is moved to that distance (it scales as parallax^-3), taken at the
    cluster parallax plus the declared sigmas of its error (nearer means lighter), and solved again at
    Gaia's M1."""
    a, b, f, g = (np.asarray(orbits[f"{x}_thiele_innes"], dtype=float) for x in "abfg")
    n = len(a)
    period = np.asarray(orbits["period"], dtype=float)
    own_plx = np.asarray(orbits["parallax"], dtype=float)
    plx = own_plx if parallax_mas is None else np.asarray(parallax_mas, dtype=float)
    m1 = _col(orbits, "m1", n)
    gaia_low = _col(orbits, "m2_lower", n)

    a0 = photocentre_semimajor_mas(a, b, f, g)
    with np.errstate(divide="ignore", invalid="ignore"):
        fm = mass_function_msun(a0, plx, period)
    m2 = companion_mass(fm, m1)
    if parallax_mas is None:
        low = gaia_low
    else:
        k = float(settings["mass"]["cluster_parallax_sigma"])
        err = np.zeros(n) if parallax_error_mas is None else np.nan_to_num(np.asarray(parallax_error_mas, dtype=float))
        near = plx + k * err
        with np.errstate(divide="ignore", invalid="ignore"):
            f_low = gaia_low**3 / (m1 + gaia_low) ** 2 * (own_plx / near) ** 3
        low = companion_mass(np.where(near > 0, f_low, np.nan), m1)
    return {"a0_mas": a0, "mass_function_msun": fm, "m2_msun": m2, "m2_lower_msun": np.asarray(low, dtype=float)}


def is_dark(orbits: Mapping[str, Any]) -> NDArray[np.bool_]:
    """Gaia found no flux ratio that fits two main-sequence stars, or put the orbit in AMRF class III."""
    n = len(np.asarray(orbits["source_id"]))
    upper = _col(orbits, "fluxratio_upper", n)
    flag = np.asarray(orbits.get("flag", np.full(n, "")), dtype=object)
    out: NDArray[np.bool_] = (np.isfinite(upper) & (upper == 0.0)) | (flag == "AMRFClassIII")
    return out


def verdicts(
    m2_lower: Array, dark: NDArray[np.bool_], usable: NDArray[np.bool_], settings: Mapping[str, Any]
) -> NDArray[np.str_]:
    """The rule in the module docstring, applied to each orbit."""
    chandra = float(settings["mass"]["chandrasekhar_msun"])
    low = np.asarray(m2_lower, dtype=float)
    dark = np.asarray(dark, dtype=bool)
    out = np.full(low.shape, ORDINARY, dtype=object)
    out[dark] = DARK
    out[dark & np.isfinite(low) & (low > chandra)] = COMPACT
    out[~np.isfinite(low)] = NO_MASS
    out[~np.asarray(usable, dtype=bool)] = UNUSABLE
    return out.astype(str)


def usable_orbits(
    orbits: Mapping[str, Any], settings: Mapping[str, Any], own_parallax: bool = True
) -> NDArray[np.bool_]:
    """Orbits of the declared solution types and significance, combined by Gaia with the star's mass
    the declared way; with ``own_parallax`` also of the declared parallax quality."""
    o = settings["orbits"]
    n = len(np.asarray(orbits["source_id"]))
    kinds = np.asarray(orbits["nss_solution_type"], dtype=object)
    method = np.asarray(orbits.get("combination_method", np.full(n, "")), dtype=object)
    sig = np.asarray(orbits["significance"], dtype=float)
    out: NDArray[np.bool_] = (
        np.isin(kinds, list(o["solution_types"]))
        & np.isin(method, list(o["combination_methods"]))
        & (np.nan_to_num(sig) >= float(o["significance_min"]))
    )
    if own_parallax:
        plx = np.asarray(orbits["parallax"], dtype=float)
        plx_err = np.asarray(orbits["parallax_error"], dtype=float)
        with np.errstate(divide="ignore", invalid="ignore"):
            poe = plx / plx_err
        out &= np.nan_to_num(poe) >= float(o["parallax_over_error_min"])
    return out


def judge(
    orbits: Mapping[str, Any],
    settings: Mapping[str, Any],
    parallax_mas: Array | None = None,
    parallax_error_mas: Array | None = None,
) -> tuple[dict[str, Array], NDArray[np.str_]]:
    """Masses and verdicts for orbits, at their own parallax or (for cluster members) the cluster's."""
    masses = companion_masses(orbits, settings, parallax_mas, parallax_error_mas)
    usable = usable_orbits(orbits, settings, own_parallax=parallax_mas is None)
    return masses, verdicts(masses["m2_lower_msun"], is_dark(orbits), usable, settings)


# --- the field: wobble baseline and rates ---------------------------------------------------------


def _edges(settings: Mapping[str, Any]) -> tuple[Array, Array, Array]:
    w = settings["wobble"]
    e = w["log_ruwe_edges"]
    g_edges = np.asarray(w["g_bins"], dtype=float)
    plx_edges = np.asarray(settings["field"]["parallax_bins_mas"], dtype=float)
    ruwe_edges = np.linspace(float(e["start"]), float(e["stop"]), int(e["count"]) + 1)
    return g_edges, plx_edges, ruwe_edges


def _bin(values: Array, edges: Array) -> NDArray[np.int64]:
    """Bin index, with values outside the edges clamped to the end bins; NaN gives -1."""
    v = np.asarray(values, dtype=float)
    idx = np.clip(np.searchsorted(edges, v, side="right") - 1, 0, len(edges) - 2)
    return np.where(np.isfinite(v), idx, -1)


@dataclass
class FieldCounts:
    """Counts of every scanned star by (G bin, parallax bin, log RUWE bin)."""

    settings: Mapping[str, Any]
    counts: NDArray[np.int64] = field(init=False)

    def __post_init__(self) -> None:
        g, p, r = _edges(self.settings)
        self.counts = np.zeros((len(g) - 1, len(p) - 1, len(r) - 1), dtype=np.int64)

    def add(self, g_mag: Array, parallax_mas: Array, ruwe: Array) -> None:
        g_edges, p_edges, r_edges = _edges(self.settings)
        gi, pi = _bin(g_mag, g_edges), _bin(parallax_mas, p_edges)
        with np.errstate(divide="ignore", invalid="ignore"):
            ri = _bin(np.log10(np.asarray(ruwe, dtype=float)), r_edges)
        ok = (gi >= 0) & (pi >= 0) & (ri >= 0) & (np.asarray(parallax_mas, dtype=float) >= p_edges[0])
        np.add.at(self.counts, (gi[ok], pi[ok], ri[ok]), 1)

    def baseline(self) -> tuple[Array, Array]:
        """Median and robust spread (1.4826 MAD) of log RUWE in each G bin, from the histogram."""
        _, _, r_edges = _edges(self.settings)
        centres = 0.5 * (r_edges[:-1] + r_edges[1:])
        per_g = self.counts.sum(axis=1)
        med = np.full(per_g.shape[0], np.nan)
        mad = np.full(per_g.shape[0], np.nan)
        for i, h in enumerate(per_g):
            if h.sum() == 0:
                continue
            med[i] = _hist_quantile(centres, h, 0.5)
            dev = np.abs(centres - med[i])
            order = np.argsort(dev)
            mad[i] = _hist_quantile(dev[order], h[order], 0.5)
        width = float(r_edges[1] - r_edges[0])
        return med, np.maximum(1.4826 * mad, width)

    def z(self, g_mag: Array, ruwe: Array) -> Array:
        g_edges, _, _ = _edges(self.settings)
        med, spread = self.baseline()
        gi = _bin(g_mag, g_edges)
        with np.errstate(divide="ignore", invalid="ignore"):
            lr = np.log10(np.asarray(ruwe, dtype=float))
        out: Array = np.where(gi >= 0, (lr - med[np.maximum(gi, 0)]) / spread[np.maximum(gi, 0)], np.nan)
        return out

    def stars(self) -> NDArray[np.int64]:
        out: NDArray[np.int64] = self.counts.sum(axis=2)
        return out

    def wobbler_share(self) -> Array:
        """For each (G, parallax) bin, the share of stars with z above the declared threshold."""
        _, _, r_edges = _edges(self.settings)
        centres = 0.5 * (r_edges[:-1] + r_edges[1:])
        med, spread = self.baseline()
        z = (centres[None, :] - med[:, None]) / spread[:, None]
        above = z > float(self.settings["wobble"]["z_min"])
        wob = (self.counts * above[:, None, :]).sum(axis=2)
        total = self.stars()
        with np.errstate(divide="ignore", invalid="ignore"):
            out: Array = np.where(total > 0, wob / total, 0.0)
        return out


def _hist_quantile(x: Array, h: NDArray[np.int64], q: float) -> float:
    c = np.cumsum(h)
    return float(x[int(np.searchsorted(c, q * c[-1]))])


def bin_index(
    g_mag: Array, parallax_mas: Array, settings: Mapping[str, Any]
) -> tuple[NDArray[np.int64], NDArray[np.int64]]:
    g_edges, p_edges, _ = _edges(settings)
    return _bin(g_mag, g_edges), _bin(parallax_mas, p_edges)


def poisson_tail(observed: int, expected: float) -> float:
    """P(N >= observed) for N ~ Poisson(expected): how often the field alone gives this many."""
    if observed <= 0:
        return 1.0
    if expected <= 0:
        return 0.0
    return float(stats.poisson.sf(observed - 1, expected))


# --- clusters and their members -------------------------------------------------------------------


def _unit(ra_deg: Array, dec_deg: Array) -> Array:
    ra, dec = np.radians(np.asarray(ra_deg, dtype=float)), np.radians(np.asarray(dec_deg, dtype=float))
    return np.stack([np.cos(dec) * np.cos(ra), np.cos(dec) * np.sin(ra), np.sin(dec)], axis=-1)


def _sep_deg(u1: Array, u2: Array) -> Array:
    return np.degrees(2.0 * np.arcsin(np.clip(np.linalg.norm(u1 - u2, axis=-1) / 2.0, 0.0, 1.0)))


@dataclass(frozen=True)
class Cluster:
    cluster_id: str
    status: str
    matches: str
    seed_ids: frozenset[int]


def clusters_from_tiles(results: Iterable[Mapping[str, Any]], statuses: Sequence[str] = ()) -> list[Cluster]:
    """One cluster per distinct sweep candidate (cross-shell copies dropped), its members' Gaia source_ids."""
    from sdp_stellar.sweep import distinct

    out = []
    for c in distinct(list(results))[0]:
        if statuses and c["status"] not in statuses:
            continue
        ids = frozenset(int(k.rsplit(":", 1)[1]) for k in c.get("member_keys", []) if k.startswith("gaia:"))
        names = sorted({m["name"] for x in c.get("crossmatch", []) for m in x.get("matches", [])})
        out.append(Cluster(str(c["candidate_id"]), str(c["status"]), ", ".join(names), ids))
    return out


def clusters_from_rows(rows: Iterable[Mapping[str, str]]) -> list[Cluster]:
    """Clusters from a plain table of (cluster, source_id) rows, e.g. a published member list."""
    groups: dict[str, set[int]] = {}
    for r in rows:
        groups.setdefault(str(r["cluster"]), set()).add(int(r["source_id"]))
    return [Cluster(name, "listed", "", frozenset(ids)) for name, ids in sorted(groups.items())]


@dataclass(frozen=True)
class Footprint:
    """Where a cluster's seed members are, used to find its members again, blind to RUWE."""

    unit: Array
    radius_deg: float
    pmra: float
    pmdec: float
    pm_spread: float
    parallax: float
    parallax_spread: float
    seeds_found: int


def footprint(
    stars: Mapping[str, Array], seed_rows: NDArray[np.int64], settings: Mapping[str, Any]
) -> Footprint | None:
    """Centre, radius, motion and parallax of the seed members found in the scanned stars."""
    if len(seed_rows) < 3:
        return None
    m = settings["membership"]
    u = _unit(stars["ra"][seed_rows], stars["dec"][seed_rows])
    centre = u.mean(axis=0)
    centre /= np.linalg.norm(centre)
    radius = float(np.max(_sep_deg(u, centre[None, :]))) * float(m["radius_factor"])
    pmra, pmdec = stars["pmra"][seed_rows], stars["pmdec"][seed_rows]
    plx = stars["parallax"][seed_rows]

    def spread(x: Array) -> float:
        return float(1.4826 * np.median(np.abs(x - np.median(x))))

    pm_spread = max(math.hypot(spread(pmra), spread(pmdec)) / math.sqrt(2.0), float(m["pm_floor_masyr"]))
    return Footprint(
        centre,
        radius,
        float(np.median(pmra)),
        float(np.median(pmdec)),
        pm_spread,
        float(np.median(plx)),
        spread(plx),
        len(seed_rows),
    )


def members(
    stars: Mapping[str, Array], fp: Footprint, candidates: NDArray[np.int64], settings: Mapping[str, Any]
) -> NDArray[np.int64]:
    """The candidate rows inside the footprint whose motion and parallax agree with the cluster's, within
    the declared sigmas of the cluster's spread and each star's own errors. RUWE is never looked at."""
    m = settings["membership"]
    if len(candidates) == 0:
        return candidates
    u = _unit(stars["ra"][candidates], stars["dec"][candidates])
    inside = _sep_deg(u, fp.unit[None, :]) <= fp.radius_deg
    dpm = np.hypot(stars["pmra"][candidates] - fp.pmra, stars["pmdec"][candidates] - fp.pmdec)
    pm_err = np.hypot(np.nan_to_num(stars["pmra_error"][candidates]), np.nan_to_num(stars["pmdec_error"][candidates]))
    pm_ok = dpm <= float(m["pm_sigma"]) * np.sqrt(fp.pm_spread**2 + pm_err**2)
    dplx = np.abs(stars["parallax"][candidates] - fp.parallax)
    plx_err = np.nan_to_num(stars["parallax_error"][candidates])
    plx_ok = dplx <= float(m["parallax_sigma"]) * np.sqrt(fp.parallax_spread**2 + plx_err**2)
    out: NDArray[np.int64] = candidates[inside & pm_ok & plx_ok]
    return out


def near_any(footprints: Sequence[Footprint], ra_deg: Array, dec_deg: Array) -> NDArray[np.bool_]:
    """Stars inside at least one cluster's footprint radius (the rows the second scan keeps)."""
    out = np.zeros(len(np.asarray(ra_deg)), dtype=bool)
    if not footprints or len(out) == 0:
        return out
    centres = np.stack([fp.unit for fp in footprints])
    radii = np.asarray([fp.radius_deg for fp in footprints])
    widest = 2.0 * math.sin(math.radians(float(radii.max())) / 2.0) + 1e-12
    u = _unit(ra_deg, dec_deg)
    for i, hits in enumerate(cKDTree(centres).query_ball_point(u, widest)):
        if hits:
            out[i] = bool(np.any(_sep_deg(centres[hits], u[i][None, :]) <= radii[hits]))
    return out


# --- the screen -----------------------------------------------------------------------------------


def screen(
    clusters: Sequence[Cluster],
    stars: Mapping[str, Array],
    orbits: Mapping[str, Any],
    field: FieldCounts,
    settings: Mapping[str, Any],
) -> dict[str, Any]:
    """Every cluster's members found again, their wobble z and any published orbit's partner mass, and
    each cluster's counts against what the field gives for stars of the same G and parallax.

    ``stars`` holds the scanned rows (``source_id``, ``ra``, ``dec``, ``parallax``, ``parallax_error``,
    ``pmra``, ``pmra_error``, ``pmdec``, ``pmdec_error``, ``phot_g_mean_mag``, ``ruwe``) of every seed
    member and every star near a cluster; ``orbits`` the Gaia orbit table with ``binary_masses``."""
    ids = np.asarray(stars["source_id"], dtype=np.int64)
    row_of = {int(s): i for i, s in enumerate(ids)}
    orbit_ids = np.asarray(orbits["source_id"], dtype=np.int64)
    orbit_rows: dict[int, list[int]] = {}
    for i, s in enumerate(orbit_ids):
        orbit_rows.setdefault(int(s), []).append(i)
    # the field share of flagged orbits, from every orbit star with its own parallax
    _, own_verdict = judge(orbits, settings)
    gi_o, pi_o = bin_index(
        np.asarray(orbits["phot_g_mean_mag"], dtype=float), np.asarray(orbits["parallax"], dtype=float), settings
    )
    total = field.stars()
    flagged = np.zeros(total.shape)
    ok = (gi_o >= 0) & (pi_o >= 0) & np.isin(own_verdict, FLAGGED)
    np.add.at(flagged, (gi_o[ok], pi_o[ok]), 1)
    with np.errstate(divide="ignore", invalid="ignore"):
        orbit_share = np.where(total > 0, flagged / total, 0.0)
    wobble_share = field.wobbler_share()
    z_all = field.z(np.asarray(stars["phot_g_mean_mag"], dtype=float), np.asarray(stars["ruwe"], dtype=float))
    z_min = float(settings["wobble"]["z_min"])

    tree = cKDTree(_unit(stars["ra"], stars["dec"])) if len(ids) else None
    rows: list[dict[str, Any]] = []
    summary: list[dict[str, Any]] = []
    for c in clusters:
        seeds = np.asarray(sorted(row_of[s] for s in c.seed_ids if s in row_of), dtype=np.int64)
        fp = footprint(stars, seeds, settings)
        if fp is None:
            summary.append(
                {
                    "cluster_id": c.cluster_id,
                    "status": c.status,
                    "matches": c.matches,
                    "members": 0,
                    "note": "fewer than 3 seed members found in the scan",
                }
            )
            continue
        assert tree is not None
        chord = 2.0 * math.sin(math.radians(fp.radius_deg) / 2.0) + 1e-12
        near = np.asarray(sorted(tree.query_ball_point(fp.unit, chord)), dtype=np.int64)
        mem = members(stars, fp, near, settings)
        gi, pi = bin_index(stars["phot_g_mean_mag"][mem], stars["parallax"][mem], settings)
        good = (gi >= 0) & (pi >= 0)
        expected_wob = float(wobble_share[gi[good], pi[good]].sum())
        expected_orb = float(orbit_share[gi[good], pi[good]].sum())
        wob = int(np.sum(z_all[mem] > z_min))
        with_orbit = [(r, o) for r in mem for o in orbit_rows.get(int(ids[r]), [])]
        found = 0
        if with_orbit:
            o_idx = np.asarray([o for _, o in with_orbit])
            sub = {k: np.asarray(v)[o_idx] for k, v in orbits.items()}
            plx_err = fp.parallax_spread / math.sqrt(max(len(mem), 1))
            masses, verdict = judge(sub, settings, np.full(len(o_idx), fp.parallax), np.full(len(o_idx), plx_err))
            for j, (r, _) in enumerate(with_orbit):
                v = str(verdict[j])
                found += v in FLAGGED
                rows.append(
                    {
                        "cluster_id": c.cluster_id,
                        "cluster_status": c.status,
                        "cluster_matches": c.matches,
                        "source_id": int(ids[r]),
                        "seed_member": int(ids[r]) in c.seed_ids,
                        "verdict": v,
                        "m2_lower_msun": _r(masses["m2_lower_msun"][j]),
                        "m2_dark_msun": _r(masses["m2_msun"][j]),
                        "m1_msun": _r(sub["m1"][j]),
                        "gaia_m2_lower_msun": _r(sub["m2_lower"][j]),
                        "fluxratio_upper": _r(sub["fluxratio_upper"][j]),
                        "gaia_flag": str(sub["flag"][j] or ""),
                        "period_days": _r(sub["period"][j]),
                        "a0_mas": _r(masses["a0_mas"][j]),
                        "solution_type": str(sub["nss_solution_type"][j]),
                        "significance": _r(sub["significance"][j]),
                        "cluster_parallax_mas": _r(fp.parallax),
                        "own_parallax_mas": _r(stars["parallax"][r]),
                        "g_mag": _r(stars["phot_g_mean_mag"][r]),
                        "ruwe": _r(stars["ruwe"][r]),
                        "wobble_z": _r(z_all[r]),
                    }
                )
        summary.append(
            {
                "cluster_id": c.cluster_id,
                "status": c.status,
                "matches": c.matches,
                "seeds_found": fp.seeds_found,
                "members": len(mem),
                "parallax_mas": _r(fp.parallax),
                "radius_deg": _r(fp.radius_deg),
                "wobblers": wob,
                "wobblers_expected": _r(expected_wob),
                "wobblers_field_p": _r(poisson_tail(wob, expected_wob)),
                "orbits": len({r for r, _ in with_orbit}),
                "flagged_orbits": int(found),
                "flagged_expected": _r(expected_orb),
                "flagged_field_p": _r(poisson_tail(int(found), expected_orb)),
            }
        )
    order = {v: i for i, v in enumerate(VERDICTS)}
    rows.sort(key=lambda r: (order.get(r["verdict"], 9), -(r["m2_lower_msun"] or 0.0)))
    field_verdicts = {v: int(np.sum(own_verdict == v)) for v in VERDICTS}
    return {"candidates": rows, "clusters": summary, "field_orbit_verdicts": field_verdicts}


def _r(x: Any, digits: int = 6) -> float | None:
    v = float(x)
    return round(v, digits) if math.isfinite(v) else None


def field_orbits(orbits: Mapping[str, Any], settings: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Every published orbit screened with its own parallax, no clusters: the known-answer check
    (Gaia BH1 must come out a neutron star or black hole) and the field's own list of flagged orbits."""
    masses, verdict = judge(orbits, settings)
    keep = np.flatnonzero(np.isin(verdict, FLAGGED))
    rows = [
        {
            "source_id": int(orbits["source_id"][i]),
            "verdict": str(verdict[i]),
            "m2_lower_msun": _r(masses["m2_lower_msun"][i]),
            "m2_dark_msun": _r(masses["m2_msun"][i]),
            "m1_msun": _r(orbits["m1"][i]),
            "period_days": _r(orbits["period"][i]),
            "parallax_mas": _r(orbits["parallax"][i]),
            "g_mag": _r(orbits["phot_g_mean_mag"][i]),
            "solution_type": str(orbits["nss_solution_type"][i]),
            "significance": _r(orbits["significance"][i]),
            "gaia_flag": str(orbits["flag"][i] or ""),
        }
        for i in keep
    ]
    rows.sort(key=lambda r: (r["verdict"] != COMPACT, -float(r["m2_lower_msun"] or 0.0)))
    return rows

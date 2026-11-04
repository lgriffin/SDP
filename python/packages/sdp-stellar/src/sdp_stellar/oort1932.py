"""UC-05: Oort's 1932 force perpendicular to the galactic plane, rerun with Gaia DR3
(docs/protocols/uc05-oort-1932.md).

Part A checks the paper's own arithmetic. Part B applies his formula (7) to Gaia tracer columns:

    K(z) = (1 / nu) d(nu sigma_z^2) / dz

with nu(z) the density of a tracer group (counted in the full bulk copy, over the exact volume of a
cylinder about the Sun with |b| >= b_min) and sigma_z^2(z) its mean square vertical velocity (from the
rvs subset). Near the plane |K| = k1 z; Poisson's equation for a rotating disc then gives the local
density, 4 pi G rho0 = k1 - 2 (A^2 - B^2), with A and B from UC-04.

Units: z in pc, velocities in km/s, K in cm/s^2 (Oort's), k1 in s^-2, rho0 in Msun/pc^3.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources
from typing import Any

import astropy.units as u
import numpy as np
from astropy.constants import G as G_CONST
from astropy.coordinates import Galactic, SkyCoord
from numpy.typing import NDArray

from sdp_stellar.oort1927 import KAPPA, _unit_vectors

CHOICES_FILE = "oort1932.json"
PROTOCOL_ID = "uc05-oort-1932"
#: one parsec in cm, and G in cgs, from astropy
PC_CM = float((1 * u.pc).to_value(u.cm))
G_CGS = float(G_CONST.cgs.value)
#: one solar mass per cubic parsec in g/cm^3
MSUN_PC3_G_CM3 = float((1 * u.Msun / u.pc**3).to_value(u.g / u.cm**3))
#: (km/s)^2 per pc in cm/s^2
KMS2_PER_PC = 1e10 / PC_CM
#: (km/s/kpc)^2 in s^-2
KMS_KPC_SQ = (1e5 / (1000.0 * PC_CM)) ** 2
#: how far either side of the Sun the plane is searched for, and in what steps (pc)
Z_SUN_SEARCH_PC = 60.0
Z_SUN_STEP_PC = 0.5

Array = NDArray[np.float64]
Mask = NDArray[np.bool_]


def choices_text() -> str:
    return resources.files("sdp_stellar").joinpath(CHOICES_FILE).read_text("utf-8")


def load_choices() -> dict[str, Any]:
    document: dict[str, Any] = json.loads(choices_text())
    return document


def choices_sha256() -> str:
    return hashlib.sha256(choices_text().encode("utf-8")).hexdigest()


# --- Part A: Oort's arithmetic ------------------------------------------------------------------


def shape_factor(a_over_c: float | None) -> float:
    """Oort's formula (15) for x, inside a homogeneous oblate ellipsoid of axis ratio a/c (None for a flat
    infinite layer, where x = 1)."""
    if a_over_c is None:
        return 1.0
    q2 = a_over_c**2
    return float(q2 / (q2 - 1.0) - q2 / (q2 - 1.0) ** 1.5 * math.atan(math.sqrt(q2 - 1.0)))


def density_from_slope(k1_s2: float, a_km_s_kpc: float = 0.0, b_km_s_kpc: float = 0.0) -> float:
    """Local density in Msun/pc^3 from 4 pi G rho = k1 - 2 (A^2 - B^2), k1 = d|K|/dz in s^-2."""
    rho = (k1_s2 - 2.0 * (a_km_s_kpc**2 - b_km_s_kpc**2) * KMS_KPC_SQ) / (4.0 * math.pi * G_CGS)
    return rho / MSUN_PC3_G_CM3


def _through_origin(z: Array, k: Array) -> float:
    return float((z * k).sum() / (z * z).sum())


def arithmetic(choices: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Part A: each figure recomputed from the paper's own inputs, beside the printed one. Nothing is asserted."""
    o = (choices or load_choices())["oort"]
    t14 = o["table14"]
    z = np.array([r["z_pc"] for r in t14], dtype=float)
    kc = np.array([r["K_mean_corrected"] for r in t14], dtype=float)
    kp = np.array([r["K_preliminary"] for r in t14], dtype=float)
    integral = np.array([r["integral"] for r in t14], dtype=float)
    slopes = {f"z<={lim}": _through_origin(z[z <= lim], kc[z <= lim]) * 1e-9 / PC_CM for lim in (150, 200)}
    plateau = kp[(z >= 200) & (z <= 400)]
    trapezium = np.cumsum(np.diff(np.concatenate([[0.0], z])) * (np.concatenate([[0.0], kc[:-1]]) + kc) / 2.0)
    ex = o["z0_example"]
    at = float(np.interp(ex["z_pc"], z, integral))
    z0_constant = 2.0 * PC_CM / 1e10
    rho_flat = o["dK_dz_s2"] / (4.0 * math.pi * G_CGS) / MSUN_PC3_G_CM3
    return {
        "eq9_constant": {"computed": math.log(10.0) * KMS2_PER_PC, "printed": o["eq9_constant"]},
        "z0_constant": {"computed": z0_constant, "printed": o["z0_constant"]},
        "z0_example": {
            "z_pc": ex["z_pc"],
            "computed_Z0_km_s": math.sqrt(z0_constant * at * 1e-9),
            "printed_Z0_km_s": ex["Z0_km_s"],
            "from": "Table 14's integral, linear between its 400 and 600 pc rows",
        },
        "shape_factor_x": [
            {"a_over_c": r["a_over_c"], "computed": shape_factor(r["a_over_c"]), "printed": r["x"]}
            for r in o["shape_factor_x"]
        ],
        "dK_dz_s2": {
            "computed_through_origin": slopes,
            "printed": o["dK_dz_s2"],
            "from": "Table 14's mean corrected K'(z), a line through the origin",
        },
        "density_flat_layer_msun_pc3": {
            "computed": rho_flat,
            "printed_cases": o["density_cases_msun_pc3"],
            "printed_adopted": o["total_density_msun_pc3"],
            "from": "formula (14) with his dK/dz and x = 1, before any allowance for the inner system",
        },
        "plateau_K_1e9": {
            "computed": float(plateau.mean()),
            "printed": o["summary_plateau_K_1e9"],
            "from": "the mean of Table 14's preliminary K(z) over 200-400 pc",
        },
        "unit_msun_pc3_in_g_cm3": {"computed": MSUN_PC3_G_CM3, "printed": o["unit_msun_pc3_in_g_cm3"]},
        "total_density_g_cm3": {
            "computed": o["total_density_msun_pc3"] * MSUN_PC3_G_CM3,
            "printed": o["total_density_g_cm3"],
        },
        "table14_integral": [
            {"z_pc": float(zi), "computed_trapezium": float(ti), "printed": float(pi)}
            for zi, ti, pi in zip(z, trapezium, integral, strict=True)
        ],
    }


# --- the column ---------------------------------------------------------------------------------


def _area_integral(z: Array, r_cyl: float, b_min_deg: float) -> Array:
    """Integral from 0 to |z| of the column's cross-section, pi min(r_cyl, |z| cot b_min)^2, with z's sign."""
    cot = 1.0 / math.tan(math.radians(b_min_deg))
    zc = r_cyl / cot  # where the cone reaches the cylinder
    a = np.abs(z)
    cone = math.pi * cot**2 * np.minimum(a, zc) ** 3 / 3.0
    tube = math.pi * r_cyl**2 * np.maximum(a - zc, 0.0)
    return np.asarray(np.sign(z) * (cone + tube), dtype=np.float64)


def slab_volume(z1: Array | float, z2: Array | float, r_cyl: float, b_min_deg: float) -> Array:
    """The column's volume (pc^3) between heliocentric heights z1 < z2."""
    lo, hi = np.asarray(z1, dtype=np.float64), np.asarray(z2, dtype=np.float64)
    return _area_integral(hi, r_cyl, b_min_deg) - _area_integral(lo, r_cyl, b_min_deg)


@dataclass(frozen=True)
class Placed:
    """Stars placed in the Sun's column frame: heights and in-plane distances in pc, M_G, and for stars
    with a radial velocity, v_z (km/s, heliocentric) and the radial-velocity error projected on z."""

    z_helio: Array
    r_xy: Array
    b_deg: Array
    m_g: Array
    v_z: Array | None = None
    v_z_rv_error: Array | None = None

    def __len__(self) -> int:
        return int(self.z_helio.size)

    def take(self, mask: Mask) -> Placed:
        return Placed(
            self.z_helio[mask],
            self.r_xy[mask],
            self.b_deg[mask],
            self.m_g[mask],
            None if self.v_z is None else self.v_z[mask],
            None if self.v_z_rv_error is None else self.v_z_rv_error[mask],
        )


def _col(columns: Mapping[str, Any], name: str) -> Array:
    return np.asarray(columns[name], dtype=np.float64)


def quality_mask(
    columns: Mapping[str, Any], choices: Mapping[str, Any] | None = None, *, velocities: bool = False
) -> Mask:
    """The cuts every scenario shares; with ``velocities``, also a finite radial velocity and proper motion."""
    sel = (choices or load_choices())["selection"]
    with np.errstate(invalid="ignore", divide="ignore"):
        plx, err = _col(columns, "parallax"), _col(columns, "parallax_error")
        keep = (_col(columns, "ruwe") < sel["ruwe_max"]) & (plx / err > sel["parallax_over_error_min"])
        keep &= np.isfinite(_col(columns, "phot_g_mean_mag"))
        if velocities:
            for name in ("radial_velocity", "pmra", "pmdec"):
                keep &= np.isfinite(_col(columns, name))
    keep &= np.asarray(np.asarray(columns["duplicated_source"]) == sel["duplicated_source"], dtype=bool)
    return np.asarray(keep, dtype=bool)


def _limits(choices: Mapping[str, Any]) -> tuple[float, float, float, float, float]:
    """The loosest of the scenario cuts: largest radius, highest |z|, |b| floor, and the M_G range."""
    s, sel = choices["scenarios"], choices["selection"]
    groups = [choices["groups"][g] for g in s["group"]]
    return (
        max(s["cylinder_radius_pc"]),
        sel["z_table_max_pc"] + Z_SUN_SEARCH_PC,
        sel["b_min_deg"],
        min(lo for lo, _ in groups),
        max(hi for _, hi in groups),
    )


def prefilters(choices: Mapping[str, Any] | None = None) -> list[tuple[str, str, Any]]:
    """Cuts safe to push into the Parquet read: no scenario can use a star outside them."""
    c = choices or load_choices()
    r_max, z_max, _, _, m_hi = _limits(c)
    d_max = math.hypot(r_max, z_max)
    zp_min = min(c["scenarios"]["parallax_zero_point_mas"])
    return [
        ("parallax", ">", 1000.0 / d_max + zp_min - 1e-6),
        ("ruwe", "<", c["selection"]["ruwe_max"]),
        ("phot_g_mean_mag", "<", m_hi + 5.0 * math.log10(d_max) - 5.0 + 0.1),
    ]


def column_mask(columns: Mapping[str, Any], choices: Mapping[str, Any] | None = None) -> Mask:
    """Stars some scenario could place in its column, under either zero-point: a cheap per-file reduction."""
    c = choices or load_choices()
    r_max, z_max, b_min, m_lo, m_hi = _limits(c)
    if _col(columns, "ra").size == 0:
        return np.zeros(0, dtype=bool)
    b = SkyCoord(ra=_col(columns, "ra") * u.deg, dec=_col(columns, "dec") * u.deg, frame="icrs").galactic.b.rad
    plx, g = _col(columns, "parallax"), _col(columns, "phot_g_mean_mag")
    inside = np.zeros(plx.size, dtype=bool)
    with np.errstate(invalid="ignore", divide="ignore"):
        for zp in c["scenarios"]["parallax_zero_point_mas"]:
            p = plx - zp
            d, m = 1000.0 / p, g + 5.0 * np.log10(p) - 10.0
            inside |= (p > 0) & (d * np.cos(b) <= r_max) & (np.abs(d * np.sin(b)) <= z_max) & (m >= m_lo) & (m < m_hi)
    return np.asarray(inside & (np.abs(np.degrees(b)) >= b_min - 1e-9), dtype=bool)


def place(columns: Mapping[str, Any], zero_point_mas: float) -> Placed:
    """Heights, in-plane distances, absolute magnitudes and, given radial velocities, vertical velocities."""
    if _col(columns, "ra").size == 0:
        empty = np.zeros(0)
        has_rv = "radial_velocity" in columns
        return Placed(empty, empty, empty, empty, empty if has_rv else None, empty if has_rv else None)
    plx = _col(columns, "parallax") - zero_point_mas
    d = 1000.0 / plx
    m_g = _col(columns, "phot_g_mean_mag") + 5.0 * np.log10(plx) - 10.0
    if "radial_velocity" in columns:
        gal = SkyCoord(
            ra=_col(columns, "ra") * u.deg,
            dec=_col(columns, "dec") * u.deg,
            pm_ra_cosdec=_col(columns, "pmra") * u.mas / u.yr,
            pm_dec=_col(columns, "pmdec") * u.mas / u.yr,
            frame="icrs",
        ).transform_to(Galactic())
        b = np.asarray(gal.b.rad, dtype=np.float64)
        v_b = KAPPA * np.asarray(gal.pm_b.to_value(u.mas / u.yr)) * d / 1000.0
        rv = _col(columns, "radial_velocity")
        rv_err = _col(columns, "radial_velocity_error") if "radial_velocity_error" in columns else np.zeros(d.size)
        v_z: Array | None = rv * np.sin(b) + v_b * np.cos(b)
        v_err: Array | None = rv_err * np.abs(np.sin(b))
    else:
        b = np.asarray(
            SkyCoord(ra=_col(columns, "ra") * u.deg, dec=_col(columns, "dec") * u.deg, frame="icrs").galactic.b.rad,
            dtype=np.float64,
        )
        v_z = v_err = None
    return Placed(d * np.sin(b), d * np.cos(b), np.degrees(b), m_g, v_z, v_err)


@dataclass(frozen=True)
class Scenario:
    group: str
    cylinder_radius_pc: float
    bin_pc: float
    parallax_zero_point_mas: float
    clip: bool

    @property
    def label(self) -> str:
        return (
            f"{self.group} R<{self.cylinder_radius_pc:g}pc bin={self.bin_pc:g}pc "
            f"zp={self.parallax_zero_point_mas:g} clip={'on' if self.clip else 'off'}"
        )

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> Scenario:
        return cls(
            str(doc["group"]),
            float(doc["cylinder_radius_pc"]),
            float(doc["bin_pc"]),
            float(doc["parallax_zero_point_mas"]),
            bool(doc["clip"]),
        )


def scenarios(choices: Mapping[str, Any] | None = None) -> list[Scenario]:
    s = (choices or load_choices())["scenarios"]
    return [
        Scenario(str(g), float(r), float(w), float(zp), bool(cl))
        for g, r, w, zp, cl in itertools.product(
            s["group"], s["cylinder_radius_pc"], s["bin_pc"], s["parallax_zero_point_mas"], s["clip"]
        )
    ]


def baseline(choices: Mapping[str, Any] | None = None) -> Scenario:
    return Scenario.from_document((choices or load_choices())["baseline"])


def scenario_mask(stars: Placed, scenario: Scenario, choices: Mapping[str, Any]) -> Mask:
    lo, hi = choices["groups"][scenario.group]
    keep = (stars.m_g >= lo) & (stars.m_g < hi) & (stars.r_xy <= scenario.cylinder_radius_pc)
    keep &= np.abs(stars.b_deg) >= choices["selection"]["b_min_deg"]
    return np.asarray(keep, dtype=bool)


# --- the plane, the bins and formula (7) --------------------------------------------------------


def _folded_volumes(edges: Array, z_sun: float, r_cyl: float, b_min: float) -> Array:
    """Volume of each |z| bin (z = z_helio + z_sun), both hemispheres."""
    a, b = edges[:-1], edges[1:]
    return slab_volume(a - z_sun, b - z_sun, r_cyl, b_min) + slab_volume(-b - z_sun, -a - z_sun, r_cyl, b_min)


def sun_height(z_helio: Array, r_cyl: float, b_min: float, window_pc: float) -> float:
    """The Sun's height above the plane: where the north and south density profiles, over the exact
    volumes, agree best within the window."""
    edges = np.arange(0.0, window_pc + 1e-9, 25.0)
    best, best_chi = 0.0, math.inf
    for s in np.arange(-Z_SUN_SEARCH_PC, Z_SUN_SEARCH_PC + 1e-9, Z_SUN_STEP_PC):
        z = z_helio + s
        n_north, _ = np.histogram(z[z > 0], edges)
        n_south, _ = np.histogram(-z[z < 0], edges)
        a, b = edges[:-1], edges[1:]
        v_n = slab_volume(a - s, b - s, r_cyl, b_min)
        v_s = slab_volume(-b - s, -a - s, r_cyl, b_min)
        ok = (v_n > 0) & (v_s > 0) & (n_north + n_south > 0)
        var = n_north[ok] / v_n[ok] ** 2 + n_south[ok] / v_s[ok] ** 2
        chi = float((((n_north[ok] / v_n[ok]) - (n_south[ok] / v_s[ok])) ** 2 / np.where(var > 0, var, 1.0)).sum())
        if chi < best_chi:
            best, best_chi = float(s), chi
    return best


@dataclass(frozen=True)
class Bins:
    edges: Array
    count: Array  # density stars
    volume: Array
    n_velocity: Array
    sigma2: Array  # km^2/s^2, error-corrected
    sigma2_error: Array

    @property
    def centres(self) -> Array:
        return np.asarray((self.edges[:-1] + self.edges[1:]) / 2.0)


def bin_column(
    density: Placed,
    velocity: Placed,
    z_sun: float,
    scenario: Scenario,
    choices: Mapping[str, Any],
) -> Bins:
    sel, f = choices["selection"], choices["fit"]
    edges = np.arange(0.0, sel["z_table_max_pc"] + 1e-9, scenario.bin_pc)
    count, _ = np.histogram(np.abs(density.z_helio + z_sun), edges)
    volume = _folded_volumes(edges, z_sun, scenario.cylinder_radius_pc, sel["b_min_deg"])
    assert velocity.v_z is not None and velocity.v_z_rv_error is not None
    zv = np.abs(velocity.z_helio + z_sun)
    idx = np.digitize(zv, edges) - 1
    nb = edges.size - 1
    n_v, s2, s2e = np.zeros(nb), np.full(nb, np.nan), np.full(nb, np.nan)
    for i in range(nb):
        v = velocity.v_z[idx == i]
        e = velocity.v_z_rv_error[idx == i]
        if scenario.clip and v.size:
            med = np.median(v)
            mad = 1.4826 * np.median(np.abs(v - med))
            keep = np.abs(v - med) <= f["clip_mad_multiple"] * mad
            v, e = v[keep], e[keep]
        n_v[i] = v.size
        if v.size < 2:
            continue
        dv = v - v.mean()
        var = float((dv**2).sum() / (v.size - 1))
        m4 = float((dv**4).mean())
        s2[i] = var - float((e**2).mean())
        s2e[i] = math.sqrt(max(m4 - var**2, 0.0) / v.size)
    return Bins(edges, count.astype(np.float64), volume, n_v, s2, s2e)


def force(bins: Bins, count: Array, sigma2: Array, usable: Mask) -> tuple[Array, Array]:
    """Formula (7) at the edge between each pair of usable neighbours: |K| in cm/s^2, and the edge heights.
    ``count`` and ``sigma2`` may carry a leading axis of Monte Carlo draws."""
    pair = usable[:-1] & usable[1:]
    nu = count / bins.volume
    p = nu * sigma2
    zc = bins.centres
    dz = zc[1:] - zc[:-1]
    with np.errstate(invalid="ignore", divide="ignore"):
        k = -(p[..., 1:] - p[..., :-1]) / dz / np.sqrt(nu[..., 1:] * nu[..., :-1]) * KMS2_PER_PC
    return np.asarray(k[..., pair]), np.asarray(bins.edges[1:-1][pair])


def _summarise(z: Array, k: Array, choices: Mapping[str, Any]) -> dict[str, Array]:
    """k1 (s^-2), the flattening ratio and the plateau force, along the last axis of k."""
    f = choices["fit"]
    inner = z <= f["inner_max_pc"]
    lo, hi = f["plateau_pc"]
    outer = (z >= lo) & (z <= hi)
    k1_per_pc = (k[..., inner] * z[inner]).sum(axis=-1) / (z[inner] ** 2).sum()
    zo = z[outer] - z[outer].mean()
    slope = (k[..., outer] * zo).sum(axis=-1) / (zo**2).sum()
    return {
        "k1": np.asarray(k1_per_pc / PC_CM),
        "flattening": np.asarray(slope / k1_per_pc),
        "plateau": np.asarray(k[..., outer].mean(axis=-1)),
    }


@dataclass(frozen=True)
class Estimate:
    z_sun_pc: float
    n_density: int
    n_velocity: int
    edges_pc: Array
    k: Array  # cm/s^2
    k_error: Array
    k1: float
    k1_error: float
    flattening: float
    flattening_error: float
    plateau: float
    plateau_error: float
    rho0: float
    rho0_error: float
    bins: Bins

    def to_document(self, *, with_bins: bool = False) -> dict[str, Any]:
        doc: dict[str, Any] = {
            "z_sun_pc": self.z_sun_pc,
            "n_density": self.n_density,
            "n_velocity": self.n_velocity,
            "K_cm_s2": [
                {"z_pc": float(z), "K": float(k), "error": float(e)}
                for z, k, e in zip(self.edges_pc, self.k, self.k_error, strict=True)
            ],
            "k1_s2": {"value": self.k1, "error": self.k1_error},
            "flattening": {"value": self.flattening, "error": self.flattening_error},
            "plateau_cm_s2": {"value": self.plateau, "error": self.plateau_error},
            "rho0_msun_pc3": {"value": self.rho0, "error": self.rho0_error},
            "rho0_g_cm3": {"value": self.rho0 * MSUN_PC3_G_CM3, "error": self.rho0_error * MSUN_PC3_G_CM3},
        }
        if with_bins:
            b = self.bins
            doc["bins"] = [
                {
                    "z_lo_pc": float(b.edges[i]),
                    "z_hi_pc": float(b.edges[i + 1]),
                    "n_density": int(b.count[i]),
                    "volume_pc3": float(b.volume[i]),
                    "n_velocity": int(b.n_velocity[i]),
                    "sigma_z_km_s": float(math.sqrt(b.sigma2[i])) if b.sigma2[i] > 0 else None,
                }
                for i in range(b.count.size)
            ]
        return doc


def estimate(density: Placed, velocity: Placed, scenario: Scenario, choices: Mapping[str, Any]) -> Estimate:
    """One scenario: the plane, the bins, formula (7), k1, the flattening, the plateau and rho0, with
    Monte Carlo errors. Raises ValueError, with the reason, when the declared minimums are not met."""
    sel, f, rot = choices["selection"], choices["fit"], choices["rotation"]
    dens = density.take(scenario_mask(density, scenario, choices))
    vel = velocity.take(scenario_mask(velocity, scenario, choices))
    if len(dens) == 0 or len(vel) == 0:
        raise ValueError(f"{len(dens)} density and {len(vel)} velocity stars in the column")
    z_sun = sun_height(dens.z_helio, scenario.cylinder_radius_pc, sel["b_min_deg"], f["z_sun_window_pc"])
    bins = bin_column(dens, vel, z_sun, scenario, choices)
    usable = np.asarray(
        (bins.count >= f["min_density_stars_per_bin"])
        & (bins.n_velocity >= f["min_velocity_stars_per_bin"])
        & (np.nan_to_num(bins.sigma2, nan=-1.0) > 0),
        dtype=bool,
    )
    k, z = force(bins, bins.count, bins.sigma2, usable)
    lo, hi = f["plateau_pc"]
    n_in, n_out = int((z <= f["inner_max_pc"]).sum()), int(((z >= lo) & (z <= hi)).sum())
    if n_in < f["min_inner_points"] or n_out < f["min_plateau_points"]:
        raise ValueError(
            f"{n_in} force points at z <= {f['inner_max_pc']:g} pc and {n_out} in {lo:g}-{hi:g} pc, fewer than the "
            f"declared {f['min_inner_points']} and {f['min_plateau_points']}"
        )
    nominal = _summarise(z, k, choices)
    rng = np.random.default_rng(int(f["seed"]))
    draws = int(f["draws"])
    count_d = rng.poisson(bins.count, size=(draws, bins.count.size)).astype(np.float64)
    s2_d = bins.sigma2 + rng.normal(size=(draws, bins.count.size)) * np.nan_to_num(bins.sigma2_error)
    k_d, _ = force(bins, count_d, s2_d, usable)
    drawn = _summarise(z, k_d, choices)
    a_d = rng.normal(rot["A_km_s_kpc"], rot["A_error"], draws)
    b_d = rng.normal(rot["B_km_s_kpc"], rot["B_error"], draws)
    rho_d = np.array([density_from_slope(x, a, b) for x, a, b in zip(drawn["k1"], a_d, b_d, strict=True)])
    k1 = float(nominal["k1"])
    return Estimate(
        z_sun_pc=z_sun,
        n_density=len(dens),
        n_velocity=len(vel),
        edges_pc=z,
        k=k,
        k_error=np.asarray(k_d.std(axis=0)),
        k1=k1,
        k1_error=float(drawn["k1"].std()),
        flattening=float(nominal["flattening"]),
        flattening_error=float(drawn["flattening"].std()),
        plateau=float(nominal["plateau"]),
        plateau_error=float(drawn["plateau"].std()),
        rho0=density_from_slope(k1, rot["A_km_s_kpc"], rot["B_km_s_kpc"]),
        rho0_error=float(rho_d.std()),
        bins=bins,
    )


# --- verdict ------------------------------------------------------------------------------------


def shape(e: Estimate, choices: Mapping[str, Any]) -> str:
    """flattens, does_not_flatten, or undecided, by the declared rule."""
    d = choices["decision"]
    if e.k1 < d["detect_sigma"] * e.k1_error:
        return "undecided"
    if e.flattening + d["ratio_sigma"] * e.flattening_error < d["flat_ratio"]:
        return "flattens"
    if e.flattening - d["ratio_sigma"] * e.flattening_error > d["flat_ratio"]:
        return "does_not_flatten"
    return "undecided"


def detection(results: Mapping[str, Estimate | str], choices: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """UC05-H1 when every scenario flattens; UC05-H0 when every scenario does not; inconclusive otherwise,
    or when any scenario failed."""
    c = choices or load_choices()
    failed = sorted(k for k, v in results.items() if isinstance(v, str))
    ok = {k: v for k, v in results.items() if isinstance(v, Estimate)}
    shapes = {k: shape(v, c) for k, v in ok.items()}
    flat = sorted(k for k, s in shapes.items() if s == "flattens")
    rising = sorted(k for k, s in shapes.items() if s == "does_not_flatten")
    if failed or not ok:
        status = "inconclusive"
    elif len(flat) == len(ok):
        status = "supports_UC05_H1"
    elif len(rising) == len(ok):
        status = "supports_UC05_H0"
    else:
        status = "inconclusive"
    return {"status": status, "scenarios_flatten": flat, "scenarios_rising": rising, "scenarios_failed": failed}


def comparison(results: Mapping[str, Estimate | str], choices: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Oort's 0.092 Msun/pc^3, whose probable error of 20% is 0.2 x 0.092 / 0.6745 at 1 sigma."""
    c = choices or load_choices()
    o = c["oort"]
    rho, sigma = o["total_density_msun_pc3"], o["probable_error_fraction"] * o["total_density_msun_pc3"] / 0.6745
    z = {k: (v.rho0 - rho) / math.hypot(v.rho0_error, sigma) for k, v in results.items() if isinstance(v, Estimate)}
    limit = c["decision"]["agree_sigma"]
    within = sorted(k for k, v in z.items() if abs(v) < limit)
    if not z or len(z) < len(results):
        status = "mixed"
    elif len(within) == len(z):
        status = "agrees_with_oort"
    elif not within:
        status = "disagrees_with_oort"
    else:
        status = "mixed"
    return {"status": status, "oort_rho0_msun_pc3": rho, "oort_sigma": sigma, "z": z, "scenarios_within": within}


def beside_oort(e: Estimate, choices: Mapping[str, Any]) -> dict[str, Any]:
    """Gaia's baseline force at Table 14's heights, k1, the plateau and the unseen mass by Oort's census."""
    o = choices["oort"]
    rows = []
    for r in o["table14"]:
        hit = np.flatnonzero(np.isclose(e.edges_pc, r["z_pc"]))
        rows.append(
            {
                "z_pc": r["z_pc"],
                "gaia_K_1e9": float(e.k[hit[0]] * 1e9) if hit.size else None,
                "gaia_error_1e9": float(e.k_error[hit[0]] * 1e9) if hit.size else None,
                "oort_mean_corrected_1e9": r["K_mean_corrected"],
                "oort_preliminary_1e9": r["K_preliminary"],
            }
        )
    visible = o["visible_stars_msun_pc3"]
    return {
        "table14": rows,
        "k1_over_oort": e.k1 / o["dK_dz_s2"],
        "plateau_1e9": {
            "gaia": e.plateau * 1e9,
            "gaia_error": e.plateau_error * 1e9,
            "oort": o["summary_plateau_K_1e9"],
        },
        "rho0_less_oorts_visible_stars": {
            "value": e.rho0 - visible,
            "error": e.rho0_error,
            "oort_visible_msun_pc3": visible,
            "note": "Oort's own census of the visible stars (his Table 34); this protocol does not count Gaia's",
        },
        "z_sun_pc": {"gaia": e.z_sun_pc, "oort_tentative": o["sun_above_plane_pc_tentative"]},
    }


def _estimate_or_reason(density: Placed, velocity: Placed, s: Scenario, choices: Mapping[str, Any]) -> Estimate | str:
    try:
        return estimate(density, velocity, s, choices)
    except (ValueError, np.linalg.LinAlgError) as exc:
        return str(exc)


def run(
    density_columns: Mapping[str, Any],
    velocity_columns: Mapping[str, Any],
    *,
    choices: Mapping[str, Any] | None = None,
    baseline_only: bool = False,
) -> dict[str, Any]:
    """Part A, then every scenario of Part B, the verdict and the comparison with Oort."""
    c = dict(choices or load_choices())
    dq = quality_mask(density_columns, c)
    vq = quality_mask(velocity_columns, c, velocities=True)
    dcols = {k: np.asarray(v)[dq] for k, v in density_columns.items()}
    vcols = {k: np.asarray(v)[vq] for k, v in velocity_columns.items()}
    base = baseline(c)
    grid = [base] if baseline_only else scenarios(c)
    if base not in grid:
        grid.insert(0, base)
    placed: dict[float, tuple[Placed, Placed]] = {}
    results: dict[str, Estimate | str] = {}
    for s in grid:
        zp = s.parallax_zero_point_mas
        if zp not in placed:
            placed[zp] = (place(dcols, zp), place(vcols, zp))
        results[s.label] = _estimate_or_reason(*placed[zp], s, c)
    reading = detection(results, c)
    if baseline_only:
        reading = {**reading, "status": "inconclusive", "quick_look": True}
    b = results[base.label]
    return {
        "protocol_id": PROTOCOL_ID,
        "version": c["version"],
        "committed_choices_sha256": choices_sha256(),
        "choices_match_committed": c == load_choices(),
        "quick_look": baseline_only,
        "arithmetic": arithmetic(c),
        "baseline_label": base.label,
        "baseline": b.to_document(with_bins=True) if isinstance(b, Estimate) else {"failed": b},
        "beside_oort": beside_oort(b, c) if isinstance(b, Estimate) else None,
        "scenarios": {
            k: ({**v.to_document(), "shape": shape(v, c)} if isinstance(v, Estimate) else {"failed": v})
            for k, v in results.items()
        },
        "verdict": reading,
        "comparison": comparison(results, c),
    }


# --- synthetic columns --------------------------------------------------------------------------


def sheet_potential(k_inf_cm_s2: float, z0_pc: float) -> Any:
    """Phi(z) in (km/s)^2 for K(z) = -K_inf tanh(z / z0): a self-gravitating sheet."""
    k = k_inf_cm_s2 / KMS2_PER_PC

    def phi(z: Array) -> Array:
        x = np.abs(z) / z0_pc
        return np.asarray(k * z0_pc * (x + np.log1p(np.exp(-2.0 * x)) - math.log(2.0)))

    return phi


def slab_potential(k1_s2: float) -> Any:
    """Phi(z) in (km/s)^2 for K(z) = -k1 z: a uniform slab thicker than the column."""
    k = k1_s2 * PC_CM / KMS2_PER_PC  # (km/s)^2 / pc^2

    def phi(z: Array) -> Array:
        return np.asarray(0.5 * k * z**2)

    return phi


def synthetic_column(
    *,
    n: int,
    potential: Any,
    sigma_km_s: float = 20.0,
    z_sun_pc: float = 20.0,
    r_max_pc: float = 320.0,
    z_max_pc: float = 1100.0,
    rv_error_km_s: float = 0.0,
    sun_uvw: tuple[float, float, float] = (10.0, 12.0, 7.0),
    seed: int = 1932,
) -> dict[str, Any]:
    """Isothermal tracers, nu ~ exp(-Phi / sigma^2), uniform across a cylinder about the Sun that sits
    ``z_sun_pc`` above the plane, as native gaia_source columns with radial velocities. M_G is uniform over
    Oort's four groups; the parallaxes are exact (their error is only declared)."""
    rng = np.random.default_rng(seed)
    zs: list[Array] = []
    have = 0
    while have < n:
        z = rng.uniform(-z_max_pc, z_max_pc, 4 * n)
        z = z[rng.uniform(size=z.size) < np.exp(-potential(z) / sigma_km_s**2)]
        zs.append(z)
        have += z.size
    z_gal = np.concatenate(zs)[:n]
    r = r_max_pc * np.sqrt(rng.uniform(size=n))
    phi = rng.uniform(0.0, 2.0 * math.pi, n)
    x, y, zh = r * np.cos(phi), r * np.sin(phi), z_gal - z_sun_pc
    d = np.sqrt(x**2 + y**2 + zh**2)
    lon = np.degrees(np.arctan2(y, x)) % 360.0
    b = np.degrees(np.arcsin(zh / d))
    vel = np.stack(
        [
            rng.normal(0, 30.0, n) - sun_uvw[0],
            rng.normal(0, 25.0, n) - sun_uvw[1],
            rng.normal(0, sigma_km_s, n) - sun_uvw[2],
        ],
        axis=1,
    )
    e = _unit_vectors(lon, b)
    v_l = np.einsum("ni,ni->n", e["l"], vel)
    v_b = np.einsum("ni,ni->n", e["b"], vel)
    v_r = np.einsum("ni,ni->n", e["r"], vel)
    d_kpc = d / 1000.0
    icrs = SkyCoord(
        l=lon * u.deg,
        b=b * u.deg,
        pm_l_cosb=(v_l / (KAPPA * d_kpc)) * u.mas / u.yr,
        pm_b=(v_b / (KAPPA * d_kpc)) * u.mas / u.yr,
        frame="galactic",
    ).icrs
    m_g = rng.uniform(-1.5, 5.5, n)
    return {
        "source_id": np.arange(n, dtype=np.int64) + 1,
        "ra": np.asarray(icrs.ra.deg),
        "dec": np.asarray(icrs.dec.deg),
        "parallax": 1000.0 / d,
        "parallax_error": np.full(n, 0.02),
        "pmra": np.asarray(icrs.pm_ra_cosdec.to_value(u.mas / u.yr)),
        "pmdec": np.asarray(icrs.pm_dec.to_value(u.mas / u.yr)),
        "phot_g_mean_mag": m_g + 5.0 * np.log10(d) - 5.0,
        "bp_rp": rng.uniform(0.0, 2.0, n),
        "ruwe": np.full(n, 1.0),
        "duplicated_source": np.zeros(n, dtype=bool),
        "radial_velocity": v_r + rng.normal(0.0, 1.0, n) * rv_error_km_s,
        "radial_velocity_error": np.full(n, rv_error_km_s),
    }

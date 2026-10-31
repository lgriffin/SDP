"""UC-04: Oort's 1927 detection of galactic rotation, rerun with Gaia DR3 (docs/protocols/uc04-oort-1927.md).

Part A checks the paper's own arithmetic. Part B fits the local velocity field to Gaia proper motions,
Part C to Gaia radial velocities (Oort's own method), both with the generalised Oort constants:

    v_x = (K + C) x + (A - B) y - U,    v_y = (A + B) x + (K - C) y - V,    v_z = -W

in heliocentric axes, x toward l = 0, y toward l = 90 deg, z toward the north galactic pole, with
(U, V, W) the Sun's own motion. A and B are then Oort's: for circular rotation V_c(R),
A = (V_c/R - dV_c/dR) / 2 and B = -(V_c/R + dV_c/dR) / 2. A star's radial velocity is
d cos^2 b (K + A sin 2l + C cos 2l) and its longitude velocity d cos b (B + A cos 2l - C sin 2l), so
A sin 2l + C cos 2l is Oort's rA sin 2(l - l0) with amplitude hypot(A, C).

Velocities are km/s and distances kpc, so A, B, C, K are km/s/kpc. Oort's km/s/pc are 1000 times smaller.
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

import astropy.units as u
import numpy as np
from astropy.constants import G as G_CONST
from astropy.coordinates import Galactic, SkyCoord
from numpy.typing import NDArray

CHOICES_FILE = "oort1927.json"
PROTOCOL_ID = "uc04-oort-1927"
PARAMETERS = ("U", "V", "W", "A", "B", "C", "K")
RV_PARAMETERS = ("U", "V", "W", "A", "C", "K")
#: km/s for 1 mas/yr at 1 kpc (one astronomical unit per year), from astropy's units
KAPPA = float((1 * u.mas / u.yr * u.kpc).to(u.km / u.s, equivalencies=u.dimensionless_angles()).value)
#: G in pc (km/s)^2 per solar mass, from astropy's constants
G_PC = float(G_CONST.to(u.pc * u.km**2 / u.s**2 / u.Msun).value)

Array = NDArray[np.float64]


def choices_text() -> str:
    return resources.files("sdp_stellar").joinpath(CHOICES_FILE).read_text("utf-8")


def load_choices() -> dict[str, Any]:
    document: dict[str, Any] = json.loads(choices_text())
    return document


def choices_sha256() -> str:
    return hashlib.sha256(choices_text().encode("utf-8")).hexdigest()


# --- Part A: Oort's arithmetic ------------------------------------------------------------------


def radius_from_a(a_km_s_pc: float, v_km_s: float, k2_over_k1: float) -> float:
    """Oort's A = (V/4R)(1 - (R/K) dK/dR) with K = K1 (a point mass, ~R^-2) + K2 (~R) gives
    A = (3V/4R) K1/(K1 + K2); solved for R in pc."""
    return 0.75 * v_km_s / a_km_s_pc / (1.0 + k2_over_k1)


def arithmetic(choices: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Part A: each figure recomputed from the paper's own inputs, beside the printed one. Nothing is asserted;
    a difference is reported for a reader to judge."""
    o = (choices or load_choices())["oort"]
    rows = o["table2_A_km_s_pc"]
    a = np.array([r["A"] for r in rows])
    w = 1.0 / np.array([r["error"] for r in rows]) ** 2
    mean, err = float((w * a).sum() / w.sum()), float(1.0 / math.sqrt(w.sum()))
    v, a_tot = float(o["assumed_V_km_s"]), float(o["total_A_km_s_pc"])
    radii = {
        "printed": {"k2_over_k1": o["K2_over_K1"], "R_pc": radius_from_a(a_tot, v, o["K2_over_K1"])},
        "after_note": {
            "k2_over_k1": o["K2_over_K1_after_note"],
            "R_pc": radius_from_a(a_tot, v, o["K2_over_K1_after_note"]),
        },
        "k2_zero": {"k2_over_k1": 0.0, "R_pc": radius_from_a(a_tot, v, 0.0)},
    }
    radii["printed"]["R_pc_printed"] = o["R_pc_printed"]
    radii["after_note"]["R_pc_printed"] = o["R_pc_after_note"]
    radii["k2_zero"]["R_pc_printed"] = o["R_pc_if_K2_zero"]
    # the point-mass part of the force at the Sun: G M / R^2 = (V^2 / R) K1 / (K1 + K2)
    r_printed = float(o["R_pc_printed"])
    mass = v**2 * r_printed / G_PC / (1.0 + o["K2_over_K1"])
    kep = o["keplerian_example"]
    kep_a = 0.75 * kep["V_km_s"] / kep["R_pc"]  # all mass at the centre: A = 3V/4R
    return {
        "weighted_mean_A_km_s_pc": {
            "computed": mean,
            "computed_error": err,
            "printed": a_tot,
            "printed_error": o["total_A_error_km_s_pc"],
            "difference": mean - a_tot,
            "weights": "1 / error^2 over the ten rows of Table 2",
        },
        "rA_times_parallax": [
            {
                "group": r["group"],
                "computed_A": r["rA_km_s"] * r["mean_parallax_arcsec"],
                "printed_A": r["A"],
            }
            for r in o["table2_rA_times_parallax"]
        ],
        "radius_from_A": radii,
        "central_mass_solar": {
            "computed": mass,
            "printed_at_least": o["central_mass_solar_at_least"],
            "from": "V^2 R / G times K1/(K1 + K2), with R and K2/K1 as printed",
        },
        "keplerian_semi_amplitude_km_s": {
            "computed": kep_a * kep["r_pc"],
            "printed_over": kep["semi_amplitude_over_km_s"],
        },
    }


# --- the stars ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Stars:
    l_deg: Array
    b_deg: Array
    d_kpc: Array
    v_l: Array  # km/s
    v_b: Array
    v_r: Array | None
    g: Array
    bp_rp: Array

    def __len__(self) -> int:
        return int(self.d_kpc.size)

    def take(self, mask: NDArray[np.bool_]) -> Stars:
        return Stars(
            self.l_deg[mask],
            self.b_deg[mask],
            self.d_kpc[mask],
            self.v_l[mask],
            self.v_b[mask],
            None if self.v_r is None else self.v_r[mask],
            self.g[mask],
            self.bp_rp[mask],
        )


@dataclass(frozen=True)
class Scenario:
    shell_kpc: tuple[float, float]
    b_max_deg: float
    g_max: float
    parallax_zero_point_mas: float
    population: str

    @property
    def label(self) -> str:
        lo, hi = self.shell_kpc
        return (
            f"d={lo:g}-{hi:g}kpc |b|<{self.b_max_deg:g} G<{self.g_max:g} "
            f"zp={self.parallax_zero_point_mas:g} {self.population}"
        )

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> Scenario:
        lo, hi = doc["shell_kpc"]
        return cls(
            (float(lo), float(hi)),
            float(doc["b_max_deg"]),
            float(doc["g_max"]),
            float(doc["parallax_zero_point_mas"]),
            str(doc["population"]),
        )


def scenarios(choices: Mapping[str, Any] | None = None) -> list[Scenario]:
    s = (choices or load_choices())["scenarios"]
    return [
        Scenario((float(sh[0]), float(sh[1])), float(b), float(g), float(zp), str(pop))
        for sh, b, g, zp, pop in itertools.product(
            s["shell_kpc"], s["b_max_deg"], s["g_max"], s["parallax_zero_point_mas"], s["population"]
        )
    ]


def baseline(choices: Mapping[str, Any] | None = None) -> Scenario:
    return Scenario.from_document((choices or load_choices())["baseline"])


def _col(columns: Mapping[str, Any], name: str) -> Array:
    return np.asarray(columns[name], dtype=np.float64)


def quality_mask(columns: Mapping[str, Any], choices: Mapping[str, Any] | None = None) -> NDArray[np.bool_]:
    """The cuts every scenario shares: RUWE, parallax over its error, not a duplicate, finite motions."""
    sel = (choices or load_choices())["selection"]
    with np.errstate(invalid="ignore", divide="ignore"):
        plx, err = _col(columns, "parallax"), _col(columns, "parallax_error")
        keep = (_col(columns, "ruwe") < sel["ruwe_max"]) & (plx / err > sel["parallax_over_error_min"])
        keep &= np.isfinite(_col(columns, "pmra")) & np.isfinite(_col(columns, "pmdec"))
        keep &= np.isfinite(_col(columns, "phot_g_mean_mag"))
    dup = np.asarray(columns["duplicated_source"])
    keep &= np.asarray(dup == sel["duplicated_source"], dtype=bool)
    return np.asarray(keep, dtype=bool)


def observe(columns: Mapping[str, Any], zero_point_mas: float) -> Stars:
    """Galactic positions, distances 1/(parallax - zero point) and velocities in km/s."""
    if _col(columns, "ra").size == 0:  # astropy cannot transform an empty set of motions
        empty = np.zeros(0)
        rv0 = empty if "radial_velocity" in columns else None
        return Stars(empty, empty, empty, empty, empty, rv0, empty, empty)
    pm = SkyCoord(
        ra=_col(columns, "ra") * u.deg,
        dec=_col(columns, "dec") * u.deg,
        pm_ra_cosdec=_col(columns, "pmra") * u.mas / u.yr,
        pm_dec=_col(columns, "pmdec") * u.mas / u.yr,
        frame="icrs",
    ).transform_to(Galactic())
    d = 1.0 / (_col(columns, "parallax") - zero_point_mas)
    rv = _col(columns, "radial_velocity") if "radial_velocity" in columns else None
    return Stars(
        np.asarray(pm.l.deg, dtype=np.float64),
        np.asarray(pm.b.deg, dtype=np.float64),
        d,
        KAPPA * np.asarray(pm.pm_l_cosb.to_value(u.mas / u.yr)) * d,
        KAPPA * np.asarray(pm.pm_b.to_value(u.mas / u.yr)) * d,
        rv,
        _col(columns, "phot_g_mean_mag"),
        _col(columns, "bp_rp") if "bp_rp" in columns else np.full(d.size, np.nan),
    )


def scenario_mask(
    stars: Stars, scenario: Scenario, choices: Mapping[str, Any] | None = None, *, radial: bool = False
) -> NDArray[np.bool_]:
    lo, hi = scenario.shell_kpc
    keep = (stars.d_kpc >= lo) & (stars.d_kpc <= hi) & (np.abs(stars.b_deg) < scenario.b_max_deg)
    keep &= stars.g < scenario.g_max
    if scenario.population == "blue":
        with np.errstate(invalid="ignore"):
            keep &= stars.bp_rp < (choices or load_choices())["selection"]["blue_bp_rp_max"]
    elif scenario.population != "all":
        raise ValueError(f"unknown population {scenario.population!r}")
    if radial:
        if stars.v_r is None:
            raise ValueError("no radial velocities")
        keep &= np.isfinite(stars.v_r)
    return np.asarray(keep, dtype=bool)


# --- the velocity field -------------------------------------------------------------------------

#: d(v_x, v_y)/d(x, y) for each Oort constant
_GRADIENT = {
    "A": np.array([[0.0, 1.0], [1.0, 0.0]]),
    "B": np.array([[0.0, -1.0], [1.0, 0.0]]),
    "C": np.array([[1.0, 0.0], [0.0, -1.0]]),
    "K": np.array([[1.0, 0.0], [0.0, 1.0]]),
}


def _unit_vectors(l_deg: Array, b_deg: Array) -> dict[str, Array]:
    lr, br = np.radians(l_deg), np.radians(b_deg)
    cl, sl, cb, sb = np.cos(lr), np.sin(lr), np.cos(br), np.sin(br)
    return {
        "r": np.stack([cb * cl, cb * sl, sb], axis=-1),
        "l": np.stack([-sl, cl, np.zeros_like(cl)], axis=-1),
        "b": np.stack([-sb * cl, -sb * sl, cb], axis=-1),
    }


def design(stars: Stars, components: Sequence[str], parameters: Sequence[str]) -> tuple[Array, Array]:
    """Rows (star-major, one per component) of the linear model, and the observed velocities."""
    e = _unit_vectors(stars.l_deg, stars.b_deg)
    r_xy = stars.d_kpc[:, None] * e["r"][:, :2]
    observed = {"l": stars.v_l, "b": stars.v_b, "r": stars.v_r}
    cols: list[Array] = []
    blocks = []
    for comp in components:
        unit = e[comp]
        row = []
        for p in parameters:
            if p in ("U", "V", "W"):
                row.append(-unit[:, "UVW".index(p)])
            else:
                row.append(np.einsum("ni,ij,nj->n", unit[:, :2], _GRADIENT[p], r_xy))
        blocks.append(np.stack(row, axis=-1))
        obs = observed[comp]
        if obs is None:
            raise ValueError(f"no {comp} velocities")
        cols.append(obs)
    x = np.stack(blocks, axis=1).reshape(-1, len(parameters))
    y = np.stack(cols, axis=1).reshape(-1)
    return x, y


@dataclass(frozen=True)
class Fit:
    parameters: tuple[str, ...]
    values: Array
    covariance: Array
    n_stars: int
    n_clipped: int
    #: which of the stars given were kept after clipping (not written to the report)
    kept: NDArray[np.bool_] | None = None

    def value(self, p: str) -> float:
        return float(self.values[self.parameters.index(p)])

    def error(self, p: str) -> float:
        i = self.parameters.index(p)
        return float(math.sqrt(self.covariance[i, i]))

    def derived(self) -> dict[str, float | None]:
        a, c = self.value("A"), self.value("C")
        i_a, i_c = self.parameters.index("A"), self.parameters.index("C")
        amp = math.hypot(a, c)
        sub = self.covariance[np.ix_([i_a, i_c], [i_a, i_c])]
        out: dict[str, float | None]
        if amp == 0.0:
            # no double wave at all: its phase is undefined, and the amplitude's error is the larger axis
            out = {
                "amplitude": 0.0,
                "amplitude_error": float(math.sqrt(np.linalg.eigvalsh(sub).max())),
                "l0_deg": None,
                "l0_error_deg": None,
            }
        else:
            # gradients of the amplitude and of l0 = -atan2(C, A) / 2 with respect to (A, C)
            g_amp = np.array([a, c]) / amp
            g_l0 = -0.5 * np.array([-c, a]) / amp**2
            out = {
                "amplitude": amp,
                "amplitude_error": float(math.sqrt(g_amp @ sub @ g_amp)),
                "l0_deg": math.degrees(-0.5 * math.atan2(c, a)),
                "l0_error_deg": math.degrees(float(math.sqrt(g_l0 @ sub @ g_l0))),
            }
        if "B" in self.parameters:
            i_b = self.parameters.index("B")
            omega = a - self.value("B")
            var = self.covariance[i_a, i_a] + self.covariance[i_b, i_b] - 2 * self.covariance[i_a, i_b]
            out["omega"] = omega
            out["omega_error"] = float(math.sqrt(var))
        return out

    def to_document(self) -> dict[str, Any]:
        doc: dict[str, Any] = {p: {"value": self.value(p), "error": self.error(p)} for p in self.parameters}
        doc["n_stars"] = self.n_stars
        doc["n_clipped"] = self.n_clipped
        doc["derived"] = self.derived()
        return doc


def fit(
    stars: Stars,
    components: Sequence[str] = ("l", "b"),
    parameters: Sequence[str] = PARAMETERS,
    choices: Mapping[str, Any] | None = None,
) -> Fit:
    """Least squares, with stars whose residual in any component exceeds the declared multiple of that
    component's robust scatter dropped and the fit repeated. Errors from the sandwich clustered by star."""
    f = (choices or load_choices())["fit"]
    if len(stars) < int(f["min_stars"]):
        raise ValueError(f"{len(stars)} stars, fewer than the declared {f['min_stars']}")
    x, y = design(stars, components, parameters)
    n_comp, n_par = len(components), len(parameters)
    keep = np.ones(len(stars), dtype=bool)
    theta = np.zeros(n_par)
    for _ in range(int(f["clip_passes"])):
        rows = np.repeat(keep, n_comp)
        theta, *_ = np.linalg.lstsq(x[rows], y[rows], rcond=None)
        resid = (y - x @ theta).reshape(-1, n_comp)
        scale = 1.4826 * np.median(np.abs(resid[keep] - np.median(resid[keep], axis=0)), axis=0)
        new = np.all(np.abs(resid) <= f["clip_mad_multiple"] * scale, axis=1)
        if np.array_equal(new, keep):
            break
        keep = new
    if int(keep.sum()) < int(f["min_stars"]):
        raise ValueError(f"{int(keep.sum())} stars after clipping, fewer than the declared {f['min_stars']}")
    # the reported fit is always the one on the stars finally kept
    rows = np.repeat(keep, n_comp)
    xk = x[rows]
    theta, *_ = np.linalg.lstsq(xk, y[rows], rcond=None)
    resid = (y[rows] - xk @ theta).reshape(-1, n_comp)
    score = np.einsum("nci,nc->ni", xk.reshape(-1, n_comp, n_par), resid)
    bread = np.linalg.inv(xk.T @ xk)
    cov = bread @ (score.T @ score) @ bread
    return Fit(tuple(parameters), theta, cov, int(keep.sum()), int((~keep).sum()), keep)


# --- verdict ------------------------------------------------------------------------------------


def detection(
    fits: Mapping[str, Fit | str], choices: Mapping[str, Any] | None = None, *, with_b: bool = True
) -> dict[str, Any]:
    """UC04-H1 when every fit has A above zero by the declared sigmas (and B below); UC04-H0 when every
    fit has A within the null sigmas of zero; inconclusive otherwise, or when any fit failed."""
    d = (choices or load_choices())["decision"]
    failed = sorted(k for k, v in fits.items() if isinstance(v, str))
    ok = {k: v for k, v in fits.items() if isinstance(v, Fit)}
    detected = sorted(
        k
        for k, f in ok.items()
        if f.value("A") >= d["detect_sigma"] * f.error("A")
        and (not with_b or f.value("B") <= -d["detect_sigma"] * f.error("B"))
    )
    null = sorted(k for k, f in ok.items() if abs(f.value("A")) < d["null_sigma"] * f.error("A"))
    if failed or not ok:
        status = "inconclusive"
    elif len(detected) == len(ok):
        status = "supports_UC04_H1"
    elif len(null) == len(ok):
        status = "supports_UC04_H0"
    else:
        status = "inconclusive"
    return {"status": status, "scenarios_detected": detected, "scenarios_null": null, "scenarios_failed": failed}


def comparison(fits: Mapping[str, Fit | str], choices: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Oort's total A (km/s/pc, times 1000) against the fitted amplitude hypot(A, C): his A is an amplitude
    with its own l0. Agrees when every fit is within the declared sigmas; disagrees when none is."""
    c = choices or load_choices()
    o = c["oort"]
    a0, e0 = 1000.0 * o["total_A_km_s_pc"], 1000.0 * o["total_A_error_km_s_pc"]
    z = {}
    for k, f in fits.items():
        if isinstance(f, Fit):
            dv = f.derived()
            amp, err = dv["amplitude"], dv["amplitude_error"]
            assert amp is not None and err is not None  # always set, even for a zero amplitude
            z[k] = (amp - a0) / math.hypot(err, e0)
    limit = c["decision"]["agree_sigma"]
    within = sorted(k for k, v in z.items() if abs(v) < limit)
    if not z or len(z) < len(fits):
        status = "mixed"
    elif len(within) == len(z):
        status = "agrees_with_oort"
    elif not within:
        status = "disagrees_with_oort"
    else:
        status = "mixed"
    return {"status": status, "oort_A_km_s_kpc": a0, "oort_error": e0, "z": z, "scenarios_within": within}


def _fit_or_reason(
    stars: Stars, components: Sequence[str], parameters: Sequence[str], choices: Mapping[str, Any]
) -> Fit | str:
    try:
        return fit(stars, components, parameters, choices)
    except (ValueError, np.linalg.LinAlgError) as exc:
        return str(exc)


def method(
    columns: Mapping[str, Any],
    components: Sequence[str],
    parameters: Sequence[str],
    choices: Mapping[str, Any],
    *,
    baseline_only: bool = False,
) -> dict[str, Any]:
    """One method (proper motions or radial velocities) over the baseline and every scenario."""
    good = quality_mask(columns, choices)
    cols = {k: np.asarray(v)[good] for k, v in columns.items()}
    base = baseline(choices)
    grid = [base] if baseline_only else scenarios(choices)
    if base not in grid:
        grid.insert(0, base)
    by_zp: dict[float, Stars] = {}
    fits: dict[str, Fit | str] = {}
    for s in grid:
        if s.parallax_zero_point_mas not in by_zp:
            by_zp[s.parallax_zero_point_mas] = observe(cols, s.parallax_zero_point_mas)
        stars = by_zp[s.parallax_zero_point_mas]
        fits[s.label] = _fit_or_reason(
            stars.take(scenario_mask(stars, s, choices, radial="r" in components)), components, parameters, choices
        )
    with_b = "B" in parameters
    reading = detection(fits, choices, with_b=with_b)
    if baseline_only:
        reading = {**reading, "status": "inconclusive", "quick_look": True}
    base_fit = fits[base.label]
    doc: dict[str, Any] = {
        "components": list(components),
        "baseline_label": base.label,
        "baseline": base_fit.to_document() if isinstance(base_fit, Fit) else {"failed": base_fit},
        "scenarios": {k: (v.to_document() if isinstance(v, Fit) else {"failed": v}) for k, v in fits.items()},
        "reading": reading,
        "comparison": comparison(fits, choices),
    }
    if isinstance(base_fit, Fit) and with_b:
        omega = base_fit.derived()["omega"]
        assert omega is not None
        v = float(choices["oort"]["assumed_V_km_s"])
        o = choices["oort"]
        doc["oort_radius"] = {
            "R_kpc_from_V_over_omega": v / omega,
            "R_kpc_from_A": {
                name: radius_from_a(base_fit.value("A") / 1000.0, v, ratio) / 1000.0
                for name, ratio in (
                    ("printed", o["K2_over_K1"]),
                    ("after_note", o["K2_over_K1_after_note"]),
                    ("k2_zero", 0.0),
                )
            },
            "assumed_V_km_s": v,
        }
    return doc


def run(
    columns: Mapping[str, Any],
    rv_columns: Mapping[str, Any] | None = None,
    *,
    choices: Mapping[str, Any] | None = None,
    baseline_only: bool = False,
) -> dict[str, Any]:
    """Part A, the proper-motion method (it decides UC04-H1/H0) and, given radial velocities, Oort's own."""
    c = dict(choices or load_choices())
    report: dict[str, Any] = {
        "protocol_id": PROTOCOL_ID,
        "version": c["version"],
        "committed_choices_sha256": choices_sha256(),
        "choices_match_committed": c == load_choices(),
        "quick_look": baseline_only,
        "arithmetic": arithmetic(c),
        "proper_motions": method(columns, ("l", "b"), PARAMETERS, c, baseline_only=baseline_only),
    }
    report["verdict"] = report["proper_motions"]["reading"]
    report["radial_velocities"] = (
        method(rv_columns, ("r",), RV_PARAMETERS, c, baseline_only=baseline_only)
        if rv_columns is not None
        else {"not_run": "no radial velocities given; build the rvs bulk subset (sdp archive bulk --subset rvs)"}
    )
    return report


# --- synthetic galaxies -------------------------------------------------------------------------


def synthetic_galaxy(
    *,
    n: int,
    r0_kpc: float = 8.0,
    v0_km_s: float = 220.0,
    slope: float = 0.0,
    sun_uvw: tuple[float, float, float] = (10.0, 12.0, 7.0),
    dispersion_km_s: float = 20.0,
    d_max_kpc: float = 1.5,
    centre_offset_deg: float = 0.0,
    parallax_error_mas: float = 0.02,
    seed: int = 1927,
) -> dict[str, Any]:
    """Stars on circular orbits of V_c(R) = v0 (R / r0)^slope about a centre at galactic longitude
    ``centre_offset_deg``, with an isotropic dispersion, seen from the Sun at r0 moving at V_c(r0) plus
    ``sun_uvw``. Returns native gaia_source columns, measured exactly (the parallax error is only declared)."""
    rng = np.random.default_rng(seed)
    # uniform in a sphere, kept out of the innermost 50 pc
    pos = rng.normal(size=(n, 3))
    pos /= np.linalg.norm(pos, axis=1)[:, None]
    pos *= (d_max_kpc * rng.uniform((0.05 / d_max_kpc) ** 3, 1.0, n) ** (1 / 3))[:, None]
    phi = math.radians(centre_offset_deg)
    to_centre = np.array([math.cos(phi), math.sin(phi), 0.0])
    ahead = np.array([-math.sin(phi), math.cos(phi), 0.0])  # the direction of rotation at the Sun

    def circular(p: Array) -> Array:
        rel = p - r0_kpc * to_centre[None, :]  # galactocentric position
        rel[:, 2] = 0.0
        radius = np.linalg.norm(rel, axis=1)
        vc = v0_km_s * (radius / r0_kpc) ** slope
        # at the Sun, rel = -r0 * to_centre, and the motion is along +ahead
        tangent = np.cross(np.array([0.0, 0.0, 1.0]), rel) / radius[:, None]
        sign = np.sign(np.dot(np.cross([0.0, 0.0, 1.0], -to_centre), ahead))
        return np.asarray(sign * vc[:, None] * tangent)

    sun = circular(np.zeros((1, 3)))[0]
    sun_pec = sun_uvw[0] * to_centre + sun_uvw[1] * ahead + np.array([0.0, 0.0, sun_uvw[2]])
    vel = circular(pos) + rng.normal(scale=dispersion_km_s, size=(n, 3)) - sun - sun_pec
    d = np.linalg.norm(pos, axis=1)
    lon = np.degrees(np.arctan2(pos[:, 1], pos[:, 0])) % 360.0
    b = np.degrees(np.arcsin(pos[:, 2] / d))
    e = _unit_vectors(lon, b)
    v_l = np.einsum("ni,ni->n", e["l"], vel)
    v_b = np.einsum("ni,ni->n", e["b"], vel)
    v_r = np.einsum("ni,ni->n", e["r"], vel)
    gal = SkyCoord(
        l=lon * u.deg,
        b=b * u.deg,
        pm_l_cosb=(v_l / (KAPPA * d)) * u.mas / u.yr,
        pm_b=(v_b / (KAPPA * d)) * u.mas / u.yr,
        frame="galactic",
    ).icrs
    parallax = 1.0 / d
    return {
        "source_id": np.arange(n, dtype=np.int64) + 1,
        "ra": np.asarray(gal.ra.deg),
        "dec": np.asarray(gal.dec.deg),
        "parallax": parallax,
        "parallax_error": np.full(n, parallax_error_mas),
        "pmra": np.asarray(gal.pm_ra_cosdec.to_value(u.mas / u.yr)),
        "pmdec": np.asarray(gal.pm_dec.to_value(u.mas / u.yr)),
        "phot_g_mean_mag": rng.uniform(8.0, 14.9, n),
        "bp_rp": rng.uniform(-0.2, 2.5, n),
        "ruwe": np.full(n, 1.0),
        "duplicated_source": np.zeros(n, dtype=bool),
        "radial_velocity": v_r,
    }


def oort_constants(*, r0_kpc: float, v0_km_s: float, slope: float) -> dict[str, float]:
    """A and B of V_c(R) = v0 (R / r0)^slope at r0, in km/s/kpc."""
    dvdr = slope * v0_km_s / r0_kpc
    return {"A": 0.5 * (v0_km_s / r0_kpc - dvdr), "B": -0.5 * (v0_km_s / r0_kpc + dvdr)}

"""UC-03: Einstein's 1921 globular-cluster calculation, rerun with Gaia (docs/protocols/uc03-einstein-1921.md).

Einstein (1921, CPAE 7 Doc. 56) applied the virial theorem to the cluster in Hercules (M13) with the
Plummer density law, his eq. (3), and borrowed every input: ``N = 2000`` stars of ``m = 15`` suns
moving at ``v = 26 km/s`` (the speed of bright stars near the Sun). This module

- checks the paper's arithmetic (part A): for eq. (3) the factor in eq. (4) is ``A = 3*pi/32``,
  where Einstein printed "etwa 0,6" (``3*pi/16``, each pair counted twice);
- fits his own density law and the isotropic Plummer kinematics to Gaia positions, proper motions
  and parallaxes, giving the cluster's dynamical mass, its light in G, and the mass-to-light ratio
  that decides whether his conclusion about non-luminous mass holds (UC03-H1: below 10).

Pure functions on NumPy arrays; reading Gaia is the CLI's job.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
from dataclasses import asdict, dataclass
from importlib import resources
from typing import Any

import astropy.units as u
import numpy as np
from numpy.typing import NDArray
from scipy.optimize import minimize

from sdp_relativity.constants import recorded_constants

Array = NDArray[np.float64]

#: km/s per (mas/yr * kpc).
KMS_PER_MASYR_KPC = 4.740470463533348
LIGHT_YEAR_CM = float((1 * u.lyr).to(u.cm).value)
CHOICES_FILE = "einstein1921.json"


def choices_text() -> str:
    return resources.files("sdp_relativity").joinpath(CHOICES_FILE).read_text("utf-8")


def load_choices() -> dict[str, Any]:
    document: dict[str, Any] = json.loads(choices_text())
    return document


def choices_sha256() -> str:
    return hashlib.sha256(choices_text().encode("utf-8")).hexdigest()


def gravitational_constant_pc() -> float:
    """G in pc (km/s)^2 per solar mass, from the recorded constants."""
    c = recorded_constants()
    return float((c.G * c.M_sun).to(u.pc * u.km**2 / u.s**2).value)


# ---------------------------------------------------------------------------------------------
# Part A: the 1921 arithmetic


def plummer_a() -> float:
    """The factor A of Einstein's eq. (4) for his eq. (3): |Phi| = A k N^2 m^2 / a."""
    return 3 * math.pi / 32


def plummer_a_numeric(shells: int = 400_000, r_max: float = 400.0) -> float:
    """|Phi| for eq. (3) with k = N m = a = 1, summed shell by shell: an independent check of plummer_a."""
    h = r_max / shells
    r = (np.arange(shells) + 0.5) * h
    dm = 4 * np.pi * r**2 * (3 / (4 * np.pi)) * (1 + r**2) ** -2.5 * h
    inside = np.concatenate(([0.0], np.cumsum(dm)[:-1]))
    return float(np.sum((inside + dm / 2) * dm / r))


def eq5_two_a_cm(n: float, m_solar: float, v_kms: float, coefficient: float) -> float:
    """Einstein's eq. (5), 2a = coefficient * k N m / v^2, in cm."""
    c = recorded_constants()
    gm = float((c.G * c.M_sun).to(u.cm**3 / u.s**2).value)
    return coefficient * gm * n * m_solar / (v_kms * 1e5) ** 2


def arithmetic() -> dict[str, Any]:
    """Part A: Einstein's numbers as printed, recomputed, and with A = 3 pi / 32."""
    e = load_choices()["einstein_1921"]
    n, m, v, factor = float(e["N"]), float(e["m_solar"]), float(e["v_kms"]), float(e["star_factor"])
    printed_coefficient = float(e["eq5_coefficient_printed"])
    a = plummer_a()

    def row(coefficient: float) -> dict[str, float]:
        cm = eq5_two_a_cm(n, m, v, coefficient)
        cm100 = eq5_two_a_cm(n * factor, m, v, coefficient)
        return {
            "coefficient": coefficient,
            "two_a_cm": cm,
            "two_a_ly": cm / LIGHT_YEAR_CM,
            "two_a_with_factor_ly": cm100 / LIGHT_YEAR_CM,
        }

    return {
        "A_printed": float(e["A_printed"]),
        "A_plummer": a,
        "A_plummer_numeric": plummer_a_numeric(),
        "A_double_counted": 2 * a,
        "printed": {
            "two_a_cm": float(e["two_a_printed_cm"]),
            "two_a_ly": float(e["two_a_printed_ly"]),
            "two_a_with_factor_ly": float(e["two_a_with_factor_printed_ly"]),
            "printed_cm_in_ly": float(e["two_a_printed_cm"]) / LIGHT_YEAR_CM,
        },
        "einstein_coefficient": row(printed_coefficient),
        "corrected_coefficient": row(2 * a),
        "radius_lower_bound_ly": float(e["radius_lower_bound_ly"]),
        "constants": recorded_constants().version,
    }


# ---------------------------------------------------------------------------------------------
# The isotropic Plummer sphere


def central_projected_dispersion_sq(mass: float, a_pc: float) -> float:
    """sigma_0^2 = (3 pi / 64) G M / a, the line-of-sight (or one sky axis) dispersion at R = 0."""
    return 3 * math.pi / 64 * gravitational_constant_pc() * mass / a_pc


def mean_square_speed(mass: float, a_pc: float) -> float:
    """<v^2> = (3 pi / 32) G M / a, mass-weighted over the cluster: Einstein's v squared."""
    return 3 * math.pi / 32 * gravitational_constant_pc() * mass / a_pc


def mass_from_dispersion(sigma0_kms: float, a_pc: float) -> float:
    """Inverse of central_projected_dispersion_sq."""
    return 64 * a_pc * sigma0_kms**2 / (3 * math.pi * gravitational_constant_pc())


def annulus_fraction(r_in: float, r_max: float, a: float) -> float:
    """Fraction of a Plummer cluster's stars seen in projection between r_in and r_max (same units as a)."""
    return r_max**2 / (r_max**2 + a**2) - r_in**2 / (r_in**2 + a**2)


# ---------------------------------------------------------------------------------------------
# Stars


@dataclass(frozen=True)
class Stars:
    """Selected stars on the tangent plane about the cone's starting position (degrees, mas, mas/yr)."""

    xi: Array
    eta: Array
    pmra: Array
    pmdec: Array
    pmra_error: Array
    pmdec_error: Array
    pm_corr: Array
    parallax: Array
    parallax_error: Array
    g: Array

    def __len__(self) -> int:
        return int(self.xi.size)

    def take(self, index: NDArray[np.intp] | NDArray[np.bool_]) -> Stars:
        return Stars(**{k: v[index] for k, v in asdict(self).items()})


def tangent_plane(ra: Array, dec: Array, ra0: float, dec0: float) -> tuple[Array, Array]:
    """Gnomonic projection about (ra0, dec0), in degrees."""
    ra_r, dec_r, ra0_r, dec0_r = np.radians(ra), np.radians(dec), math.radians(ra0), math.radians(dec0)
    cos_c = np.sin(dec0_r) * np.sin(dec_r) + np.cos(dec0_r) * np.cos(dec_r) * np.cos(ra_r - ra0_r)
    xi = np.cos(dec_r) * np.sin(ra_r - ra0_r) / cos_c
    eta = (np.cos(dec0_r) * np.sin(dec_r) - np.sin(dec0_r) * np.cos(dec_r) * np.cos(ra_r - ra0_r)) / cos_c
    return np.degrees(xi), np.degrees(eta)


SELECTION_ORDER = ("finite", "ruwe", "astrometric_params_solved", "duplicated_source", "g_max")


def select(columns: dict[str, Any], choices: dict[str, Any] | None = None) -> tuple[Stars, dict[str, int]]:
    """Apply the predeclared cuts to native gaia_source columns; returns the stars and the rows each cut removed."""
    choices = choices or load_choices()
    cuts, target = choices["selection"], choices["target"]

    def col(name: str) -> Array:
        return np.asarray(columns[name], dtype=np.float64)

    needed = ("ra", "dec", "pmra", "pmdec", "pmra_error", "pmdec_error", "parallax", "parallax_error")
    corr = np.nan_to_num(col("pmra_pmdec_corr"), nan=0.0)
    tests = {
        "finite": np.all([np.isfinite(col(n)) for n in (*needed, "phot_g_mean_mag")], axis=0)
        & (col("pmra_error") > 0)
        & (col("pmdec_error") > 0)
        & (col("parallax_error") > 0),
        "ruwe": np.nan_to_num(col("ruwe"), nan=np.inf) < float(cuts["ruwe_max"]),
        "astrometric_params_solved": np.isin(col("astrometric_params_solved"), cuts["astrometric_params_solved"]),
        "duplicated_source": ~np.asarray(columns["duplicated_source"], dtype=bool),
        "g_max": np.nan_to_num(col("phot_g_mean_mag"), nan=np.inf) < float(cuts["g_max"]),
    }
    keep = np.ones(col("ra").size, dtype=bool)
    removed: dict[str, int] = {}
    for name in SELECTION_ORDER:
        removed[name] = int(np.sum(keep & ~tests[name]))
        keep &= tests[name]
    xi, eta = tangent_plane(col("ra")[keep], col("dec")[keep], target["start_ra_deg"], target["start_dec_deg"])
    stars = Stars(
        xi=xi,
        eta=eta,
        pmra=col("pmra")[keep],
        pmdec=col("pmdec")[keep],
        pmra_error=col("pmra_error")[keep],
        pmdec_error=col("pmdec_error")[keep],
        pm_corr=corr[keep],
        parallax=col("parallax")[keep],
        parallax_error=col("parallax_error")[keep],
        g=col("phot_g_mean_mag")[keep],
    )
    return stars, removed


def find_centre(stars: Stars, window_deg: float = 0.1, pm_window: float = 1.5, rounds: int = 5) -> tuple[float, float]:
    """The cluster centre on the tangent plane: the mean position of stars near the current centre whose proper
    motion lies within ``pm_window`` mas/yr of their median, iterated from the starting position."""
    x0, y0 = 0.0, 0.0
    for _ in range(rounds):
        near = np.hypot(stars.xi - x0, stars.eta - y0) < window_deg
        if near.sum() < 10:
            break
        mx, my = np.median(stars.pmra[near]), np.median(stars.pmdec[near])
        pick = near & (np.hypot(stars.pmra - mx, stars.pmdec - my) < pm_window)
        x0, y0 = float(stars.xi[pick].mean()), float(stars.eta[pick].mean())
    return x0, y0


# ---------------------------------------------------------------------------------------------
# The mixture fit

PARAMS = (
    "log_a_deg",
    "logit_f",
    "mu_c_ra",
    "mu_c_dec",
    "log_sigma0",
    "mu_f_ra",
    "mu_f_dec",
    "log_s_f_ra",
    "log_s_f_dec",
    "atanh_rho_f",
    "plx_c",
    "plx_f",
    "log_tau_f",
)


@dataclass(frozen=True)
class Scenario:
    r_in_arcmin: float
    r_max_deg: float
    error_scale: float
    parallax_zero_point_mas: float
    a_g_mag: float

    @property
    def label(self) -> str:
        return (
            f"r_in={self.r_in_arcmin}' r_max={self.r_max_deg}deg err={self.error_scale} "
            f"zp={self.parallax_zero_point_mas} A_G={self.a_g_mag}"
        )


def scenarios(choices: dict[str, Any] | None = None) -> list[Scenario]:
    s = (choices or load_choices())["scenarios"]
    keys = ("r_in_arcmin", "r_max_deg", "error_scale", "parallax_zero_point_mas", "a_g_mag")
    return [Scenario(*map(float, values)) for values in itertools.product(*(s[k] for k in keys))]


def baseline(choices: dict[str, Any] | None = None) -> Scenario:
    b = (choices or load_choices())["baseline"]
    return Scenario(
        float(b["r_in_arcmin"]),
        float(b["r_max_deg"]),
        float(b["error_scale"]),
        float(b["parallax_zero_point_mas"]),
        float(b["a_g_mag"]),
    )


def _gauss2_logpdf(dx: Array, dy: Array, sxx: Array, syy: Array, sxy: Array) -> Array:
    det = sxx * syy - sxy**2
    quad = (syy * dx**2 - 2 * sxy * dx * dy + sxx * dy**2) / det
    return -0.5 * quad - 0.5 * np.log(det) - math.log(2 * math.pi)


def _gauss1_logpdf(d: Array, var: Array) -> Array:
    return -0.5 * d**2 / var - 0.5 * np.log(2 * math.pi * var)


@dataclass(frozen=True)
class _Data:
    r: Array
    pmra: Array
    pmdec: Array
    exx: Array
    eyy: Array
    exy: Array
    plx: Array
    plx_var: Array
    r_in: float
    r_max: float


def _components(theta: Array, d: _Data) -> tuple[Array, Array]:
    """Log of the cluster and field terms (weight included) for every star."""
    a = math.exp(theta[0])
    f = 1 / (1 + math.exp(-theta[1]))
    sigma0 = math.exp(theta[4])
    sfx, sfy, rho = math.exp(theta[7]), math.exp(theta[8]), math.tanh(theta[9])
    tau = math.exp(theta[12])
    norm_c = math.pi * a**2 * (1 / (1 + d.r_in**2 / a**2) - 1 / (1 + d.r_max**2 / a**2))
    log_sigma_c = -2 * np.log1p(d.r**2 / a**2) - math.log(norm_c)
    log_sigma_f = -math.log(math.pi * (d.r_max**2 - d.r_in**2))
    s2 = sigma0**2 / np.sqrt(1 + d.r**2 / a**2)
    pm_c = _gauss2_logpdf(d.pmra - theta[2], d.pmdec - theta[3], s2 + d.exx, s2 + d.eyy, d.exy)
    pm_f = _gauss2_logpdf(
        d.pmra - theta[5], d.pmdec - theta[6], sfx**2 + d.exx, sfy**2 + d.eyy, rho * sfx * sfy + d.exy
    )
    plx_c = _gauss1_logpdf(d.plx - theta[10], d.plx_var)
    plx_f = _gauss1_logpdf(d.plx - theta[11], tau**2 + d.plx_var)
    cluster = math.log(f) + log_sigma_c + pm_c + plx_c
    field = math.log1p(-f) + log_sigma_f + pm_f + plx_f
    return cluster, field


def _nll(theta: Array, d: _Data) -> float:
    if not np.all(np.isfinite(theta)) or abs(theta[9]) > 5 or abs(theta[1]) > 30:
        return 1e300
    cluster, field = _components(theta, d)
    return float(-np.sum(np.logaddexp(cluster, field)))


@dataclass(frozen=True)
class Fit:
    a_deg: float
    cluster_fraction: float
    mu_c: tuple[float, float]
    sigma0_masyr: float
    parallax_c_mas: float
    converged: bool
    message: str
    nll: float
    n_stars: int
    membership: Array
    centre: tuple[float, float]
    theta: Array


def _data(stars: Stars, centre: tuple[float, float], scenario: Scenario) -> tuple[_Data, NDArray[np.bool_]]:
    r = np.hypot(stars.xi - centre[0], stars.eta - centre[1])
    r_in, r_max = scenario.r_in_arcmin / 60, scenario.r_max_deg
    inside = (r >= r_in) & (r <= r_max)
    s2 = scenario.error_scale**2
    ex, ey = stars.pmra_error[inside], stars.pmdec_error[inside]
    data = _Data(
        r=r[inside],
        pmra=stars.pmra[inside],
        pmdec=stars.pmdec[inside],
        exx=s2 * ex**2,
        eyy=s2 * ey**2,
        exy=s2 * stars.pm_corr[inside] * ex * ey,
        plx=stars.parallax[inside],
        plx_var=s2 * stars.parallax_error[inside] ** 2,
        r_in=r_in,
        r_max=r_max,
    )
    return data, inside


def _start(d: _Data) -> Array:
    inner = d.r < np.quantile(d.r, 0.2)
    mx, my = np.median(d.pmra[inner]), np.median(d.pmdec[inner])
    sx = 1.4826 * np.median(np.abs(d.pmra - np.median(d.pmra))) + 0.5
    sy = 1.4826 * np.median(np.abs(d.pmdec - np.median(d.pmdec))) + 0.5
    tau = 1.4826 * np.median(np.abs(d.plx - np.median(d.plx))) + 0.1
    return np.array(
        [
            math.log(max(d.r_in, 0.02)),
            0.0,
            mx,
            my,
            math.log(0.2),
            float(np.median(d.pmra)),
            float(np.median(d.pmdec)),
            math.log(sx),
            math.log(sy),
            0.0,
            float(np.median(d.plx[inner])),
            float(np.median(d.plx)),
            math.log(tau),
        ]
    )


def fit(stars: Stars, centre: tuple[float, float], scenario: Scenario, start: Array | None = None) -> Fit:
    """Maximum-likelihood fit of the cluster + field mixture in the scenario's annulus."""
    d, inside = _data(stars, centre, scenario)
    if d.r.size < 50:
        raise ValueError(f"only {d.r.size} stars in the annulus for {scenario.label}; at least 50 are needed")
    theta0 = _start(d) if start is None else start
    result = minimize(_nll, theta0, args=(d,), method="L-BFGS-B", options={"maxiter": 5000, "maxfun": 100_000})
    # one restart from the optimum settles L-BFGS-B's finite-difference stopping
    result = minimize(_nll, result.x, args=(d,), method="L-BFGS-B", options={"maxiter": 5000, "maxfun": 100_000})
    theta = result.x
    cluster, field = _components(theta, d)
    membership = np.zeros(len(stars))
    membership[inside] = np.exp(cluster - np.logaddexp(cluster, field))
    return Fit(
        a_deg=math.exp(theta[0]),
        cluster_fraction=1 / (1 + math.exp(-theta[1])),
        mu_c=(float(theta[2]), float(theta[3])),
        sigma0_masyr=math.exp(theta[4]),
        parallax_c_mas=float(theta[10]),
        converged=bool(result.success) and bool(np.isfinite(result.fun)),
        message=str(result.message),
        nll=float(result.fun),
        n_stars=int(d.r.size),
        membership=membership,
        centre=centre,
        theta=theta,
    )


# ---------------------------------------------------------------------------------------------
# From the fit to Einstein's quantities


@dataclass(frozen=True)
class Physical:
    distance_pc: float
    a_pc: float
    two_a_ly: float
    sigma0_kms: float
    mass_solar: float
    mass_solar_einstein_a: float
    v_rms_kms: float
    light_g_solar: float
    mass_to_light: float
    members_bright: float
    in_units_of_einstein_stars: float


def physical(stars: Stars, f: Fit, scenario: Scenario, choices: dict[str, Any] | None = None) -> Physical:
    choices = choices or load_choices()
    plx = f.parallax_c_mas - scenario.parallax_zero_point_mas
    if plx <= 0:
        raise ValueError(f"the cluster parallax less the zero-point is {plx:.4f} mas, not positive")
    d_pc = 1000 / plx
    a_pc = math.radians(f.a_deg) * d_pc
    sigma0_kms = KMS_PER_MASYR_KPC * f.sigma0_masyr * d_pc / 1000
    mass = mass_from_dispersion(sigma0_kms, a_pc)
    # Einstein's A, 3 pi / 16, doubles |Phi| and so halves the mass the same motions imply
    mass_einstein = mass * plummer_a() / float(choices["einstein_1921"]["A_printed"])
    fraction = annulus_fraction(scenario.r_in_arcmin / 60, scenario.r_max_deg, f.a_deg)
    modulus = 5 * math.log10(d_pc) - 5
    m_sun = float(choices["light"]["abs_g_sun"])
    light = float(np.sum(f.membership * 10 ** (-0.4 * (stars.g - modulus - scenario.a_g_mag - m_sun)))) / fraction
    e = choices["einstein_1921"]
    return Physical(
        distance_pc=d_pc,
        a_pc=a_pc,
        two_a_ly=float((2 * a_pc * u.pc).to(u.lyr).value),
        sigma0_kms=sigma0_kms,
        mass_solar=mass,
        mass_solar_einstein_a=mass_einstein,
        v_rms_kms=math.sqrt(mean_square_speed(mass, a_pc)),
        light_g_solar=light,
        mass_to_light=mass / light,
        members_bright=float(f.membership.sum()) / fraction,
        in_units_of_einstein_stars=mass / float(e["m_solar"]),
    )


# ---------------------------------------------------------------------------------------------
# Scenarios, bootstrap, verdict


def bootstrap(
    stars: Stars, centre: tuple[float, float], scenario: Scenario, start: Array, resamples: int, seed: int
) -> list[Physical]:
    """Physical quantities for ``resamples`` star-resampled refits; the seed is recorded in the report."""
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(resamples):
        sample = stars.take(rng.integers(0, len(stars), len(stars)))
        f = fit(sample, centre, scenario, start=start)
        if f.converged:
            out.append(physical(sample, f, scenario))
    return out


def percentiles(values: list[Physical]) -> dict[str, dict[str, float]]:
    if not values:
        return {}
    keys = asdict(values[0]).keys()
    return {
        k: {
            "p16": float(np.percentile([getattr(v, k) for v in values], 16)),
            "p50": float(np.percentile([getattr(v, k) for v in values], 50)),
            "p84": float(np.percentile([getattr(v, k) for v in values], 84)),
        }
        for k in keys
    }


def verdict(
    baseline_band: dict[str, dict[str, float]], by_scenario: dict[str, float | None], threshold: float
) -> dict[str, Any]:
    """The predeclared decision rule on the mass-to-light ratio (protocol, Decision rule)."""
    failed = sorted(label for label, value in by_scenario.items() if value is None)
    values = {k: v for k, v in by_scenario.items() if v is not None}
    below = sorted(k for k, v in values.items() if v < threshold)
    above = sorted(k for k, v in values.items() if v >= threshold)
    band = baseline_band.get("mass_to_light")
    if failed or band is None:
        status = "inconclusive"
    elif band["p84"] < threshold and not above:
        status = "supports_H1"
    elif band["p16"] >= threshold and not below:
        status = "supports_H0"
    else:
        status = "inconclusive"
    return {
        "status": status,
        "threshold": threshold,
        "scenarios_below": below,
        "scenarios_at_or_above": above,
        "scenarios_failed": failed,
    }


def run(
    columns: dict[str, Any],
    *,
    resamples: int | None = None,
    all_scenarios: bool = True,
    choices: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The whole protocol on one Gaia cone: part A, the baseline with its bootstrap, every scenario, the verdict."""
    choices = choices or load_choices()
    stars, removed = select(columns, choices)
    centre = find_centre(stars)
    base = baseline(choices)
    base_fit = fit(stars, centre, base)
    if not base_fit.converged:
        raise ValueError(f"the baseline fit did not converge: {base_fit.message}")
    start = base_fit.theta
    base_physical = physical(stars, base_fit, base, choices)
    n_boot = int(choices["bootstrap"]["resamples"]) if resamples is None else resamples
    seed = int(choices["bootstrap"]["seed"])
    band = percentiles(bootstrap(stars, centre, base, start, n_boot, seed))
    rows: dict[str, dict[str, Any]] = {}
    by_scenario: dict[str, float | None] = {}
    for s in scenarios(choices) if all_scenarios else [base]:
        try:
            f = fit(stars, centre, s, start=start)
            p = physical(stars, f, s, choices) if f.converged and f.cluster_fraction > 0 else None
        except ValueError as error:
            f, p = None, None
            rows[s.label] = {"scenario": asdict(s), "error": str(error)}
        if f is not None:
            rows[s.label] = {
                "scenario": asdict(s),
                "converged": f.converged,
                "stars": f.n_stars,
                "a_deg": f.a_deg,
                "sigma0_masyr": f.sigma0_masyr,
                "parallax_c_mas": f.parallax_c_mas,
                "physical": asdict(p) if p is not None else None,
            }
        by_scenario[s.label] = p.mass_to_light if p is not None else None
    threshold = float(choices["hypothesis_threshold_mass_to_light"])
    return {
        "protocol_id": choices["protocol_id"],
        "protocol_version": choices["protocol_version"],
        "committed_choices_sha256": choices_sha256(),
        "choices_match_committed": choices == load_choices(),
        "arithmetic": arithmetic(),
        "selection": {"stars_in": int(np.asarray(columns["ra"]).size), "removed_by_cut": removed, "kept": len(stars)},
        "centre_offset_deg": centre,
        "baseline": {
            "scenario": asdict(base),
            "fit": {
                "a_deg": base_fit.a_deg,
                "cluster_fraction": base_fit.cluster_fraction,
                "mu_c": base_fit.mu_c,
                "sigma0_masyr": base_fit.sigma0_masyr,
                "parallax_c_mas": base_fit.parallax_c_mas,
                "stars": base_fit.n_stars,
            },
            "physical": asdict(base_physical),
            "bootstrap": {"resamples": n_boot, "seed": seed, "percentiles": band},
        },
        "scenarios": rows,
        "verdict": verdict(band, by_scenario, threshold),
        "einstein_vs_gaia": {
            "v_kms": {"einstein": choices["einstein_1921"]["v_kms"], "gaia": base_physical.v_rms_kms},
            "two_a_ly": {
                "einstein": choices["einstein_1921"]["two_a_with_factor_printed_ly"],
                "gaia": base_physical.two_a_ly,
            },
            "mass_solar": {
                "einstein": float(choices["einstein_1921"]["N"]) * float(choices["einstein_1921"]["m_solar"]),
                "einstein_with_factor": float(choices["einstein_1921"]["N"])
                * float(choices["einstein_1921"]["m_solar"])
                * float(choices["einstein_1921"]["star_factor"]),
                "gaia": base_physical.mass_solar,
            },
        },
    }


# ---------------------------------------------------------------------------------------------
# Synthetic clusters (validation)


def synthetic_cluster(
    *,
    mass: float,
    a_pc: float,
    distance_pc: float,
    n_cluster: int,
    n_field: int,
    mass_to_light: float,
    seed: int,
    cone_deg: float = 0.6,
    pm_error: tuple[float, float] = (0.03, 0.15),
    parallax_error: tuple[float, float] = (0.03, 0.15),
) -> dict[str, Array]:
    """Native gaia_source columns for an isotropic Plummer cluster at the cone's start plus a uniform field.

    Positions follow eq. (3); speeds are drawn from the Plummer distribution function by rejection
    (Aarseth, Henon and Wielen 1974). Every cluster star carries an equal share of the light that gives
    the planted mass-to-light ratio in G.
    """
    rng = np.random.default_rng(seed)
    choices = load_choices()
    ra0, dec0 = float(choices["target"]["start_ra_deg"]), float(choices["target"]["start_dec_deg"])
    g_const = gravitational_constant_pc()
    # positions
    x = rng.uniform(size=n_cluster) ** (-2 / 3)
    r = a_pc / np.sqrt(x - 1)
    direction = rng.normal(size=(n_cluster, 3))
    direction /= np.linalg.norm(direction, axis=1)[:, None]
    pos = r[:, None] * direction
    # speeds
    q = np.empty(n_cluster)
    filled = 0
    while filled < n_cluster:
        trial_q = rng.uniform(size=2 * (n_cluster - filled))
        trial_g = rng.uniform(0, 0.1, size=trial_q.size)
        ok = trial_q[trial_g < trial_q**2 * (1 - trial_q**2) ** 3.5]
        take = min(ok.size, n_cluster - filled)
        q[filled : filled + take] = ok[:take]
        filled += take
    v_escape = np.sqrt(2 * g_const * mass / np.sqrt(r**2 + a_pc**2))
    vdir = rng.normal(size=(n_cluster, 3))
    vdir /= np.linalg.norm(vdir, axis=1)[:, None]
    vel = (q * v_escape)[:, None] * vdir
    d_kpc = distance_pc / 1000
    xi_c = np.degrees(pos[:, 0] / distance_pc)
    eta_c = np.degrees(pos[:, 1] / distance_pc)
    keep = np.hypot(xi_c, eta_c) < cone_deg
    xi_c, eta_c, vel = xi_c[keep], eta_c[keep], vel[keep]
    n_c = int(keep.sum())
    pm_c = vel[:, :2] / (KMS_PER_MASYR_KPC * d_kpc) + np.array([-3.0, -2.5])
    # field, uniform over the cone
    rr = cone_deg * np.sqrt(rng.uniform(size=n_field))
    phi = rng.uniform(0, 2 * np.pi, size=n_field)
    xi_f, eta_f = rr * np.cos(phi), rr * np.sin(phi)
    pm_f = rng.normal([-1.0, -4.0], [4.0, 3.5], size=(n_field, 2))
    plx_f = np.abs(rng.normal(0.5, 0.4, size=n_field))
    xi = np.concatenate([xi_c, xi_f])
    eta = np.concatenate([eta_c, eta_f])
    n = xi.size
    pm_err = rng.uniform(*pm_error, size=(n, 2))
    plx_err = rng.uniform(*parallax_error, size=n)
    pm = np.concatenate([pm_c, pm_f]) + rng.normal(size=(n, 2)) * pm_err
    plx = np.concatenate([np.full(n_c, 1 / d_kpc), plx_f]) + rng.normal(size=n) * plx_err
    # light: an equal share per star of the whole cluster's light, so the visible part follows the counts
    light_each = mass / mass_to_light / n_cluster
    modulus = 5 * math.log10(distance_pc) - 5
    g_c = np.full(n_c, float(choices["light"]["abs_g_sun"]) - 2.5 * math.log10(light_each) + modulus)
    g = np.concatenate([g_c, rng.uniform(14, 18.9, size=n_field)])
    # back to the sky
    xi_r, eta_r = np.radians(xi), np.radians(eta)
    ra0_r, dec0_r = math.radians(ra0), math.radians(dec0)
    rho = np.hypot(xi_r, eta_r)
    c = np.arctan(rho)
    with np.errstate(invalid="ignore", divide="ignore"):
        dec = np.arcsin(np.cos(c) * math.sin(dec0_r) + np.where(rho > 0, eta_r * np.sin(c) * math.cos(dec0_r) / rho, 0))
        ra = ra0_r + np.arctan2(
            xi_r * np.sin(c), rho * math.cos(dec0_r) * np.cos(c) - eta_r * math.sin(dec0_r) * np.sin(c)
        )
    return {
        "ra": np.degrees(ra) % 360,
        "dec": np.degrees(dec),
        "pmra": pm[:, 0],
        "pmdec": pm[:, 1],
        "pmra_error": pm_err[:, 0],
        "pmdec_error": pm_err[:, 1],
        "pmra_pmdec_corr": np.zeros(n),
        "parallax": plx,
        "parallax_error": plx_err,
        "phot_g_mean_mag": g,
        "ruwe": np.full(n, 1.0),
        "astrometric_params_solved": np.full(n, 31.0),
        "duplicated_source": np.zeros(n, dtype=bool),
    }

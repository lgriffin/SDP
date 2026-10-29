"""Fitter: weighted least squares and maximum likelihood for the limb deflection alpha (§9).

Per plate, the measured displacement on each axis is modelled as

    dx = a_x + s x + r y + alpha g_x        dy = a_y + s y - r x + alpha g_y

with plate offsets (a_x, a_y), scale s and rotation r (solved, or fixed from check plates), and
g the geometric response of a 1/b deflection (StarGeometry.response). The ``solved_affine`` plate
model is the 1920 reduction's: six constants per plate,

    dx = a_x + s_xx x + s_xy y + alpha g_x  dy = a_y + s_yx x + s_yy y + alpha g_y

When the scale is fixed, the known linear terms (one check-plate scale, or the four terms of
Plate.check_terms) are subtracted, and so is the rotation when the plate's orientation is known;
otherwise the rotation is solved from the plate. A constant no measurement touches (a plate
measured in one coordinate only) is left out. Errors are Gaussian or
Student-t, independent or correlated within a plate through the check-plate scale uncertainty.
This is the reference implementation; typescript/packages/relativity-fit is the independent one
(REL-007), written from this docstring and the scenario definitions, not from this code.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from scipy.special import gammaln

from sdp_relativity.measurement import to_standard, weight
from sdp_relativity.observations import ObservationSet
from sdp_relativity.reduction import FitScenario

IRLS_TOLERANCE = 1e-13
IRLS_MAX_ITERATIONS = 500


class FitError(ValueError):
    pass


@dataclass(frozen=True)
class System:
    """The linear system for one instrument set under one scenario."""

    design: np.ndarray  # rows x (nuisance..., alpha)
    observed: np.ndarray
    covariance: np.ndarray
    plates: tuple[str, ...]
    nuisance: int

    @property
    def n(self) -> int:
        return int(self.observed.shape[0])


@dataclass(frozen=True)
class FitResult:
    alpha: float
    sigma_alpha: float | None
    loglik: float
    n: int
    k: int
    chi2: float
    iterations: int

    @property
    def aic(self) -> float:
        return 2 * self.k - 2 * self.loglik

    @property
    def bic(self) -> float:
        return self.k * math.log(self.n) - 2 * self.loglik

    def to_document(self) -> dict[str, float | int | None]:
        return {
            "alpha_arcsec": self.alpha,
            "sigma_alpha_arcsec": self.sigma_alpha,
            "loglik": self.loglik,
            "n": self.n,
            "k": self.k,
            "chi2": self.chi2,
            "aic": self.aic,
            "bic": self.bic,
        }


#: Plate constants per plate, by plate model.
PLATE_CONSTANTS = {"solved": 4, "solved_affine": 6, "fixed_from_check_plates": 3}


def build_system(obs: ObservationSet, scenario: FitScenario, sigma_scale: float = 1.0) -> System:
    model = scenario.plate_scale
    solved = model != "fixed_from_check_plates"
    per_plate = PLATE_CONSTANTS[model]
    columns = per_plate * len(obs.plates) + 1
    rows: list[np.ndarray] = []
    observed: list[float] = []
    variances: list[float] = []
    plate_of_row: list[int] = []
    scale_direction: list[float] = []
    for p_index, plate in enumerate(obs.plates):
        base = per_plate * p_index
        for star in plate.stars:
            sigma = to_standard(star.error, scenario.error_convention)
            variance = sigma_scale**2 / weight(sigma)
            gx, gy = star.geometry.response
            x, y = star.geometry.x_arcmin, star.geometry.y_arcmin
            for axis, value in star.axes:
                row = np.zeros(columns)
                if axis == "x":
                    row[base] = 1.0
                    position, cross, response = x, y, gx
                else:
                    row[base + 1] = 1.0
                    position, cross, response = y, -x, gy
                if model == "solved":
                    row[base + 2] = position
                    row[base + 3] = cross
                    target = value
                elif model == "solved_affine":
                    first = base + (2 if axis == "x" else 4)
                    row[first] = x
                    row[first + 1] = y
                    target = value
                else:
                    xx, xy, yx, yy = plate.known_terms
                    target = value - (xx * x + xy * y if axis == "x" else yx * x + yy * y)
                    if plate.check_rotation is None:
                        row[base + 2] = cross
                    else:
                        target -= plate.check_rotation * cross
                row[-1] = response
                rows.append(row)
                observed.append(target)
                variances.append(variance)
                plate_of_row.append(p_index)
                scale_direction.append(position)
    if not rows:
        raise FitError(f"{obs.instrument}: no measured displacements")
    design = np.vstack(rows)
    design = design[:, np.any(design != 0.0, axis=0) | (np.arange(columns) == columns - 1)]
    covariance = np.diag(variances)
    if scenario.error_correlation == "per_plate" and not solved:
        plate_idx = np.asarray(plate_of_row)
        direction = np.asarray(scale_direction)
        for p_index, plate in enumerate(obs.plates):
            mask = plate_idx == p_index
            v = np.where(mask, direction, 0.0)
            covariance += plate.check_scale_sigma**2 * np.outer(v, v)
    if np.linalg.matrix_rank(design) < design.shape[1]:
        raise FitError(f"{obs.instrument}: too few stars to solve the plate constants under {scenario.key}")
    return System(design, np.asarray(observed), covariance, tuple(p.plate_id for p in obs.plates), design.shape[1] - 1)


def _student_t_logpdf(r: np.ndarray, nu: float) -> np.ndarray:
    return np.asarray(
        gammaln((nu + 1) / 2) - gammaln(nu / 2) - 0.5 * math.log(nu * math.pi) - (nu + 1) / 2 * np.log1p(r**2 / nu)
    )


def fit(
    system: System, likelihood: str = "gaussian", *, nu: float = 4.0, fixed_alpha: float | None = None
) -> FitResult:
    """Maximum likelihood for alpha and the plate constants, or for the plate constants alone when
    ``fixed_alpha`` is given (a point hypothesis)."""
    chol = np.linalg.cholesky(system.covariance)
    log_det_half = float(np.sum(np.log(np.diag(chol))))
    a = system.design
    y = system.observed
    if fixed_alpha is not None:
        y = y - fixed_alpha * a[:, -1]
        a = a[:, :-1]
    aw = np.linalg.solve(chol, a)
    yw = np.linalg.solve(chol, y)
    beta = np.linalg.solve(aw.T @ aw, aw.T @ yw)
    iterations = 0
    if likelihood == "gaussian":
        information = aw.T @ aw
    elif likelihood == "student_t":
        for step in range(1, IRLS_MAX_ITERATIONS + 1):
            iterations = step
            r = yw - aw @ beta
            w = (nu + 1) / (nu + r**2)
            information = aw.T @ (w[:, None] * aw)
            new = np.linalg.solve(information, aw.T @ (w * yw))
            converged = np.max(np.abs(new - beta)) <= IRLS_TOLERANCE * max(1.0, float(np.max(np.abs(new))))
            beta = new
            if converged:
                break
        else:
            raise FitError(f"IRLS did not converge in {IRLS_MAX_ITERATIONS} iterations")
        r = yw - aw @ beta
        w = (nu + 1) / (nu + r**2)
        information = aw.T @ (w[:, None] * aw)
    else:
        raise ValueError(f"unknown likelihood {likelihood!r}")
    r = yw - aw @ beta
    chi2 = float(r @ r)
    if likelihood == "gaussian":
        loglik = -0.5 * (chi2 + system.n * math.log(2 * math.pi)) - log_det_half
    else:
        loglik = float(np.sum(_student_t_logpdf(r, nu))) - log_det_half
    if fixed_alpha is None:
        covariance = np.linalg.inv(information)
        alpha, sigma = float(beta[-1]), float(math.sqrt(covariance[-1, -1]))
        k = a.shape[1]
    else:
        alpha, sigma, k = fixed_alpha, None, a.shape[1]
    return FitResult(alpha, sigma, loglik, system.n, k, chi2, iterations)


def fit_set(obs: ObservationSet, scenario: FitScenario, *, nu: float = 4.0, sigma_scale: float = 1.0) -> FitResult:
    return fit(build_system(obs, scenario, sigma_scale), scenario.likelihood, nu=nu)


def bootstrap(
    obs: ObservationSet, scenario: FitScenario, resamples: int, seed: int, *, nu: float = 4.0
) -> dict[str, float | int]:
    """Stars resampled with replacement within each plate, so every plate keeps its constants."""
    rng = np.random.default_rng(seed)
    alphas: list[float] = []
    failed = 0
    keys = [[(p.plate_id, s.star_id) for s in p.stars] for p in obs.plates]
    for _ in range(resamples):
        chosen: list[tuple[str, str]] = []
        for plate_keys in keys:
            picks = rng.integers(0, len(plate_keys), len(plate_keys))
            chosen.extend(plate_keys[i] for i in picks)
        try:
            alphas.append(fit_set(obs.restrict(chosen), scenario, nu=nu).alpha)
        except (FitError, np.linalg.LinAlgError):
            failed += 1
    if not alphas:
        raise FitError(f"{obs.instrument}: every bootstrap resample was degenerate")
    values = np.asarray(alphas)
    q = np.quantile(values, [0.025, 0.16, 0.5, 0.84, 0.975])
    return {
        "resamples": resamples,
        "degenerate": failed,
        "median": float(q[2]),
        "p16": float(q[1]),
        "p84": float(q[3]),
        "p2_5": float(q[0]),
        "p97_5": float(q[4]),
        "std": float(values.std(ddof=1)) if len(values) > 1 else 0.0,
    }


def gaussian_log_evidence(free: FitResult, centre: float, width: float) -> float:
    """log of the integral of L(alpha) x N(alpha | centre, width): exact for a Gaussian profile,
    Laplace's approximation for Student-t."""
    if free.sigma_alpha is None:
        raise FitError("the free-amplitude fit has no alpha uncertainty")
    s2 = free.sigma_alpha**2
    total = s2 + width**2
    return (
        free.loglik
        + 0.5 * math.log(2 * math.pi * s2)
        - 0.5 * math.log(2 * math.pi * total)
        - 0.5 * (free.alpha - centre) ** 2 / total
    )


def posterior_interval(
    free: FitResult, centre: float, width: float, grid: Sequence[float] | np.ndarray
) -> dict[str, float]:
    """Posterior on alpha over the predeclared grid, from the profile and the Gaussian prior."""
    if free.sigma_alpha is None:
        raise FitError("the free-amplitude fit has no alpha uncertainty")
    g = np.asarray(grid)
    log_post = -0.5 * ((g - free.alpha) / free.sigma_alpha) ** 2 - 0.5 * ((g - centre) / width) ** 2
    p = np.exp(log_post - log_post.max())
    cdf = np.cumsum(p)
    cdf /= cdf[-1]
    return {
        "mode": float(g[int(np.argmax(p))]),
        "p2_5": float(np.interp(0.025, cdf, g)),
        "p97_5": float(np.interp(0.975, cdf, g)),
    }

"""UC-03b: exploratory corrections to the UC-03 rerun (docs/protocols/uc03b-einstein-1921-corrections.md).

Written after the first UC-03 run came out inconclusive and before any correction touched data. The fit is
UC-03's own (:func:`sdp_relativity.einstein1921.fit`); what changes is

- C1: the distance is the published one (Baumgardt & Vasiliev 2021, via the Baumgardt database), not 1/parallax;
- C2: the light is the published total V light, scaled to the distance, so faint stars and the core count;
- C3: the core cut, the magnitude limit and the error scale are varied to see whether crowding drives a and sigma_0.

UC-03's verdict is not changed by anything here.
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

from sdp_relativity import einstein1921 as uc03

CHOICES_FILE = "einstein1921_corrections.json"
DISTANCES = ("published_minus_1sigma", "published", "published_plus_1sigma", "alternative")


def choices_text() -> str:
    return resources.files("sdp_relativity").joinpath(CHOICES_FILE).read_text("utf-8")


def load_choices() -> dict[str, Any]:
    document: dict[str, Any] = json.loads(choices_text())
    return document


def choices_sha256() -> str:
    return hashlib.sha256(choices_text().encode("utf-8")).hexdigest()


def distance_pc(name: str, choices: dict[str, Any] | None = None) -> float:
    """The scenario's distance in pc from the published values in the choices file."""
    published = (choices or load_choices())["published"]
    d, err = float(published["distance"]["distance_pc"]), float(published["distance"]["distance_error_pc"])
    values = {
        "published_minus_1sigma": d - err,
        "published": d,
        "published_plus_1sigma": d + err,
        "alternative": float(published["distance_alternative"]["distance_pc"]),
    }
    if name not in values:
        raise ValueError(f"unknown distance {name!r}; expected one of {', '.join(DISTANCES)}")
    return values[name]


def published_light_v(d_pc: float, choices: dict[str, Any] | None = None) -> float:
    """Total V light in L_sun: published mass / published M/L_V, scaled from the database distance to d_pc."""
    light = (choices or load_choices())["published"]["light"]
    at = float(light["mass_solar"]) / float(light["mass_to_light_v"])
    return at * (d_pc / float(light["at_distance_pc"])) ** 2


@dataclass(frozen=True)
class Correction:
    r_in_arcmin: float
    g_max: float
    error_scale: float
    distance: str

    @property
    def label(self) -> str:
        return f"r_in={self.r_in_arcmin}' G<{self.g_max} err={self.error_scale} d={self.distance}"

    def fit_scenario(self, r_max_deg: float) -> uc03.Scenario:
        # the zero-point and A_G of UC-03 play no part once the distance and the light are published
        return uc03.Scenario(self.r_in_arcmin, r_max_deg, self.error_scale, 0.0, 0.0)


def corrections(choices: dict[str, Any] | None = None) -> list[Correction]:
    s = (choices or load_choices())["scenarios"]
    return [
        Correction(float(r), float(g), float(e), str(d))
        for r, g, e, d in itertools.product(s["r_in_arcmin"], s["g_max"], s["error_scale"], s["distance"])
    ]


def baseline(choices: dict[str, Any] | None = None) -> Correction:
    b = (choices or load_choices())["baseline"]
    return Correction(float(b["r_in_arcmin"]), float(b["g_max"]), float(b["error_scale"]), str(b["distance"]))


@dataclass(frozen=True)
class Corrected:
    distance_pc: float
    a_pc: float
    two_a_ly: float
    sigma0_kms: float
    v_rms_kms: float
    mass_solar: float
    light_v_solar: float
    mass_to_light_v: float
    light_g_solar: float
    mass_to_light_g: float


def corrected(
    stars: uc03.Stars, f: uc03.Fit, c: Correction, r_max_deg: float, choices: dict[str, Any] | None = None
) -> Corrected:
    """UC-03's fit at the published distance, set against the published total light."""
    choices = choices or load_choices()
    d = distance_pc(c.distance, choices)
    a_pc = math.radians(f.a_deg) * d
    sigma0 = uc03.KMS_PER_MASYR_KPC * f.sigma0_masyr * d / 1000
    mass = uc03.mass_from_dispersion(sigma0, a_pc)
    light_v = published_light_v(d, choices)
    fraction = uc03.annulus_fraction(c.r_in_arcmin / 60, r_max_deg, f.a_deg)
    modulus = 5 * math.log10(d) - 5
    m_sun = float(uc03.load_choices()["light"]["abs_g_sun"])
    light_g = float(np.sum(f.membership * 10 ** (-0.4 * (stars.g - modulus - m_sun)))) / fraction
    return Corrected(
        distance_pc=d,
        a_pc=a_pc,
        two_a_ly=float((2 * a_pc * u.pc).to(u.lyr).value),
        sigma0_kms=sigma0,
        v_rms_kms=math.sqrt(uc03.mean_square_speed(mass, a_pc)),
        mass_solar=mass,
        light_v_solar=light_v,
        mass_to_light_v=mass / light_v,
        light_g_solar=light_g,
        mass_to_light_g=mass / light_g,
    )


def reading(
    band: dict[str, float] | None,
    by_scenario: dict[str, float | None],
    threshold: float,
    bootstrap_complete: bool,
) -> dict[str, Any]:
    """The exploratory reading of protocol UC-03b. It never replaces UC-03's verdict."""
    failed = sorted(k for k, v in by_scenario.items() if v is None)
    values = {k: v for k, v in by_scenario.items() if v is not None}
    below = sorted(k for k, v in values.items() if v < threshold)
    above = sorted(k for k, v in values.items() if v >= threshold)
    if failed or band is None or not bootstrap_complete:
        status = "mixed"
    elif band["p84"] < threshold and not above:
        status = "consistent_with_H1_after_corrections"
    elif band["p16"] >= threshold and not below:
        status = "consistent_with_H0_after_corrections"
    else:
        status = "mixed"
    return {
        "status": status,
        "exploratory": True,
        "threshold": threshold,
        "scenarios_below": below,
        "scenarios_at_or_above": above,
        "scenarios_failed": failed,
        "bootstrap_complete": bootstrap_complete,
    }


def _band(values: list[Corrected]) -> dict[str, dict[str, float]]:
    if not values:
        return {}
    return {
        k: {
            q: float(np.percentile([getattr(v, k) for v in values], p))
            for q, p in (("p16", 16), ("p50", 50), ("p84", 84))
        }
        for k in asdict(values[0])
    }


def run(
    columns: dict[str, Any],
    *,
    resamples: int | None = None,
    choices: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """UC-03b on one Gaia cone: the corrected baseline with its bootstrap, every correction, the crowding table."""
    choices = choices or load_choices()
    parent = uc03.load_choices()
    widest = max(float(g) for g in choices["scenarios"]["g_max"])
    if widest > float(parent["selection"]["g_max"]):
        raise ValueError(f"G < {widest} is fainter than UC-03's selection limit {parent['selection']['g_max']}")
    stars, removed = uc03.select(columns, parent)
    centre = uc03.find_centre(stars)
    r_max = float(choices["r_max_deg"])
    threshold = float(choices["hypothesis_threshold_mass_to_light"])

    def limited(g_max: float) -> uc03.Stars:
        return stars.take(stars.g < g_max)

    base = baseline(choices)
    base_stars = limited(base.g_max)
    base_fit = uc03.fit(base_stars, centre, base.fit_scenario(r_max))
    if not base_fit.converged:
        raise ValueError(f"the corrected baseline fit did not converge: {base_fit.message}")
    base_value = corrected(base_stars, base_fit, base, r_max, choices)

    declared = int(choices["bootstrap"]["resamples"])
    n_boot = declared if resamples is None else resamples
    seed = int(choices["bootstrap"]["seed"])
    rng = np.random.default_rng(seed)
    boot: list[Corrected] = []
    failures: list[str] = []
    for _ in range(n_boot):
        sample = base_stars.take(rng.integers(0, len(base_stars), len(base_stars)))
        try:
            f = uc03.fit(sample, centre, base.fit_scenario(r_max), start=base_fit.theta)
            if not f.converged:
                failures.append(f"did not converge: {f.message}")
                continue
            boot.append(corrected(sample, f, base, r_max, choices))
        except ValueError as error:
            failures.append(str(error))
    band = _band(boot)
    # a band decides only when it has the declared number of refits, every one of them successful
    complete = n_boot >= declared > 0 and len(boot) == n_boot

    fits: dict[tuple[float, float, float], uc03.Fit | str] = {}
    rows: dict[str, dict[str, Any]] = {}
    by_scenario: dict[str, float | None] = {}
    for c in corrections(choices):
        key = (c.r_in_arcmin, c.g_max, c.error_scale)
        if key not in fits:
            try:
                fits[key] = uc03.fit(limited(c.g_max), centre, c.fit_scenario(r_max), start=base_fit.theta)
            except ValueError as error:
                fits[key] = str(error)
        outcome = fits[key]
        if isinstance(outcome, str):
            rows[c.label] = {"correction": asdict(c), "error": outcome}
            by_scenario[c.label] = None
            continue
        ok = outcome.converged and outcome.cluster_fraction > 0
        value = corrected(limited(c.g_max), outcome, c, r_max, choices) if ok else None
        rows[c.label] = {
            "correction": asdict(c),
            "converged": outcome.converged,
            "stars": outcome.n_stars,
            "a_deg": outcome.a_deg,
            "sigma0_masyr": outcome.sigma0_masyr,
            "parallax_c_mas": outcome.parallax_c_mas,
            "corrected": asdict(value) if value is not None else None,
        }
        by_scenario[c.label] = value.mass_to_light_v if value is not None else None

    crowding = [
        {
            "r_in_arcmin": c.r_in_arcmin,
            "g_max": c.g_max,
            "stars": rows[c.label].get("stars"),
            "a_pc": (rows[c.label].get("corrected") or {}).get("a_pc"),
            "sigma0_kms": (rows[c.label].get("corrected") or {}).get("sigma0_kms"),
            "mass_solar": (rows[c.label].get("corrected") or {}).get("mass_solar"),
            "mass_to_light_v": (rows[c.label].get("corrected") or {}).get("mass_to_light_v"),
        }
        for c in corrections(choices)
        if c.error_scale == base.error_scale and c.distance == base.distance
    ]
    return {
        "protocol_id": choices["protocol_id"],
        "protocol_version": choices["protocol_version"],
        "parent_protocol": choices["parent_protocol"],
        "status_note": choices["status"],
        "committed_choices_sha256": choices_sha256(),
        "choices_match_committed": choices == load_choices(),
        "selection": {"stars_in": int(np.asarray(columns["ra"]).size), "removed_by_cut": removed, "kept": len(stars)},
        "centre_offset_deg": centre,
        "published": choices["published"],
        "baseline": {
            "correction": asdict(base),
            "fit": {
                "a_deg": base_fit.a_deg,
                "cluster_fraction": base_fit.cluster_fraction,
                "sigma0_masyr": base_fit.sigma0_masyr,
                "parallax_c_mas": base_fit.parallax_c_mas,
                "parallax_distance_pc": 1000 / base_fit.parallax_c_mas if base_fit.parallax_c_mas > 0 else None,
                "stars": base_fit.n_stars,
            },
            "corrected": asdict(base_value),
            "bootstrap": {
                "resamples": n_boot,
                "succeeded": len(boot),
                "failures": failures,
                "seed": seed,
                "percentiles": band,
            },
        },
        "corrections": rows,
        "crowding": crowding,
        "reading": reading(band.get("mass_to_light_v"), by_scenario, threshold, complete),
        "comparison": {
            "mass_solar": {"gaia_corrected": base_value.mass_solar, **_comparison(choices, "baumgardt_mass_solar")},
            "sigma0_kms": {
                "gaia_corrected": base_value.sigma0_kms,
                **_comparison(choices, "baumgardt_sigma0_kms"),
                **_comparison(choices, "harris_sigma_v_kms"),
            },
        },
    }


def _comparison(choices: dict[str, Any], key: str) -> dict[str, float]:
    return {key: float(choices["published"]["comparison_only"][key])}

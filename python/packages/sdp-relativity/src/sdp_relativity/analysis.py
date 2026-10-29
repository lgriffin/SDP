"""The TaskKinds of the UC-02 plan. Each is a pure function of its params, upstream values and the
resolved inputs; together they produce every number the gates evaluate."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

import astropy.units as u
import numpy as np

from sdp.kernel.science.plan import TaskContext
from sdp_relativity.comparison import Comparison, compare, pooled_system
from sdp_relativity.constants import recorded_constants
from sdp_relativity.dossier import claim_violations, limitations
from sdp_relativity.fitter import FitError, bootstrap, build_system, fit, posterior_interval
from sdp_relativity.models import GRDeflection, HalfDeflection, hypotheses
from sdp_relativity.observations import ObservationBundle, PoolingJustification, parse_bundle
from sdp_relativity.oracles import load_registry
from sdp_relativity.reduction import BASELINE, FitScenario, fit_scenarios, historical

ASTROGRAPHIC = "sobral-astro"
HYPOTHESES = ("UC02-H0", "UC02-H1", "UC02-H2", "UC02-H3")


def _bundle(ctx: TaskContext) -> ObservationBundle:
    return parse_bundle(ctx.service("inputs")["observations"])


def _prior_centre() -> float:
    """Midway between the half and GR limb values: neutral between H1 and H2."""
    return float((HalfDeflection().limb() + GRDeflection().limb()).to_value(u.arcsec) / 2)


def limb_models(params: Mapping[str, Any], upstream: Mapping[str, Any], ctx: TaskContext) -> dict[str, Any]:
    """REL-G-03: the model classes against a direct evaluation of 4GM/(c^2 R) and 2GM/(c^2 R)."""
    k = recorded_constants()
    rad_to_arcsec = 180.0 / math.pi * 3600.0
    direct = {
        "gr": 4 * k.G.si.value * k.M_sun.si.value / (k.c.si.value**2 * k.R_sun.si.value) * rad_to_arcsec,
        "half": 2 * k.G.si.value * k.M_sun.si.value / (k.c.si.value**2 * k.R_sun.si.value) * rad_to_arcsec,
    }
    modelled = {
        "gr": GRDeflection(constants=k).limb().to_value(u.arcsec),
        "half": HalfDeflection(constants=k).limb().to_value(u.arcsec),
    }
    errors = {name: abs(modelled[name] - direct[name]) / direct[name] for name in direct}
    return {
        "gr_limb_arcsec": modelled["gr"],
        "half_limb_arcsec": modelled["half"],
        "max_rel_error": max(errors.values()),
        "constants_version": k.version,
    }


def transcription_audit(params: Mapping[str, Any], upstream: Mapping[str, Any], ctx: TaskContext) -> dict[str, Any]:
    """REL-G-01 and REL-G-02: every star row links to a located record with a matching second pass."""
    bundle = _bundle(ctx)
    # A record's latest pass decides, so a correction after a matching pass leaves the row unverified
    # until it is re-checked. Passes sort by time; the sort is stable, so untimed passes keep their order.
    latest = {v.record_id: v.matches for v in sorted(bundle.verifications, key=lambda v: v.verified_at)}
    verified = {record_id for record_id, matches in latest.items() if matches}
    unlinked: list[str] = []
    unverified: list[str] = []
    for obs in bundle.sets:
        for star in obs.stars:
            label = f"{obs.instrument}/{star.star_id}"
            if not star.locator.strip() or not star.record_id or not bundle.records.get(star.record_id, "").strip():
                unlinked.append(label)
            if star.record_id not in verified:
                unverified.append(label)
    expected = list(params.get("instruments", []))
    return {
        "stars": sum(len(o.stars) for o in bundle.sets),
        "unlinked": unlinked,
        "unverified": unverified,
        "missing_instruments": [i for i in expected if i not in bundle.instruments],
        "provenance": dict(bundle.provenance),
    }


def _comparison(system: Any, scenario: FitScenario, params: Mapping[str, Any]) -> Comparison:
    return compare(
        system,
        scenario.likelihood,
        hypotheses(),
        [float(w) for w in params["prior_widths"]],
        _prior_centre(),
        nu=float(params.get("nu", 4)),
    )


def instrument_sweep(params: Mapping[str, Any], upstream: Mapping[str, Any], ctx: TaskContext) -> dict[str, Any]:
    """REL-004, REL-005: all four hypotheses on one instrument, for every fit scenario."""
    bundle = _bundle(ctx)
    instrument = str(params["instrument"])
    out: dict[str, Any] = {"instrument": instrument, "scenarios": {}, "failures": {}}
    if instrument not in bundle.instruments:
        # Reported, not raised: the missing hypotheses fail REL-G-05 and the verdict says why.
        out["failures"]["*"] = f"the bundle has no observation set for {instrument}"
        return out
    obs = bundle.instrument(instrument)
    grid = np.arange(
        params["grid"]["start"], params["grid"]["stop"] + params["grid"]["step"] / 2, params["grid"]["step"]
    )
    for scenario in fit_scenarios(params["axes"]):
        try:
            comparison = _comparison(build_system(obs, scenario), scenario, params)
        except (FitError, np.linalg.LinAlgError) as exc:
            out["failures"][scenario.key] = str(exc)
            continue
        posterior = {
            str(w): posterior_interval(comparison.free, _prior_centre(), float(w), grid) for w in params["prior_widths"]
        }
        out["scenarios"][scenario.key] = {
            "scenario": scenario.to_document(),
            **comparison.to_document(),
            "posterior": posterior,
            "dof": comparison.free.n - comparison.free.k,
        }
    resamples = int(params.get("bootstrap_resamples", 0))
    if resamples:
        out["bootstrap"] = bootstrap(obs, BASELINE, resamples, ctx.seed, nu=float(params.get("nu", 4)))
    return out


def pooled(params: Mapping[str, Any], upstream: Mapping[str, Any], ctx: TaskContext) -> dict[str, Any]:
    """Pooled fits, only under a PoolingJustification, for each astrographic treatment (REL-004)."""
    bundle = _bundle(ctx)
    out: dict[str, Any] = {"justification": None, "scenarios": {}, "failures": {}}
    if len(bundle.sets) < 2:
        return out
    justification = PoolingJustification(
        bundle.instruments,
        "Predeclared sensitivity axis: does the conclusion depend on how the astrographic set is treated when "
        "the instruments are combined?",
        (
            "one limb deflection alpha is common to all instruments",
            "plate constants are independent between instruments",
            "uncertainties are comparable across instruments after the scenario's convention",
        ),
    )
    out["justification"] = {
        "instruments": list(justification.instruments),
        "reason": justification.reason,
        "assumptions": list(justification.assumptions),
    }
    for scenario in fit_scenarios(params["axes"]):
        for treatment in params["axes"]["astrographic"]:
            key = f"{scenario.key}|astrographic={treatment}"
            try:
                systems = []
                for obs in bundle.sets:
                    if obs.instrument == ASTROGRAPHIC and treatment == "excluded":
                        continue
                    scale = 1.0
                    if obs.instrument == ASTROGRAPHIC and treatment == "downweighted":
                        own = fit(build_system(obs, scenario), scenario.likelihood, nu=float(params.get("nu", 4)))
                        dof = own.n - own.k
                        scale = max(1.0, math.sqrt(own.chi2 / dof)) if dof > 0 else 1.0
                    systems.append(build_system(obs, scenario, sigma_scale=scale))
                if len(systems) < 2:
                    out["failures"][key] = "fewer than two instruments left to pool"
                    continue
                comparison = _comparison(pooled_system(systems), scenario, params)
            except (FitError, np.linalg.LinAlgError) as exc:
                out["failures"][key] = str(exc)
                continue
            out["scenarios"][key] = {
                "scenario": {**scenario.to_document(), "astrographic": treatment},
                **comparison.to_document(),
            }
    return out


def independent_agreement(params: Mapping[str, Any], upstream: Mapping[str, Any], ctx: TaskContext) -> dict[str, Any]:
    """REL-007, REL-G-08: the independent fitter's alpha against ours, scenario by scenario."""
    theirs: dict[tuple[str, str], float] = {}
    for row in ctx.service("inputs")["independent_fit"].get("fits", []):
        key = (str(row["instrument"]), FitScenario(**row["scenario"]).key)
        theirs[key] = float(row["alpha_arcsec"])
    diffs: list[float] = []
    missing: list[str] = []
    for name, value in upstream.items():
        if not name.startswith("fit:"):
            continue
        for key, result in value["scenarios"].items():
            ours = next(h["alpha_arcsec"] for h in result["hypotheses"] if h["hypothesis_id"] == "UC02-H3")
            other = theirs.get((value["instrument"], key))
            if other is None:
                missing.append(f"{value['instrument']} {key}")
            else:
                diffs.append(abs(ours - other))
    return {
        "compared": len(diffs),
        "missing": missing,
        "max_abs_diff_arcsec": max(diffs) if diffs else None,
        "implementation": ctx.service("inputs")["independent_fit"].get("implementation", {}),
    }


def _surviving_conclusions(fits: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    conclusions = []
    for value in fits:
        preferred = {s["preferred"] for s in value["scenarios"].values()}
        disfavoured_everywhere = set(HYPOTHESES)
        for s in value["scenarios"].values():
            disfavoured_everywhere &= {h for h, d in s["delta_bic"].items() if d > 10}
        alphas = [
            next(h["alpha_arcsec"] for h in s["hypotheses"] if h["hypothesis_id"] == "UC02-H3")
            for s in value["scenarios"].values()
        ]
        conclusions.append(
            {
                "instrument": value["instrument"],
                "preferred_in_every_scenario": next(iter(preferred)) if len(preferred) == 1 else None,
                "preferred_somewhere": sorted(preferred),
                "strongly_disfavoured_in_every_scenario": sorted(disfavoured_everywhere),
                "alpha_range_arcsec": [min(alphas), max(alphas)] if alphas else None,
            }
        )
    return conclusions


def synthesis(params: Mapping[str, Any], upstream: Mapping[str, Any], ctx: TaskContext) -> dict[str, Any]:
    """Historical reproduction, the 2020 comparison, sweep completeness and the dossier lint."""
    fits = [v for k, v in sorted(upstream.items()) if k.startswith("fit:")]
    registry = load_registry()
    tolerances = {t["oracle_id"]: t for t in params["oracle_tolerances"]}
    expected_instruments = list(params["instruments"])
    n_fit = len(fit_scenarios(params["axes"]))

    # REL-G-04: our fit under the 1920 reduction against each published estimate.
    reproduction: list[dict[str, Any]] = []
    for oracle in registry.values():
        if oracle.kind != "published_estimate" or oracle.oracle_id not in tolerances or oracle.value_arcsec is None:
            continue
        mine = next((v for v in fits if v["instrument"] == oracle.instrument), None)
        result = mine["scenarios"].get(historical(str(oracle.instrument)).key) if mine else None
        if result is None:
            reproduction.append({"oracle_id": oracle.oracle_id, "instrument": oracle.instrument, "z": None})
            continue
        alpha = next(h["alpha_arcsec"] for h in result["hypotheses"] if h["hypothesis_id"] == "UC02-H3")
        tol = tolerances[oracle.oracle_id]
        allowed = float(tol["atol"]) + float(tol["rtol"]) * abs(oracle.value_arcsec)
        reproduction.append(
            {
                "oracle_id": oracle.oracle_id,
                "instrument": oracle.instrument,
                "published_arcsec": oracle.value_arcsec,
                "reproduced_arcsec": alpha,
                "z": abs(alpha - oracle.value_arcsec) / allowed,
                "oracle_verified": oracle.verified,
            }
        )
    zs = [r["z"] for r in reproduction if r["z"] is not None]

    # REL-G-07: agreements and disagreements with the 2020 reanalysis, per instrument.
    gtp = registry.get("gtp-2020")
    comparison_rows = []
    if gtp is not None:
        for value in fits:
            ours = value["scenarios"].get(BASELINE.key, {}).get("preferred")
            theirs = (gtp.raw.get("preferred") or {}).get(value["instrument"])
            comparison_rows.append(
                {
                    "instrument": value["instrument"],
                    "ours_baseline": ours,
                    "reanalysis_2020": theirs,
                    "reanalysis_conclusion": (gtp.raw.get("conclusions") or {}).get(value["instrument"]),
                    "agrees": None if theirs is None else ours == theirs,
                }
            )

    evaluated = sum(
        sum(1 for h in s["hypotheses"] if h["hypothesis_id"] in HYPOTHESES)
        for v in fits
        for s in v["scenarios"].values()
    )
    pooled_value = upstream.get("pooled", {"scenarios": {}})
    widths = len(params["axes"]["prior_width_arcsec"])
    astro = len(params["axes"]["astrographic"])
    expected_scenarios = n_fit * astro * widths
    complete = 0
    for scenario in fit_scenarios(params["axes"]):
        per_instrument = all(scenario.key in v["scenarios"] for v in fits) and len(fits) == len(expected_instruments)
        for treatment in params["axes"]["astrographic"]:
            if per_instrument and f"{scenario.key}|astrographic={treatment}" in pooled_value["scenarios"]:
                complete += widths
    texts = [*limitations(), *params.get("dossier_text", [])]
    return {
        "historical_reproduction": reproduction,
        "historical_reproduction_max_z": max(zs) if zs else None,
        "reanalysis_comparison": comparison_rows,
        "hypotheses_expected": len(expected_instruments) * n_fit * len(HYPOTHESES),
        "hypotheses_evaluated": evaluated,
        "scenarios_expected": expected_scenarios,
        "scenarios_complete": complete,
        "surviving_conclusions": _surviving_conclusions(fits),
        "limitations": list(limitations()),
        "claim_violations": claim_violations(texts),
    }

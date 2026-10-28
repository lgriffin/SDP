"""ModelComparison: the four hypotheses on equal footing for one fit (REL-005).

Point hypotheses H0, H1 and H2 fix alpha at 0, the half value and the general-relativistic value;
H3 frees it. Each gets a maximised likelihood, AIC and BIC; Bayes factors against H3 use a Gaussian
prior on alpha centred midway between the half and GR values, at each predeclared width.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from sdp_relativity.fitter import FitResult, System, fit, gaussian_log_evidence
from sdp_relativity.models import DeflectionModel

#: Hypotheses whose BIC is within this of the best are not distinguished from it (Kass and Raftery).
BIC_INDISTINCT = 2.0
BIC_STRONG = 10.0


@dataclass(frozen=True)
class HypothesisResult:
    hypothesis_id: str
    model_id: str
    fit: FitResult

    def to_document(self) -> dict[str, Any]:
        return {"hypothesis_id": self.hypothesis_id, "model_id": self.model_id, **self.fit.to_document()}


@dataclass(frozen=True)
class Comparison:
    results: tuple[HypothesisResult, ...]
    log_bayes_vs_free: Mapping[float, Mapping[str, float]] = field(default_factory=dict)

    def result(self, hypothesis_id: str) -> HypothesisResult:
        for r in self.results:
            if r.hypothesis_id == hypothesis_id:
                return r
        raise KeyError(hypothesis_id)

    @property
    def free(self) -> FitResult:
        return self.result("UC02-H3").fit

    @property
    def preferred(self) -> str:
        """Lowest BIC; ties go to the simpler (point) hypothesis."""
        return min(self.results, key=lambda r: (round(r.fit.bic, 9), r.fit.k)).hypothesis_id

    def delta_bic(self) -> dict[str, float]:
        best = min(r.fit.bic for r in self.results)
        return {r.hypothesis_id: r.fit.bic - best for r in self.results}

    def strongly_disfavoured(self) -> list[str]:
        return sorted(h for h, d in self.delta_bic().items() if d > BIC_STRONG)

    def to_document(self) -> dict[str, Any]:
        return {
            "hypotheses": [r.to_document() for r in self.results],
            "preferred": self.preferred,
            "delta_bic": self.delta_bic(),
            "log_bayes_vs_free": {str(w): dict(v) for w, v in self.log_bayes_vs_free.items()},
        }


def compare(
    system: System,
    likelihood: str,
    models: Sequence[DeflectionModel],
    prior_widths: Sequence[float],
    prior_centre: float,
    *,
    nu: float = 4.0,
) -> Comparison:
    results: list[HypothesisResult] = []
    for model in models:
        if model.free:
            result = fit(system, likelihood, nu=nu)
        else:
            result = fit(system, likelihood, nu=nu, fixed_alpha=float(model.limb().value))
        results.append(HypothesisResult(model.hypothesis_id, model.model_id, result))
    free = next(r.fit for r in results if r.hypothesis_id == "UC02-H3")
    bayes: dict[float, dict[str, float]] = {}
    for width in prior_widths:
        evidence = gaussian_log_evidence(free, prior_centre, width)
        bayes[float(width)] = {
            r.hypothesis_id: r.fit.loglik - evidence for r in results if r.hypothesis_id != "UC02-H3"
        }
    return Comparison(tuple(results), bayes)


def pooled_system(systems: Sequence[System]) -> System:
    """Block-diagonal nuisance parameters per instrument, one shared alpha column."""
    nuisance = sum(s.nuisance for s in systems)
    rows = sum(s.n for s in systems)
    design = np.zeros((rows, nuisance + 1))
    covariance = np.zeros((rows, rows))
    observed = np.concatenate([s.observed for s in systems])
    r0 = c0 = 0
    for s in systems:
        design[r0 : r0 + s.n, c0 : c0 + s.nuisance] = s.design[:, :-1]
        design[r0 : r0 + s.n, -1] = s.design[:, -1]
        covariance[r0 : r0 + s.n, r0 : r0 + s.n] = s.covariance
        r0 += s.n
        c0 += s.nuisance
    plates = tuple(p for s in systems for p in s.plates)
    return System(design, observed, covariance, plates, nuisance)

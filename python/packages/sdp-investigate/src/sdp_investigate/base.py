"""The shape every field test shares: extract features from bundles, evaluate them, and evaluate
again on null versions of the same field to get an empirical false-positive rate."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from sdp.kernel.domain.investigation import EvidenceBundle, NullFieldResult, Subject, TestResult, significant
from sdp.kernel.domain.policy import NumericalPolicy
from sdp.kernel.science.plan import TaskContext

DEFAULT_POLICY = NumericalPolicy(rtol=1e-9)


@dataclass(frozen=True)
class Scored:
    """One subject's outcome before it becomes a TestResult."""

    subject: Subject
    statistic: float
    p_value: float | None
    effect_size: float | None = None
    uncertainty: float | None = None
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Features:
    """What a test extracted from the bundles: one row per object it can judge."""

    keys: tuple[str, ...]
    arrays: Mapping[str, Any]

    def __len__(self) -> int:
        return len(self.keys)


def catalogue_row(bundle: EvidenceBundle, source_id: str | None, column: str) -> Mapping[str, Any] | None:
    """The first row carrying ``column`` from ``source_id``, or from any catalogue when unset."""
    sources = [source_id] if source_id else sorted(bundle.catalogue_rows)
    for s in sources:
        for row in bundle.rows(s):
            if row.get(column) is not None:
                return row
    return None


def finite(*values: Any) -> bool:
    return all(v is not None and np.isfinite(float(v)) for v in values)


def robust_sigma(x: np.ndarray) -> float:
    """1.4826 × the median absolute deviation, floored so a degenerate field cannot divide by zero."""
    if len(x) == 0:
        return 1.0
    return max(float(np.median(np.abs(x - np.median(x))) * 1.4826), 1e-9)


class FieldTest(ABC):
    """Subclasses set the class attributes and implement features, evaluate and null_features."""

    name: str
    version: str = "1.0.0"
    question: str
    null_model: str
    inputs: tuple[str, ...] = ()
    needs_products: tuple[str, ...] = ()
    policy: NumericalPolicy = DEFAULT_POLICY

    @abstractmethod
    def features(
        self, bundles: Sequence[EvidenceBundle], thresholds: Mapping[str, Any], context: TaskContext
    ) -> Features:
        """Pull this test's inputs out of the bundles; objects without them are left out."""

    @abstractmethod
    def evaluate(self, features: Features, thresholds: Mapping[str, Any], rng: np.random.Generator) -> list[Scored]:
        """Score each subject. Pure apart from ``rng``."""

    @abstractmethod
    def null_features(self, features: Features, thresholds: Mapping[str, Any], rng: np.random.Generator) -> Features:
        """One null version of the field under this test's null model."""

    def run(
        self, bundles: Sequence[EvidenceBundle], thresholds: Mapping[str, Any], context: TaskContext
    ) -> Sequence[TestResult]:
        rng = np.random.default_rng(context.seed)
        scored = self.evaluate(self.features(bundles, thresholds, context), thresholds, rng)
        return [
            TestResult(
                test_name=self.name,
                test_version=self.version,
                subject=s.subject,
                statistic=float(s.statistic),
                seed=context.seed,
                uncertainty=s.uncertainty,
                p_value=None if s.p_value is None else float(min(1.0, max(0.0, s.p_value))),
                effect_size=s.effect_size,
                details=dict(s.details),
            )
            for s in scored
        ]

    def null(
        self,
        bundles: Sequence[EvidenceBundle],
        thresholds: Mapping[str, Any],
        context: TaskContext,
        trials: int,
        alpha: float,
    ) -> NullFieldResult:
        rng = np.random.default_rng(context.seed)
        features = self.features(bundles, thresholds, context)
        examined = positives = 0
        for _ in range(trials):
            scored = self.evaluate(self.null_features(features, thresholds, rng), thresholds, rng)
            examined += len(scored)
            positives += sum(1 for s in scored if significant(s.p_value, alpha))
        return NullFieldResult(self.name, self.version, trials, examined, positives, alpha, self.null_model)

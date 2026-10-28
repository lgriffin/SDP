"""ClusterSearchAlgorithm adapters (§10): scikit-learn HDBSCAN and DBSCAN as the CPU references, cuML
HDBSCAN on the DGX Spark.

Labels are renumbered in order of each cluster's first row, so equal partitions give equal arrays.
A GPU adapter's results reach a gate only after ``agreement`` with the CPU reference on the fixed
fixture passes in the same release (STL-004, STL-G-08); the check lives here so both sides of it
run the same code.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np

from sdp.kernel.ports.compute import ClusterLabels, ClusterSearchAlgorithm
from sdp.kernel.science.plan import Backend


class ClusteringUnavailable(RuntimeError):
    pass


def canonical_labels(labels: np.ndarray) -> np.ndarray:
    out = np.full(labels.shape, -1, dtype=np.int64)
    seen: dict[int, int] = {}
    for i, label in enumerate(labels.tolist()):
        if label < 0:
            continue
        if label not in seen:
            seen[label] = len(seen)
        out[i] = seen[label]
    return out


def _check(features: np.ndarray) -> np.ndarray:
    x = np.asarray(features, dtype=np.float64)
    if x.ndim != 2 or not np.all(np.isfinite(x)):
        raise ValueError("features must be a finite 2-D matrix")
    return x


@dataclass
class SklearnHdbscan:
    algorithm_id: str = "sklearn-hdbscan"
    backend: Backend = Backend.CPU

    def search(self, features: np.ndarray, params: Mapping[str, Any], seed: int) -> ClusterLabels:
        from sklearn.cluster import HDBSCAN

        x = _check(features)
        model = HDBSCAN(
            min_cluster_size=int(params.get("min_cluster_size", 10)),
            min_samples=None if params.get("min_samples") is None else int(params["min_samples"]),
            cluster_selection_method=str(params.get("cluster_selection_method", "eom")),
            cluster_selection_epsilon=float(params.get("cluster_selection_epsilon", 0.0)),
            copy=True,
        ).fit(x)
        labels = canonical_labels(model.labels_)
        probabilities = np.where(labels >= 0, np.asarray(model.probabilities_, dtype=np.float64), 0.0)
        return ClusterLabels(labels, probabilities, self.algorithm_id, dict(params))


@dataclass
class SklearnDbscan:
    """DBSCAN has no soft membership: core and border members get 1, noise 0."""

    algorithm_id: str = "sklearn-dbscan"
    backend: Backend = Backend.CPU

    def search(self, features: np.ndarray, params: Mapping[str, Any], seed: int) -> ClusterLabels:
        from sklearn.cluster import DBSCAN

        x = _check(features)
        model = DBSCAN(eps=float(params.get("eps", 0.5)), min_samples=int(params.get("min_samples", 10))).fit(x)
        labels = canonical_labels(model.labels_)
        return ClusterLabels(labels, np.where(labels >= 0, 1.0, 0.0), self.algorithm_id, dict(params))


@dataclass
class CumlHdbscan:
    """cuML HDBSCAN on CUDA. Import fails cleanly where RAPIDS is not installed (CI, laptops)."""

    algorithm_id: str = "cuml-hdbscan"
    backend: Backend = Backend.CUDA

    def search(self, features: np.ndarray, params: Mapping[str, Any], seed: int) -> ClusterLabels:
        try:
            from cuml.cluster import HDBSCAN
        except ImportError as exc:
            raise ClusteringUnavailable("cuML is not installed; GPU clustering runs only on the DGX Spark") from exc
        x = _check(features).astype(np.float32)
        model = HDBSCAN(
            min_cluster_size=int(params.get("min_cluster_size", 10)),
            min_samples=None if params.get("min_samples") is None else int(params["min_samples"]),
            cluster_selection_method=str(params.get("cluster_selection_method", "eom")),
        ).fit(x)
        labels = canonical_labels(np.asarray(model.labels_))
        probabilities = np.where(labels >= 0, np.asarray(model.probabilities_, dtype=np.float64), 0.0)
        return ClusterLabels(labels, probabilities, self.algorithm_id, dict(params))


def rand_index(a: np.ndarray, b: np.ndarray) -> float:
    same_a = a[:, None] == a[None, :]
    same_b = b[:, None] == b[None, :]
    return float(np.mean(same_a == same_b))


@dataclass(frozen=True)
class Agreement:
    algorithm_id: str
    reference_id: str
    release: str
    rand_index: float
    max_probability_diff: float
    passed: bool

    def to_document(self) -> dict[str, Any]:
        return {
            "algorithm_id": self.algorithm_id,
            "reference_id": self.reference_id,
            "release": self.release,
            "rand_index": self.rand_index,
            "max_probability_diff": self.max_probability_diff,
            "passed": self.passed,
        }


def agreement(
    candidate: ClusterSearchAlgorithm,
    reference: ClusterSearchAlgorithm,
    features: np.ndarray,
    params: Mapping[str, Any],
    *,
    release: str,
    min_rand_index: float = 0.999,
    max_probability_diff: float = 0.05,
    seed: int = 0,
) -> Agreement:
    """Labels (up to relabelling) and membership probabilities of ``candidate`` against ``reference``,
    recorded for ``release`` so the record cannot admit the GPU adapter in any other release."""
    a = candidate.search(features, params, seed)
    b = reference.search(features, params, seed)
    ri = rand_index(a.labels, b.labels)
    both = (a.labels >= 0) & (b.labels >= 0)
    diff = float(np.max(np.abs(a.probabilities[both] - b.probabilities[both]))) if both.any() else 0.0
    passed = ri >= min_rand_index and diff <= max_probability_diff
    return Agreement(candidate.algorithm_id, reference.algorithm_id, release, ri, diff, passed)

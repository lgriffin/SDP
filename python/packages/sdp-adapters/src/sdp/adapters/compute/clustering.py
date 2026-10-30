"""ClusterSearchAlgorithm adapters (§10): scikit-learn HDBSCAN and DBSCAN as the CPU references, cuML
HDBSCAN on the DGX Spark, and fast_hdbscan's Boruvka HDBSCAN for the whole-sky sweep.

Labels are renumbered in order of each cluster's first row, so equal partitions give equal arrays.
A GPU adapter's results reach a gate only after ``agreement`` with the CPU reference on the fixed
fixture passes in the same release (STL-004, STL-G-08); the check lives here so both sides of it
run the same code.
"""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
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


#: fast_hdbscan condenses its tree recursively, which overflows Windows' 1 MB main-thread stack from
#: about 600,000 points; Python caps a thread's stack just under 256 MB on Windows
_STACK_BYTES = 200 * 1024 * 1024


def _with_big_stack[T](fn: Callable[[], T]) -> T:
    out: list[T] = []
    errors: list[BaseException] = []

    def target() -> None:
        try:
            out.append(fn())
        except BaseException as exc:  # re-raised in the caller's thread
            errors.append(exc)

    previous = threading.stack_size(_STACK_BYTES)
    try:
        worker = threading.Thread(target=target, name="fast-hdbscan")
        worker.start()
    finally:
        threading.stack_size(previous)
    worker.join()
    if errors:
        raise errors[0]
    return out[0]


@dataclass
class FastHdbscan:
    """HDBSCAN on a Boruvka minimum spanning tree (fast_hdbscan): roughly n log n in a few dimensions,
    where scikit-learn's Prim tree grows about as n squared (#133). The tree depends only on
    ``min_samples``, so the last one is kept and a grid over ``min_cluster_size`` condenses it again
    rather than rebuilding it. ``reproducible`` makes the tree independent of thread scheduling.

    ``min_samples`` means what it means to scikit-learn (the point itself counts), so the same grid
    gives the same labels as ``SklearnHdbscan``: fast_hdbscan does not count the point, so it is asked
    for one fewer."""

    algorithm_id: str = "fast-hdbscan"
    backend: Backend = Backend.CPU
    #: numba threads per search; sweep workers are processes already, so one each
    threads: int = 1
    _tree: tuple[tuple[str, int], Any] | None = field(default=None, init=False, repr=False, compare=False)

    def search(self, features: np.ndarray, params: Mapping[str, Any], seed: int) -> ClusterLabels:
        x = _check(features)
        min_cluster_size = int(params.get("min_cluster_size", 10))
        min_samples = min_cluster_size if params.get("min_samples") is None else int(params["min_samples"])
        if min_samples < 2:
            raise ValueError("fast-hdbscan needs min_samples of at least 2 (the point itself and one neighbour)")
        key = (hashlib.blake2b(x.tobytes() + repr(x.shape).encode(), digest_size=16).hexdigest(), min_samples)
        labels, strengths = _with_big_stack(lambda: self._labels(x, key, min_cluster_size, params))
        labels = canonical_labels(np.asarray(labels))
        probabilities = np.where(labels >= 0, np.asarray(strengths, dtype=np.float64), 0.0)
        return ClusterLabels(labels, probabilities, self.algorithm_id, dict(params))

    def _labels(
        self, x: np.ndarray, key: tuple[str, int], min_cluster_size: int, params: Mapping[str, Any]
    ) -> tuple[np.ndarray, np.ndarray]:
        import numba
        from fast_hdbscan.hdbscan import clusters_from_spanning_tree, compute_minimum_spanning_tree

        numba.set_num_threads(max(1, min(self.threads, int(getattr(numba.config, "NUMBA_NUM_THREADS", 1)))))
        if self._tree is None or self._tree[0] != key:
            tree, _, _ = compute_minimum_spanning_tree(
                np.ascontiguousarray(x), min_samples=key[1] - 1, reproducible=True
            )
            self._tree = (key, tree)
        labels, strengths, *_ = clusters_from_spanning_tree(
            self._tree[1],
            min_cluster_size=min_cluster_size,
            cluster_selection_method=str(params.get("cluster_selection_method", "eom")),
            cluster_selection_epsilon=float(params.get("cluster_selection_epsilon", 0.0)),
        )
        return labels, strengths


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

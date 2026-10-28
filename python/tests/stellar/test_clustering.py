"""T3: the scikit-learn clustering adapters against the ClusterSearch contract, and the GPU agreement check."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from sdp.adapters.compute.clustering import (
    ClusteringUnavailable,
    CumlHdbscan,
    SklearnDbscan,
    SklearnHdbscan,
    agreement,
    canonical_labels,
)
from sdp.kernel.ports.compute import ClusterLabels
from sdp.kernel.science.plan import Backend
from sdp.testing.contracts.compute import ClusterSearchContract, blobs

pytestmark = pytest.mark.tier("T3")


class TestSklearnHdbscan(ClusterSearchContract):
    @pytest.fixture
    def clusterer(self) -> SklearnHdbscan:
        return SklearnHdbscan()

    @pytest.fixture
    def cluster_params(self) -> dict[str, Any]:
        return {"min_cluster_size": 10, "min_samples": 5}


class TestSklearnDbscan(ClusterSearchContract):
    @pytest.fixture
    def clusterer(self) -> SklearnDbscan:
        return SklearnDbscan()

    @pytest.fixture
    def cluster_params(self) -> dict[str, Any]:
        return {"eps": 1.0, "min_samples": 5}


def test_labels_are_numbered_in_order_of_first_appearance() -> None:
    assert canonical_labels(np.array([4, 4, -1, 2, 4, 2])).tolist() == [0, 0, -1, 1, 0, 1]


def test_non_finite_features_are_refused() -> None:
    x, _ = blobs()
    x[0, 0] = np.nan
    with pytest.raises(ValueError, match="finite"):
        SklearnHdbscan().search(x, {"min_cluster_size": 10}, seed=1)


class _ShiftedGpu:
    """A stand-in GPU adapter: the CPU reference's labels, optionally with membership noise."""

    algorithm_id = "fake-gpu-hdbscan"
    backend = Backend.CUDA

    def __init__(self, noise: float = 0.0, scramble: bool = False) -> None:
        self.noise, self.scramble = noise, scramble

    def search(self, features: np.ndarray, params: Any, seed: int) -> ClusterLabels:
        ref = SklearnHdbscan().search(features, params, seed)
        labels = np.where(np.arange(len(ref.labels)) % 2 == 0, -1, ref.labels) if self.scramble else ref.labels
        probs = np.where(labels >= 0, np.clip(ref.probabilities - self.noise, 0, 1), 0.0)
        return ClusterLabels(labels, probs, self.algorithm_id, dict(params))


@pytest.mark.req("STL-004")
def test_agreement_passes_only_when_labels_and_memberships_match_the_cpu_reference() -> None:
    x, _ = blobs()
    params = {"min_cluster_size": 10, "min_samples": 5}
    same = agreement(_ShiftedGpu(), SklearnHdbscan(), x, params, release="0.1.0")
    assert same.passed and same.rand_index == 1.0
    assert not agreement(_ShiftedGpu(noise=0.2), SklearnHdbscan(), x, params, release="0.1.0").passed
    assert not agreement(_ShiftedGpu(scramble=True), SklearnHdbscan(), x, params, release="0.1.0").passed
    doc = same.to_document()
    assert (doc["algorithm_id"], doc["reference_id"], doc["release"]) == (
        "fake-gpu-hdbscan",
        "sklearn-hdbscan",
        "0.1.0",
    )


def test_cuml_absent_is_reported_not_faked() -> None:
    pytest.importorskip("sklearn")
    try:
        import cuml  # noqa: F401
    except ImportError:
        x, _ = blobs()
        with pytest.raises(ClusteringUnavailable, match="cuML is not installed"):
            CumlHdbscan().search(x, {"min_cluster_size": 10}, seed=1)
    else:  # pragma: no cover - only on the DGX Spark
        pytest.skip("cuML is installed; the agreement test runs under -m gpu")

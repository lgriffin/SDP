"""ClusterSearch: the predeclared grid of algorithms and hyperparameters, and running one point of it.

The search plans; a ``ClusterSearchAlgorithm`` service executes. It takes only a FeatureMatrix
(STL-002) and records which backend produced each labelling, so GPU results can be kept out of the
gates until their agreement test passes (STL-004).
"""

from __future__ import annotations

import itertools
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from sdp.kernel.ports.compute import ClusterLabels, ClusterSearchAlgorithm
from sdp_stellar.features import FeatureMatrix, PolicyError


@dataclass(frozen=True)
class Configuration:
    algorithm_id: str
    params: Mapping[str, Any]

    @property
    def key(self) -> str:
        return self.algorithm_id + "|" + ",".join(f"{k}={self.params[k]}" for k in sorted(self.params))


def configurations(algorithms: Sequence[str], grid: Mapping[str, Mapping[str, Sequence[Any]]]) -> list[Configuration]:
    """Every grid point, algorithms in predeclared order and parameters in sorted-name order."""
    out: list[Configuration] = []
    for algorithm in algorithms:
        axes = grid.get(algorithm, {})
        names = sorted(axes)
        for combo in itertools.product(*(axes[n] for n in names)):
            out.append(Configuration(algorithm, dict(zip(names, combo, strict=True))))
    if not out:
        raise ValueError("the hyperparameter grid is empty")
    return out


def run(clusterer: ClusterSearchAlgorithm, features: FeatureMatrix, config: Configuration, seed: int) -> ClusterLabels:
    if not isinstance(features, FeatureMatrix):
        raise PolicyError("clustering takes a FeatureMatrix from a registered policy, never raw columns (STL-002)")
    if clusterer.algorithm_id != config.algorithm_id:
        raise ValueError(f"service {clusterer.algorithm_id} cannot run {config.algorithm_id}")
    return clusterer.search(features.values, config.params, seed)


def labelling_document(result: ClusterLabels, config: Configuration, backend: str) -> dict[str, Any]:
    return {
        "config": config.key,
        "algorithm_id": config.algorithm_id,
        "params": dict(config.params),
        "backend": backend,
        "labels": np.asarray(result.labels, dtype=np.int64),
        "probabilities": np.asarray(result.probabilities, dtype=np.float64),
        "n_clusters": result.n_clusters,
    }

"""ClusterSignificance: a per-candidate significance in the feature space (STL-011).

After Hunt & Reffert's cluster significance test (2021, A&A 646, A104): a real cluster's members sit
closer to their neighbours than the field stars around the cluster do. For every member, the
distance to its ``k``-th nearest neighbour among all stars searched is taken in the feature matrix
the clustering saw. The comparison sample is the ``field_multiple`` x members (at least
``min_field``) non-members nearest the candidate's median feature vector: its own surroundings, not
the whole tile. A one-sided Mann-Whitney U test asks whether the members' distances are smaller; its
p-value is quoted as a one-sided Gaussian significance in sigma.

The tree over the tile is built once and shared by every candidate, so a tile of millions of stars
pays for one build and a query per member and comparison star.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy import stats
from scipy.spatial import cKDTree

#: the largest significance quoted: beyond it the p-value underflows a double
MAX_SIGMA = 38.0


@dataclass(frozen=True)
class SignificanceSettings:
    k: int
    field_multiple: float
    min_field: int
    min_sigma: float

    @classmethod
    def from_parameters(cls, doc: Mapping[str, Any]) -> SignificanceSettings:
        """Read as written, refusing rather than coercing a value that would change the test."""
        k, min_field = doc.get("k"), doc.get("min_field")
        if isinstance(k, bool) or not isinstance(k, int) or k < 1:
            raise ValueError(f"significance.k must be a whole number of at least 1, not {k!r}")
        if isinstance(min_field, bool) or not isinstance(min_field, int) or min_field < 2:
            raise ValueError(f"significance.min_field must be a whole number of at least 2, not {min_field!r}")
        multiple, sigma = doc.get("field_multiple"), doc.get("min_sigma")
        for name, value in (("field_multiple", multiple), ("min_sigma", sigma)):
            if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"significance.{name} must be a positive number, not {value!r}")
        assert isinstance(multiple, int | float) and isinstance(sigma, int | float)
        return cls(k, float(multiple), min_field, float(sigma))


def tree(features: np.ndarray) -> cKDTree:
    return cKDTree(np.asarray(features, dtype=np.float64))


def _kth_distance(index: cKDTree, points: np.ndarray, k: int) -> np.ndarray:
    # k + 1 because each queried star is its own nearest neighbour, at distance 0
    distances, _ = index.query(points, k=k + 1)
    return np.asarray(distances[:, -1], dtype=np.float64)


def significance(
    index: cKDTree, features: np.ndarray, members: np.ndarray, settings: SignificanceSettings
) -> float | None:
    """The candidate's significance in sigma, or None when it cannot be assessed: fewer than ``k + 1``
    members, or fewer than ``min_field`` comparison stars, or more neighbours asked for than stars."""
    n = features.shape[0]
    if members.size < settings.k + 1 or n < settings.k + 2:
        return None
    want = max(settings.min_field, math.ceil(settings.field_multiple * members.size))
    centre = np.median(features[members], axis=0)
    ask = min(n, want + members.size)
    _, nearest = index.query(centre, k=ask)
    nearest = np.atleast_1d(nearest)
    field = nearest[~np.isin(nearest, members)][:want]
    if field.size < settings.min_field:
        return None
    member_d = _kth_distance(index, features[members], settings.k)
    field_d = _kth_distance(index, features[field], settings.k)
    p = float(stats.mannwhitneyu(member_d, field_d, alternative="less").pvalue)
    if p <= 0.0:
        return MAX_SIGMA
    return float(min(MAX_SIGMA, max(0.0, stats.norm.isf(p))))

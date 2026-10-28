"""MembershipEstimator: candidates from the reference labelling, membership and stability across the grid.

The reference labelling is the first grid point (predeclared order). For each of its clusters, every
other labelling contributes its best-overlapping cluster (Jaccard); stability is the mean of those
overlaps, and a star's membership probability is the mean over labellings of its probability in the
matched cluster (0 where it is not in it). Members are stars at membership >= 0.5.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

MEMBER_THRESHOLD = 0.5


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    members: np.ndarray  # row indices into the region table after cuts
    membership: np.ndarray  # per member
    stability: float
    reference_size: int

    @property
    def mean_membership(self) -> float:
        return float(np.mean(self.membership)) if len(self.membership) else 0.0


def jaccard(a: np.ndarray, b: np.ndarray) -> float:
    union = np.union1d(a, b).size
    return float(np.intersect1d(a, b).size / union) if union else 0.0


def estimate(region: str, labellings: Sequence[Mapping[str, Any]]) -> list[Candidate]:
    if not labellings:
        return []
    reference = labellings[0]
    ref_labels = np.asarray(reference["labels"])
    n = ref_labels.shape[0]
    candidates: list[Candidate] = []
    for label in sorted(set(ref_labels.tolist()) - {-1}):
        rows = np.nonzero(ref_labels == label)[0]
        overlaps: list[float] = []
        prob_sum = np.zeros(n)
        for lab in labellings:
            labels = np.asarray(lab["labels"])
            probs = np.asarray(lab["probabilities"])
            best, best_j = None, 0.0
            for other in sorted(set(labels[rows].tolist()) - {-1}):
                j = jaccard(rows, np.nonzero(labels == other)[0])
                if j > best_j:
                    best, best_j = other, j
            overlaps.append(best_j)
            if best is not None:
                prob_sum += np.where(labels == best, probs, 0.0)
        membership = prob_sum / len(labellings)
        members = np.nonzero(membership >= MEMBER_THRESHOLD)[0]
        candidates.append(
            Candidate(f"{region}-c{label:03d}", members, membership[members], float(np.mean(overlaps)), int(rows.size))
        )
    return candidates

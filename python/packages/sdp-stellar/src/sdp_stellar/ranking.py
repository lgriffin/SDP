"""CandidateRanker: a reproducible score from membership, CMD coherence, stability and novelty."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sdp.kernel.domain.investigation import RankedCandidate, RankingPolicy, TestResult, rank_by_significance

WEIGHTS = {"membership": 0.35, "coherence": 0.25, "stability": 0.25, "novelty": 0.15}


def score(c: Mapping[str, Any]) -> float:
    coherence = c.get("cmd_coherence")
    coherence_term = 0.0 if coherence is None else min(float(coherence), 4.0) / 4.0
    return (
        WEIGHTS["membership"] * float(c["mean_membership"])
        + WEIGHTS["coherence"] * coherence_term
        + WEIGHTS["stability"] * float(c["stability"])
        + WEIGHTS["novelty"] * (1.0 if c["status"] == "potentially_new" else 0.0)
    )


def rank(candidates: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Highest score first; ties broken by candidate id, so the order never depends on input order."""
    scored = [{**c, "score": round(score(c), 12)} for c in candidates]
    scored.sort(key=lambda c: (-c["score"], c["candidate_id"]))
    for i, c in enumerate(scored, start=1):
        c["rank"] = i
    return scored


STRUCTURE_TEST = "astrometric-coherence"


@dataclass(frozen=True)
class StellarCandidateRanker:
    """The CandidateRanker above, for Investigate (Investigate §6), under the ``stellar`` policy.

    A co-moving group scores as a UC-01 candidate from its structure test's result: mean membership,
    CMD coherence and stability across the predeclared grid. A group whose structure test is not
    significant at the policy's alpha against its null fields scores 0. Novelty needs a benchmark
    cross-match Investigate does not run yet, so every group is scored as not novel (deviation D26):
    the term is the same for all, so it does not change the order. Single objects keep the kernel's
    significance score.
    """

    ranker_id: str = "stellar-uc01"
    structure_test: str = STRUCTURE_TEST

    def rank(self, results: Sequence[TestResult], policy: RankingPolicy) -> Sequence[RankedCandidate]:
        ranked = []
        for candidate in rank_by_significance(results, policy):
            subject = candidate.subject
            if subject.structure_id is None:
                ranked.append(candidate)
                continue
            structure = next((r for r in candidate.results if r.test_name == self.structure_test), None)
            value = 0.0
            if structure is not None and structure.p_value is not None and structure.p_value < policy.alpha:
                membership = subject.membership or ()
                value = score(
                    {
                        "mean_membership": sum(membership) / len(membership) if membership else 0.0,
                        "cmd_coherence": structure.details.get("cmd_coherence"),
                        "stability": structure.details.get("stability") or 0.0,
                        "status": "unassessed",
                    }
                )
            ranked.append(RankedCandidate(subject, round(value, 12), candidate.results))
        ranked.sort(key=lambda c: (-c.score, c.subject.key))
        return ranked

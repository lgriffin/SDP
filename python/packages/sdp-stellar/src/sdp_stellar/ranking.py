"""CandidateRanker: a reproducible score from membership, CMD coherence, stability and novelty."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

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

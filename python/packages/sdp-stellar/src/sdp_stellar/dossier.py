"""DossierBuilder: one record per candidate, and the fixed sentence that keeps candidates from becoming
discoveries (STL-G-09)."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

SENTENCE = "{candidate_id} is a candidate open cluster, not a confirmed discovery."
_CLAIM = re.compile(r"\b(discover(?:ed|y|ies)|new (?:open )?cluster|confirmed cluster)\b", re.IGNORECASE)
_SAFE = re.compile(r"\b(candidate|not a confirmed discovery|potentially new)\b", re.IGNORECASE)


def statement(candidate_id: str) -> str:
    return SENTENCE.format(candidate_id=candidate_id)


def claim_violations(texts: Iterable[str]) -> list[str]:
    """Sentences that present a candidate as a discovery without the candidate wording."""
    found = []
    for text in texts:
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            if _CLAIM.search(sentence) and not _SAFE.search(sentence):
                found.append(sentence.strip())
    return found


def build(candidate: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": candidate["candidate_id"],
        "statement": statement(str(candidate["candidate_id"])),
        "status": candidate["status"],
        "reasons": list(candidate["reasons"]),
        "checks": {
            "members": candidate["n_members"],
            "mean_membership": candidate["mean_membership"],
            "stability": candidate["stability"],
            "cmd_coherence": candidate["cmd_coherence"],
            "crossmatch": candidate["crossmatch"],
        },
        "centre": candidate["centre"],
        "rank": candidate.get("rank"),
        "score": candidate.get("score"),
    }

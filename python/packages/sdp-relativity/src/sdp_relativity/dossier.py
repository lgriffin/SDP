"""What the dossier must say about recoverability, and the lint that keeps it honest (REL-008, REL-G-09).

The plates are not available to this project: every result is a reanalysis of published tables
(1920) and a published remeasurement (1979). The lint flags any sentence that presents the work as a
plate remeasurement unless the sentence is itself a denial of that.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

UNRECOVERABLE_INPUTS = (
    "The original 1919 photographic plates (eclipse and comparison plates) are not available to this project.",
    "Individual star image measurements below the precision of the published tables cannot be recovered.",
    "The 1920 plate-constant solutions are known only as published; intermediate reductions are not.",
)

RECOVERABILITY_STATEMENT = (
    "This is a reanalysis of the measurements published in 1920 and of the 1979 remeasurement as published. "
    "It is not a plate remeasurement: no plate was measured for this work."
)

_CLAIM = re.compile(
    r"\b(re-?measur\w*\s+(?:of\s+)?(?:the\s+)?(?:original\s+)?plates?|plates?\s+(?:were|was|have been)\s+re-?measured|"
    r"plate\s+re-?measurement|measured\s+(?:the\s+)?(?:original\s+)?plates)\b",
    re.IGNORECASE,
)
_DENIAL = re.compile(r"\b(not|never|no|cannot|without|isn't|is not)\b", re.IGNORECASE)
_PRIOR_WORK = re.compile(r"\b(1979|Harvey)\b")


def claim_violations(texts: Iterable[str]) -> list[str]:
    """Sentences that describe this table-based reanalysis as a plate remeasurement."""
    found: list[str] = []
    for text in texts:
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            if _CLAIM.search(sentence) and not _DENIAL.search(sentence) and not _PRIOR_WORK.search(sentence):
                found.append(sentence.strip())
    return found


def limitations() -> tuple[str, ...]:
    return (RECOVERABILITY_STATEMENT, *UNRECOVERABLE_INPUTS)

"""The dossier lists unrecoverable inputs and never calls a table reanalysis a plate remeasurement (REL-008)."""

from __future__ import annotations

import pytest

from sdp_relativity.dossier import RECOVERABILITY_STATEMENT, UNRECOVERABLE_INPUTS, claim_violations, limitations

pytestmark = pytest.mark.tier("T1")


@pytest.mark.req("REL-008")
def test_the_limitations_list_every_unrecoverable_input() -> None:
    assert limitations() == (RECOVERABILITY_STATEMENT, *UNRECOVERABLE_INPUTS)
    assert any("plates" in text and "not available" in text for text in UNRECOVERABLE_INPUTS)
    assert claim_violations(limitations()) == []


@pytest.mark.req("REL-008")
@pytest.mark.parametrize(
    "text",
    [
        "We remeasured the original plates and find 1.75 arcsec.",
        "The plates were re-measured for this study.",
        "Our plate remeasurement confirms the result.",
        "This work measured the original plates again.",
    ],
)
def test_a_remeasurement_claim_is_flagged(text: str) -> None:
    assert claim_violations([text]) == [text]


@pytest.mark.req("REL-008")
@pytest.mark.parametrize(
    "text",
    [
        "This is not a plate remeasurement.",
        "The 1979 remeasurement of the plates by Harvey is used as published.",
        "We fit the published tables under 144 reduction scenarios.",
    ],
)
def test_denials_prior_work_and_plain_reanalysis_pass(text: str) -> None:
    assert claim_violations([text]) == []


def test_only_the_offending_sentence_is_reported() -> None:
    text = "We reanalyse the 1920 tables. The plates were remeasured by us. Nothing else changed."
    assert claim_violations([text]) == ["The plates were remeasured by us."]

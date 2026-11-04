"""T2: the Evidence UI's contradiction examples (typescript/.../testing/contradictions.json) have the
shape the engine writes today, so the UI tests read real results (INV-029). Regenerate them from the
synthetic field when a record's keys change."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.investigate.test_cross_survey_contradiction import context, field
from tests.investigate.test_cross_survey_contradiction_channels import WITH_WISE

from sdp_investigate.contradiction import CrossSurveyContradiction

pytestmark = [pytest.mark.tier("T2"), pytest.mark.req("INV-029")]

FIXTURE = Path(__file__).resolve().parents[3] / "typescript/packages/evidence-ui/src/lib/testing/contradictions.json"


def test_the_ui_examples_have_the_engine_s_shape() -> None:
    examples = json.loads(FIXTURE.read_text())
    results = CrossSurveyContradiction().run(field(allwise=True), WITH_WISE, context())
    detail_keys = {k for r in results for k in r.details}
    by_kind: dict[str, set[str]] = {}
    for r in results:
        for c in r.details["comparisons"]:
            by_kind.setdefault(c["kind"], set(c))
    for name, example in examples.items():
        assert example["test_name"] == CrossSurveyContradiction.name, name
        assert example["test_version"] == CrossSurveyContradiction.version, name
        assert set(example["details"]) <= detail_keys, name
        for c in example["details"]["comparisons"]:
            assert set(c) == by_kind[c["kind"]], (name, c["channel"])

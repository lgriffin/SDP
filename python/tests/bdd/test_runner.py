"""The runner's own failure modes: each is what makes a green BDD run mean something (TST-010)."""

from __future__ import annotations

from pathlib import Path

import pytest

from .runner import StepLibrary, StepNotDefined, scenarios

pytestmark = [pytest.mark.tier("harness"), pytest.mark.req("TST-010")]

FEATURE = """Feature: F
  Rule: KRN-001: The kernel shall register versioned hypotheses and record every edit as a new version that retains every prior version.
    Scenario: S
      Given a thing
      And another thing
      When it happens
      Then it is so
      But not otherwise
"""


def test_a_scenario_inherits_the_rule_requirement_and_and_but_take_the_previous_kind(tmp_path: Path) -> None:
    path = tmp_path / "f.feature"
    path.write_text(FEATURE)
    [scenario] = list(scenarios(path))
    assert scenario.requirement == "KRN-001"
    assert [kind for kind, _ in scenario.steps] == ["given", "given", "when", "then", "then"]
    assert scenario.line == 3


def test_a_step_without_a_definition_fails() -> None:
    with pytest.raises(StepNotDefined, match="no given step matches 'a thing'"):
        StepLibrary().find("given", "a thing")


def test_a_step_with_two_definitions_fails() -> None:
    library = StepLibrary()
    library.given("a {what}")(lambda ctx, what: None)
    library.given("a thing")(lambda ctx: None)
    with pytest.raises(StepNotDefined, match="2 given steps match"):
        library.find("given", "a thing")


def test_a_step_of_the_wrong_kind_does_not_match() -> None:
    library = StepLibrary()
    library.then("a thing")(lambda ctx: None)
    with pytest.raises(StepNotDefined):
        library.find("given", "a thing")


def test_a_scenario_outside_a_rule_has_no_requirement(tmp_path: Path) -> None:
    path = tmp_path / "f.feature"
    path.write_text("Feature: F\n  Scenario: loose\n    Given a thing\n")
    [scenario] = list(scenarios(path))
    assert scenario.rule is None and scenario.requirement is None


def test_a_rule_without_an_id_has_no_requirement(tmp_path: Path) -> None:
    path = tmp_path / "f.feature"
    path.write_text("Feature: F\n  Rule: the kernel shall work\n    Scenario: S\n      Given a thing\n")
    [scenario] = list(scenarios(path))
    assert scenario.rule == "the kernel shall work" and scenario.requirement is None

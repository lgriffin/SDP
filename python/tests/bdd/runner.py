"""A small Gherkin runner for the BDD tier, in the shape of ESI.ts's ``bindFeature`` and step library.

Every ``.feature`` file under ``tests/bdd/features`` is collected by pytest; each Scenario becomes one
test item named ``Feature › Rule › Scenario``. A step is found by its text in one global library, so
any feature can use any step. The library is strict: a step with no definition, or with two that
match, fails the scenario rather than being skipped.

The Gherkin is parsed by Cucumber's own parser (``gherkin-official``), and step patterns use ``parse``
(``"the hypothesis {hid} is registered"``).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import parse
import pytest
from gherkin.parser import Parser
from gherkin.pickles.compiler import Compiler

#: A Rule's title is the requirement it states: ``KRN-002: When a predeclaration is frozen, ...``.
RULE_TITLE = re.compile(r"^(?P<id>[A-Z]{3}-\d{3}): (?P<text>.+)$")
KEYWORDS = {"Context": "given", "Action": "when", "Outcome": "then"}

StepFunction = Callable[..., None]


@dataclass(frozen=True)
class Step:
    kind: str
    pattern: parse.Parser
    function: StepFunction


class StepLibrary:
    """Every step definition, by kind. ``And`` and ``But`` take the kind of the step before them."""

    def __init__(self) -> None:
        self.steps: list[Step] = []

    def _register(self, kind: str, text: str) -> Callable[[StepFunction], StepFunction]:
        def decorate(function: StepFunction) -> StepFunction:
            self.steps.append(Step(kind, parse.compile(text), function))
            return function

        return decorate

    def given(self, text: str) -> Callable[[StepFunction], StepFunction]:
        return self._register("given", text)

    def when(self, text: str) -> Callable[[StepFunction], StepFunction]:
        return self._register("when", text)

    def then(self, text: str) -> Callable[[StepFunction], StepFunction]:
        return self._register("then", text)

    def find(self, kind: str, text: str) -> tuple[StepFunction, dict[str, Any]]:
        found = [(s, m) for s in self.steps if s.kind == kind and (m := s.pattern.parse(text)) is not None]
        if not found:
            raise StepNotDefined(f"no {kind} step matches {text!r}")
        if len(found) > 1:
            raise StepNotDefined(f"{len(found)} {kind} steps match {text!r}: {[s.pattern._format for s, _ in found]}")
        step, match = found[0]
        return step.function, dict(match.named)


class StepNotDefined(Exception):
    """A scenario names a step the library does not define exactly once."""


library = StepLibrary()
given, when, then = library.given, library.when, library.then


@dataclass(frozen=True)
class Scenario:
    feature: str
    rule: str | None
    requirement: str | None
    name: str
    line: int
    steps: tuple[tuple[str, str], ...]


def scenarios(path: Path) -> Iterator[Scenario]:
    """The scenarios of one feature file, each with the Rule it sits under and that Rule's requirement id."""
    # gherkin types its AST as TypedDicts; this reads it as plain JSON
    document: Any = Parser().parse(path.read_text(encoding="utf-8"))
    document["uri"] = str(path)
    feature = document["feature"]
    rule_of: dict[str, str] = {}
    for child in feature["children"]:
        for grandchild in child.get("rule", {}).get("children", []):
            if "scenario" in grandchild:
                rule_of[grandchild["scenario"]["id"]] = child["rule"]["name"]
    for pickle in Compiler().compile(document):
        rule = next((rule_of[i] for i in pickle["astNodeIds"] if i in rule_of), None)
        match = RULE_TITLE.match(rule) if rule else None
        kinds: list[tuple[str, str]] = []
        for step in pickle["steps"]:
            kind = KEYWORDS.get(step["type"], kinds[-1][0] if kinds else "given")
            kinds.append((kind, step["text"]))
        line = next(
            s["scenario"]["location"]["line"]
            for s in _all_scenarios(feature)
            if s["scenario"]["id"] in pickle["astNodeIds"]
        )
        yield Scenario(feature["name"], rule, match["id"] if match else None, pickle["name"], line, tuple(kinds))


def _all_scenarios(feature: dict[str, Any]) -> Iterator[dict[str, Any]]:
    for child in feature["children"]:
        if "scenario" in child:
            yield child
        for grandchild in child.get("rule", {}).get("children", []):
            if "scenario" in grandchild:
                yield grandchild


class FeatureFile(pytest.File):
    def collect(self) -> Iterator[pytest.Item]:
        for scenario in scenarios(self.path):
            if scenario.requirement is None:
                raise self.CollectError(
                    f"{self.path.name}:{scenario.line}: scenario {scenario.name!r} is not under a Rule titled '<ID>: <requirement>'"
                )
            title = f"{scenario.feature} › {scenario.requirement} › {scenario.name}"
            item = ScenarioItem.from_parent(self, name=title, scenario=scenario)
            item.add_marker(pytest.mark.req(scenario.requirement))
            item.add_marker(pytest.mark.tier("T4"))
            yield item

    class CollectError(Exception):
        pass


class ScenarioItem(pytest.Item):
    def __init__(self, *, scenario: Scenario, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.scenario = scenario

    def runtest(self) -> None:
        context: dict[str, Any] = {}
        for kind, text in self.scenario.steps:
            function, arguments = library.find(kind, text)
            function(context, **arguments)

    def repr_failure(self, excinfo: pytest.ExceptionInfo[BaseException], style: Any = None) -> str:
        steps = "\n".join(f"  {kind.title()} {text}" for kind, text in self.scenario.steps)
        return f"{self.scenario.feature} › {self.scenario.rule}\n  Scenario: {self.scenario.name}\n{steps}\n\n{super().repr_failure(excinfo)}"

    def reportinfo(self) -> tuple[Path, int, str]:
        return self.path, self.scenario.line - 1, f"Scenario: {self.scenario.name}"

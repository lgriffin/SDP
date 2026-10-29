"""BDD tier (TST-010): every ``.feature`` under ``features/`` is collected, one test per Scenario.

A Rule's title is ``<ID>: <the requirement's .ears text>``. The id becomes each scenario's ``req``
marker, which the root conftest writes to JUnit, so ``spec-harness trace`` counts the scenario as
evidence for the requirement; ``spec-harness bdd`` checks that the text matches the ``.ears`` file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from . import steps_kernel, steps_planning  # noqa: F401  (each steps_* module registers its steps in the one library)
from .runner import FeatureFile


def pytest_collect_file(parent: pytest.Collector, file_path: Path) -> pytest.Collector | None:
    if file_path.suffix == ".feature":
        return FeatureFile.from_parent(parent, path=file_path)
    return None

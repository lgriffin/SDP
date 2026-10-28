"""Shared test configuration: requirement markers become JUnit properties for spec-harness trace."""

from __future__ import annotations

import pytest


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        for marker in item.iter_markers("req"):
            for requirement in marker.args:
                item.user_properties.append(("req", requirement))
        for marker in item.iter_markers("gate"):
            for gate in marker.args:
                item.user_properties.append(("gate", gate))
        for marker in item.iter_markers("tier"):
            item.user_properties.append(("tier", marker.args[0]))

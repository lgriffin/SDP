"""Shared test configuration: requirement markers become JUnit properties for spec-harness trace."""

from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--tier", action="append", default=[], help="run only tests marked with this tier (repeatable), e.g. --tier T8"
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    wanted = set(config.getoption("--tier"))
    if wanted:
        keep = [i for i in items if wanted & {m.args[0] for m in i.iter_markers("tier")}]
        config.hook.pytest_deselected(items=[i for i in items if i not in keep])
        items[:] = keep
    for item in items:
        for marker in item.iter_markers("req"):
            for requirement in marker.args:
                item.user_properties.append(("req", requirement))
        for marker in item.iter_markers("gate"):
            for gate in marker.args:
                item.user_properties.append(("gate", gate))
        for marker in item.iter_markers("tier"):
            item.user_properties.append(("tier", marker.args[0]))

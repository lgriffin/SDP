"""Shared test configuration: requirement markers become JUnit properties for spec-harness trace."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

#: where the full real-data tables live when no SDP_REALDATA is set: the project share in the cloud sandbox
REALDATA_FALLBACKS = (Path("/mnt/project-files"),)


def realdata_root() -> Path | None:
    """The folder holding full real-data tables (T8R, TST-011): SDP_REALDATA, else a known share."""
    env = os.environ.get("SDP_REALDATA")
    if env:
        return Path(env)
    return next((p for p in REALDATA_FALLBACKS if p.is_dir()), None)


def realdata_file(relative: str) -> Path:
    """A full real-data table by its path under the real-data folder, or skip the test saying where it looked."""
    root = realdata_root()
    if root is None:
        pytest.skip(f"full real data not here: set SDP_REALDATA to the folder holding {relative}")
    path = root / relative
    if not path.is_file():
        pytest.skip(f"full real data not here: {path} is missing")
    return path


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

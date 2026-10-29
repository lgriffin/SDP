"""tools/mutation.py: the known-weak fixture check and the target list the CI matrix is built from."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

TOOL = Path(__file__).resolve().parents[2] / "tools" / "mutation.py"


@pytest.fixture(scope="module")
def tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sdp_mutation_tool", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.req("TST-009")
def test_a_fixture_run_with_a_kill_and_a_survivor_passes(tool: ModuleType) -> None:
    assert tool.fixture_problems({"killed": 5, "survived": 4, "no_tests": 2}) == []
    assert tool.fixture_problems({"timeout": 1, "no_tests": 1}) == []


@pytest.mark.req("TST-009")
def test_a_fixture_run_that_kills_nothing_fails(tool: ModuleType) -> None:
    assert tool.fixture_problems({"killed": 0, "survived": 11}) == [
        "no fixture mutant was killed: the run cannot detect a fault"
    ]


@pytest.mark.req("TST-009")
def test_a_fixture_run_where_everything_dies_fails(tool: ModuleType) -> None:
    assert tool.fixture_problems({"killed": 11}) == ["no fixture mutant survived: the run cannot show a weak test"]


@pytest.mark.req("TST-009")
def test_the_fixture_is_mutated_alone_and_never_scored(tool: ModuleType) -> None:
    assert tool.FIXTURE.name not in tool.TARGETS
    assert (TOOL.parents[1] / tool.FIXTURE.root / "src" / "weak" / "grade.py").is_file()


@pytest.mark.req("TST-004")
def test_every_kernel_target_selects_every_suite_that_drives_the_kernel(tool: ModuleType) -> None:
    kernel = [t for t in tool.TARGETS.values() if t.package == "sdp-kernel"]
    assert {t.name for t in kernel} == {
        "sdp-kernel-domain",
        "sdp-kernel-science",
        "sdp-kernel-application",
        "sdp-kernel-integration",
    }
    for target in kernel:
        assert target.tests == tool.KERNEL_TESTS
    root = TOOL.parents[1]
    assert all((root / path).is_dir() for path in (*tool.KERNEL_TESTS, *tool.DATA))


@pytest.mark.req("TST-004")
def test_the_shards_cover_every_science_module(tool: ModuleType) -> None:
    tool.check_shards()

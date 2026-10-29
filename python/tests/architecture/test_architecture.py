"""T0 architecture rules (§4, §15). import-linter enforces the layer contracts; these rules name
the requirements so the spec harness can trace them."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from pytest_archon import archrule

pytestmark = pytest.mark.tier("T0")

SCIENCE_MODULES = ("sdp_relativity*", "sdp_stellar*", "sdp.relativity*", "sdp.stellar*")
INFRASTRUCTURE = (
    "sdp.adapters*",
    "sdp.cli*",
    "sdp.api*",
    "psycopg*",
    "duckdb*",
    "httpx*",
    "sklearn*",
    "cuml*",
    "strawberry*",
    "typer*",
    "mlflow*",
)


@pytest.mark.req("KRN-011")
def test_kernel_never_imports_science_modules() -> None:
    (
        archrule("kernel reaches science only through ScientificProtocol")
        .match("sdp.kernel*")
        .should_not_import(*SCIENCE_MODULES)
        .check("sdp.kernel")
    )


@pytest.mark.req("NFR-006", "DSI-001")
def test_domain_and_science_ports_do_not_depend_on_infrastructure() -> None:
    (
        archrule("kernel is free of infrastructure")
        .match("sdp.kernel*")
        .should_not_import(*INFRASTRUCTURE, "sdp.testing*")
        .check("sdp.kernel")
    )


@pytest.mark.req("NFR-006")
def test_domain_is_pure() -> None:
    (
        archrule("domain imports nothing from the outer layers")
        .match("sdp.kernel.domain*")
        .should_not_import("sdp.kernel.application*", "sdp.kernel.ports*", "sdp.kernel.science*", "pyarrow*")
        .check("sdp.kernel")
    )


@pytest.mark.req("TST-005")
def test_no_mocks_or_pickle_in_kernel_or_fakes() -> None:
    for package in ("sdp.kernel", "sdp.testing", "sdp.contracts", "sdp.adapters", "sdp.cli", "sdp.api"):
        (
            archrule(f"fakes, not mocks; no pickle ({package})")
            .match(f"{package}*")
            .should_not_import("unittest.mock", "mock", "pickle", "dill", "cloudpickle")
            .check(package)
        )


@pytest.mark.req("NFR-006")
def test_import_linter_contracts_are_kept() -> None:
    lint_imports = shutil.which("lint-imports")
    if lint_imports is None:
        pytest.skip("import-linter is not installed in this environment")
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run([lint_imports], cwd=root, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.req("NFR-006")
def test_adapters_never_import_test_fakes() -> None:
    (
        archrule("production adapters do not depend on sdp.testing")
        .match("sdp.adapters*")
        .should_not_import("sdp.testing*")
        .check("sdp.adapters")
    )


def test_driving_adapters_reach_infrastructure_only_through_bootstrap() -> None:
    for package in ("sdp.cli", "sdp.api"):
        (
            archrule(f"{package} wires adapters only through sdp.adapters.bootstrap and settings")
            .match(f"{package}*")
            .should_not_import(
                "sdp.adapters.postgres.__init__",
                "sdp.adapters.storage*",
                "sdp.adapters.ledger*",
                "sdp.adapters.compute*",
            )
            .check(package)
        )


@pytest.mark.req("KRN-011")
def test_the_science_extra_brings_every_package_that_registers_a_plugin() -> None:
    import tomllib

    packages = Path(__file__).resolve().parents[2] / "packages"
    plugins = {
        doc["project"]["name"]
        for doc in (tomllib.loads(p.read_text("utf-8")) for p in packages.glob("*/pyproject.toml"))
        if any(group.startswith("sdp.") for group in doc["project"].get("entry-points", {}))
    }
    assert plugins >= {"sdp-investigate", "sdp-stellar"}
    for entry in ("sdp-cli", "sdp-api"):
        project = tomllib.loads((packages / entry / "pyproject.toml").read_text("utf-8"))["project"]
        assert set(project["optional-dependencies"]["science"]) == plugins, entry
        assert not plugins & set(project["dependencies"]), f"{entry}: science modules stay optional"

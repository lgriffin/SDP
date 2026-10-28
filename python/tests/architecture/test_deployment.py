"""NFR-009 in the deployment files: no container runs as root, every unit drops its capabilities,
and credentials reach containers only as Podman secrets or secret files, never as values."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

INFRA = Path(__file__).resolve().parents[3] / "infrastructure"
CONTAINERFILES = sorted((INFRA / "containers").glob("*.Containerfile"))
UNITS = sorted((INFRA / "quadlet").glob("*.container"))
COMPOSE = INFRA / "compose" / "compose.yaml"
ROOT = {"", "0", "root", "0:0", "root:root"}
#: Units that cannot run unprivileged inside their user namespace, and why. Under rootless Podman
#: their uid 0 is still the unprivileged host user (§15 least privilege).
ROOT_IN_NAMESPACE = {"sdp-dcgm-exporter.container": "DCGM needs uid 0 and SYS_ADMIN to read GPU counters"}
CREDENTIAL = re.compile(r"PASSWORD|TOKEN|SECRET|DATABASE_URL|DATA_SOURCE_NAME", re.IGNORECASE)


def _credential_keys(assignments: str) -> list[str]:
    """Keys in ``K=V K2=V2`` that name a credential and are not ``*_FILE`` pointers to a secret."""
    return [k for k in re.findall(r"(\w+)=", assignments) if CREDENTIAL.search(k) and not k.endswith("_FILE")]


pytestmark = [pytest.mark.tier("T0"), pytest.mark.req("NFR-009")]


def _keys(path: Path) -> dict[str, list[str]]:
    keys: dict[str, list[str]] = {}
    for line in path.read_text("utf-8").splitlines():
        if "=" in line and not line.startswith(("#", "[")):
            key, _, value = line.partition("=")
            keys.setdefault(key.strip(), []).append(value.strip())
    return keys


def test_there_are_deployment_files_to_check() -> None:
    assert len(CONTAINERFILES) >= 4 and len(UNITS) >= 8 and COMPOSE.exists()


@pytest.mark.parametrize("containerfile", CONTAINERFILES, ids=lambda p: p.name)
def test_every_image_ends_as_a_non_root_user(containerfile: Path) -> None:
    final_stage = containerfile.read_text("utf-8").split("\nFROM ")[-1]
    users = re.findall(r"^USER\s+(\S+)", final_stage, re.MULTILINE)
    assert users, f"{containerfile.name}: the final stage sets no USER"
    assert users[-1] not in ROOT


@pytest.mark.parametrize("unit", UNITS, ids=lambda p: p.name)
def test_every_quadlet_unit_runs_unprivileged_with_capabilities_dropped(unit: Path) -> None:
    keys = _keys(unit)
    if unit.name not in ROOT_IN_NAMESPACE:
        assert keys.get("User", [""])[-1] not in ROOT, f"{unit.name} must set a non-root User="
    assert "ALL" in keys.get("DropCapability", []), f"{unit.name} must DropCapability=ALL"
    assert keys.get("NoNewPrivileges") == ["true"], f"{unit.name} must set NoNewPrivileges=true"


@pytest.mark.parametrize("unit", UNITS, ids=lambda p: p.name)
def test_no_unit_carries_a_credential_value(unit: Path) -> None:
    for value in _keys(unit).get("Environment", []):
        assert not _credential_keys(value), f"{unit.name}: {value} puts a credential in the environment"


def test_the_compose_stack_runs_unprivileged_and_takes_credentials_from_secret_files() -> None:
    doc = yaml.safe_load(COMPOSE.read_text("utf-8"))
    anchors = {k: v for k, v in doc.items() if k.startswith("x-")}
    assert all(s["file"].startswith("./secrets/") for s in doc["secrets"].values())
    for name, service in doc["services"].items():
        assert str(service.get("user", "")) not in ROOT, f"compose service {name} must set a non-root user"
        assert service.get("cap_drop") == ["ALL"], f"compose service {name} must drop all capabilities"
        for key, value in (service.get("environment") or {}).items():
            assert not _credential_keys(f"{key}={value}"), f"compose service {name}: {key}={value}"
    assert anchors


def test_api_and_runner_read_their_credentials_from_mounted_secrets() -> None:
    for unit in ("sdp-api.container", "sdp-runner@.container"):
        keys = _keys(INFRA / "quadlet" / unit)
        assert any(s.startswith("sdp_database_url,type=mount") for s in keys["Secret"])
        assert any("SDP_DATABASE_URL_FILE=/run/secrets/database_url" in e for e in keys["Environment"])

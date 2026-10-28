"""T6 contract tests, Python side. The TypeScript suite runs the same fixtures through Zod."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from sdp.contracts import MODELS, ContractViolation, load, schema_major, validate

pytestmark = pytest.mark.tier("T6")

REPO = Path(__file__).resolve().parents[3]
CONTRACTS = REPO / "contracts"
PACKAGE_SCHEMAS = REPO / "python/packages/sdp-contracts/src/sdp/contracts/schemas"


def fixtures(kind: str) -> list[tuple[str, Path]]:
    return [
        (path.parent.name, path)
        for path in sorted((CONTRACTS / "fixtures" / kind).glob("*/*.json"))
        if not path.name.endswith(".expect.json")
    ]


@pytest.mark.req("CTR-001")
def test_packaged_schemas_are_the_source_schemas() -> None:
    source = {p.name: p.read_bytes() for p in (CONTRACTS / "schemas").glob("*.schema.json")}
    packaged = {p.name: p.read_bytes() for p in PACKAGE_SCHEMAS.glob("*.schema.json")}
    assert packaged == source
    assert set(MODELS) == {n.removesuffix(".schema.json") for n in source} - {"common"}


@pytest.mark.req("CTR-003")
@pytest.mark.parametrize(("name", "path"), fixtures("valid"), ids=lambda v: getattr(v, "stem", v))
def test_valid_fixtures_are_accepted(name: str, path: Path) -> None:
    document = json.loads(path.read_text())
    model = load(name, document)
    assert isinstance(model, MODELS[name])


@pytest.mark.req("CTR-003")
@pytest.mark.parametrize(("name", "path"), fixtures("invalid"), ids=lambda v: getattr(v, "stem", v))
def test_invalid_fixtures_are_rejected_at_the_expected_path(name: str, path: Path) -> None:
    document = json.loads(path.read_text())
    expected = json.loads(path.with_suffix("").with_suffix(".expect.json").read_text())["path"]
    with pytest.raises(ContractViolation) as caught:
        load(name, document)
    assert expected in {e.path for e in caught.value.errors}


@pytest.mark.req("CTR-004")
def test_manifests_carry_a_schema_version_with_a_readable_major() -> None:
    for _, path in fixtures("valid"):
        if path.parent.name == "evidence-manifest":
            document: dict[str, Any] = json.loads(path.read_text())
            assert schema_major(document) == 1
            assert not validate("evidence-manifest", document)


@pytest.mark.req("CTR-002")
def test_committed_pydantic_models_match_a_fresh_generation(tmp_path: Path) -> None:
    import shutil
    import subprocess

    if shutil.which("datamodel-codegen") is None:
        pytest.skip("datamodel-code-generator is not installed")
    env = {**__import__("os").environ, "SDP_PYDANTIC_OUT": str(tmp_path)}
    subprocess.run([str(CONTRACTS / "tools/generate_python.sh")], check=True, env=env, capture_output=True)
    committed = REPO / "python/packages/sdp-contracts/src/sdp/contracts"
    for folder in ("v1", "schemas"):
        fresh = {p.name: p.read_bytes() for p in (tmp_path / folder).iterdir() if p.is_file()}
        current = {p.name: p.read_bytes() for p in (committed / folder).iterdir() if p.is_file()}
        assert fresh == current, f"{folder} is stale: run contracts/tools/generate.sh"

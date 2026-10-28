from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from referencing import Registry, Resource

from sdp.kernel.domain.canonical import canonical_json

from .conftest import Harness

pytestmark = pytest.mark.tier("T4")

SCHEMAS = Path(__file__).resolve().parents[3] / "contracts" / "schemas"


def manifest_validator() -> jsonschema.Draft202012Validator:
    registry: Registry = Registry()
    for path in SCHEMAS.glob("*.schema.json"):
        document = json.loads(path.read_text())
        resource = Resource.from_contents(document)
        registry = registry.with_resources([(path.name, resource), (document["$id"], resource)])
    schema = json.loads((SCHEMAS / "evidence-manifest.schema.json").read_text())
    return jsonschema.Draft202012Validator(schema, registry=registry)


@pytest.mark.req("KRN-004")
def test_records_inputs_environment_and_seed(harness: Harness) -> None:
    experiment = harness.run()
    manifest = harness.k.kernel.query_evidence.manifest(experiment.experiment_id)
    assert manifest is not None

    (series,) = manifest["inputs"]
    assert series["role"] == "series"
    assert series["artifact_id"] == experiment.definition.inputs[0].artifact_id.value
    assert series["artifact_id"].startswith("sha256:")
    environment = manifest["environment"]
    assert environment["code_revision"] and environment["lock_digest"].startswith("sha256:")
    assert environment["backend"] == "cpu"
    assert manifest["execution"]["seed"] == 42
    assert {t["task_id"]: t["policy"]["rtol"] for t in manifest["execution"]["tasks"]} == {
        "fit": 1e-9,
        "residuals": 1e-9,
    }
    assert manifest["intent"]["predeclaration_hash"] == experiment.definition.predeclaration_hash
    assert manifest["intent"]["ledger_entry_id"] == experiment.ledger_entry_id
    assert list(manifest_validator().iter_errors(manifest)) == []


@pytest.mark.req("KRN-004")
def test_manifest_is_stored_at_its_logical_path(harness: Harness) -> None:
    experiment = harness.run()
    stored = harness.k.kernel.query_evidence.experiment(experiment.experiment_id)
    assert stored is not None and stored.manifest_id is not None
    path = f"experiments/synthetic-linear/{experiment.experiment_id}/manifest.json"
    assert harness.k.store.resolve(path) == stored.manifest_id
    manifest = harness.k.kernel.query_evidence.manifest(experiment.experiment_id)
    assert harness.k.store.get(stored.manifest_id) == canonical_json(manifest).encode()


@pytest.mark.req("KRN-013")
def test_cuda_runs_record_driver_cuda_and_agreement(harness: Harness) -> None:
    from sdp.kernel.ports.driving import DefineExperimentCommand
    from sdp.kernel.science.plan import Backend
    from sdp.testing.synthetic import make_series

    series = harness.k.store.put(make_series(), "application/json")
    experiment = harness.k.kernel.define_experiment.execute(
        DefineExperimentCommand("synthetic-linear", harness.freeze().hash, {"series": series}, backend=Backend.CUDA)
    )
    manifest = harness.k.kernel.query_evidence.manifest(experiment.experiment_id)
    assert manifest is not None
    environment = manifest["environment"]
    assert environment["backend"] == "cuda"
    assert environment["driver_version"] and environment["cuda_version"] and environment["agreement_test_id"]

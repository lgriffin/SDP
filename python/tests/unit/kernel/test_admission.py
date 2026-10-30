"""INT-016: an input acquired through an integration adapter needs a passing compatibility record
for its product type before the kernel issues a verdict; the records go in the manifest."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from sdp.contracts import validate
from sdp.kernel.domain.dataset import AcquisitionRecord, DatasetArtifact, DialectRef, ProductUse, admission_type
from sdp.kernel.domain.errors import InvariantViolation
from sdp.kernel.domain.integration import (
    AdapterCompatibilityRecord,
    ConformanceKind,
    IntegrationRefused,
    ProductType,
)
from sdp.kernel.domain.sources import SourceKind, SourceRef
from sdp.kernel.ports.driving import DefineExperimentCommand, FreezePredeclarationCommand
from sdp.testing.fakes import InMemoryArtifactStore
from sdp.testing.fakes.integration import StaticCompatibilityRecords
from sdp.testing.integration import compatibility
from sdp.testing.synthetic import SyntheticLinearProtocol, make_series
from sdp.testing.wiring import InMemoryKernel, build_in_memory_kernel

pytestmark = pytest.mark.tier("T1")

USE = ProductUse("mast-observations", "1.0.0", "image")
KEY = "sha256:" + "0" * 64
MAST = SourceRef(SourceKind.ARCHIVE, "mast", "public", "MAST", release_label="mast:jwst")


def _run(k: InMemoryKernel, product: ProductUse | None = USE) -> str:
    protocol = SyntheticLinearProtocol()
    frozen, _ = k.kernel.freeze_predeclaration.execute(FreezePredeclarationCommand(protocol.predeclare(), "leigh"))
    data = make_series(noise=0.1)
    series = k.store.put(data, "application/json")
    acquired = AcquisitionRecord("SELECT 1", KEY, "mast:jwst", datetime(2026, 10, 28, tzinfo=UTC), series, len(data))
    raw = DatasetArtifact.acquired_raw(
        actual=series, media_type="application/json", byte_count=len(data), source_ref=MAST, acquisition=acquired
    )
    with k.db.unit_of_work() as uow:
        uow.datasets.add(replace(raw, product=product))
        uow.commit()
    experiment = k.kernel.define_experiment.execute(
        DefineExperimentCommand("synthetic-linear", frozen.hash, {"series": series}, {}, seed=42)
    )
    return k.kernel.run_experiment.execute(experiment.experiment_id).experiment_id


@pytest.mark.req("INT-016")
def test_no_verdict_without_a_compatibility_record() -> None:
    k = build_in_memory_kernel()
    experiment_id = _run(k)
    with pytest.raises(IntegrationRefused, match=r"mast-observations 1\.0\.0 for image.*INT-016"):
        k.kernel.validate_experiment.execute(experiment_id)
    manifest = k.kernel.query_evidence.manifest(experiment_id)
    assert manifest is not None and manifest["verdict"] is None


@pytest.mark.req("INT-016")
@pytest.mark.parametrize(
    "records",
    [
        [compatibility(failing=(ConformanceKind.SEMANTIC,))],
        [compatibility(product_type=ProductType.SPECTRUM)],
        [compatibility("gaia-tap")],
        [compatibility(version="0.9.0")],
    ],
    ids=["failing-semantic", "other-product-type", "other-adapter", "other-version"],
)
def test_no_verdict_with_a_failing_or_unrelated_record(records: list[AdapterCompatibilityRecord]) -> None:
    k = build_in_memory_kernel(compatibility=StaticCompatibilityRecords(records))
    with pytest.raises(IntegrationRefused, match="INT-016"):
        k.kernel.validate_experiment.execute(_run(k))


@pytest.mark.req("INT-016")
def test_an_admitted_adapter_gets_a_verdict_and_its_record_in_the_manifest() -> None:
    k = build_in_memory_kernel(
        compatibility=StaticCompatibilityRecords([compatibility(), compatibility(product_type=ProductType.SPECTRUM)])
    )
    experiment_id = _run(k)
    report = k.kernel.validate_experiment.execute(experiment_id)
    assert report.verdict.status.value == "VERIFIED"
    manifest = k.kernel.query_evidence.manifest(experiment_id)
    assert manifest is not None
    assert validate("evidence-manifest", manifest) == []
    assert manifest["schema_version"] == "1.4.0"
    (series,) = manifest["inputs"]
    assert (series["adapter"], series["adapter_version"], series["product_type"]) == (
        "mast-observations",
        "1.0.0",
        "image",
    )
    records = manifest["environment"]["adapter_compatibility"]
    assert [(r["adapter"], r["product_type"], r["passed"]) for r in records] == [("mast-observations", "image", True)]


def test_inputs_not_acquired_through_an_adapter_need_no_record() -> None:
    k = build_in_memory_kernel()
    experiment_id = _run(k, product=None)
    assert k.kernel.validate_experiment.execute(experiment_id).verdict.status.value == "VERIFIED"
    manifest = k.kernel.query_evidence.manifest(experiment_id)
    assert manifest is not None
    assert "adapter" not in manifest["inputs"][0]
    assert "adapter_compatibility" not in manifest["environment"]


@pytest.mark.req("INT-016")
def test_a_dataset_keeps_its_product_use_and_older_documents_are_unchanged() -> None:
    data = make_series(noise=0.1)
    artifact = InMemoryArtifactStore().put(data, "application/json")
    acquired = AcquisitionRecord("q", KEY, "mast:jwst", datetime(2026, 10, 28, tzinfo=UTC), artifact, len(data))
    raw = DatasetArtifact.acquired_raw(
        actual=artifact, media_type="application/json", byte_count=len(data), source_ref=MAST, acquisition=acquired
    )
    plain = raw.to_document()
    assert plain["schema_version"] == "1.0.0" and "product" not in plain
    with_use = replace(raw, product=USE).to_document()
    assert with_use["schema_version"] == "1.2.0" and with_use["product"] == {
        "adapter": "mast-observations",
        "adapter_version": "1.0.0",
        "product_type": "image",
    }
    assert validate("dataset-artifact", with_use) == []
    assert DatasetArtifact.from_document(with_use).product == USE
    with pytest.raises(InvariantViolation, match="not a product type"):
        ProductUse("mast-observations", "1.0.0", "preview")


@pytest.mark.req("INT-016")
def test_a_curated_artifact_keeps_the_product_use_of_its_raw_parent() -> None:
    data = make_series(noise=0.1)
    store = InMemoryArtifactStore()
    artifact = store.put(data, "application/json")
    acquired = AcquisitionRecord("q", KEY, "mast:jwst", datetime(2026, 10, 28, tzinfo=UTC), artifact, len(data))
    raw = replace(
        DatasetArtifact.acquired_raw(
            actual=artifact, media_type="application/json", byte_count=len(data), source_ref=MAST, acquisition=acquired
        ),
        product=USE,
    )
    curated = raw.curate(
        curated_id=store.put(b"curated", "text/csv"),
        byte_count=7,
        dialect=DialectRef("mast-jwst", "1.0.0"),
        media_type="text/csv",
    )
    assert curated.product == USE


@pytest.mark.req("INT-016", "INT-022")
@pytest.mark.parametrize(
    ("product_type", "admitted"),
    [
        ("lightcurve", "timeseries"),
        ("astrometry", "measurements"),
        ("image", "image"),
        ("timeseries", "timeseries"),
        ("thumbnail", None),
        ("preview", None),
        ("unknown", None),
    ],
)
def test_a_canonical_product_is_admitted_as_its_obscore_type(product_type: str, admitted: str | None) -> None:
    assert admission_type(product_type) == admitted
    if admitted is not None:
        assert ProductUse("adapter", "1.0.0", admitted).product_type == admitted

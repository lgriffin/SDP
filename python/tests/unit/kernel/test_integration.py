"""T1: the v0.4.0 integration domain: capability matching, the product envelope, acquisition plans
and adapter compatibility, each invariant of v0.4.0 §9 with a test named after it."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from sdp.kernel.domain.errors import CurationBlocked, InvariantViolation
from sdp.kernel.domain.integration import (
    AccessStatus,
    ApprovalDecision,
    ConformanceKind,
    EvidenceRequirement,
    IntegrationRefused,
    MatchStatus,
    Operation,
    PlanItem,
    ProductType,
    ScientificDataProduct,
    SemanticCheck,
    SemanticValidationReport,
    SourceCapabilityDescriptor,
    match_requirement,
    order_matches,
    require_admitted,
)
from sdp.testing.fakes.integration import descriptor_with_conformance
from sdp.testing.integration import (
    SHA,
    SYNTHETIC_FIELD,
    astrometry_requirement,
    compatibility,
    dasch_descriptor,
    gaia_descriptor,
    image_metadata,
    image_requirement,
    mast_descriptor,
    registry_descriptors,
    sample_plan,
)

pytestmark = pytest.mark.tier("T1")

REGION = SYNTHETIC_FIELD.query_region()


def report(passed: bool = True) -> SemanticValidationReport:
    return SemanticValidationReport(
        image_metadata().product_id, "fixture-validator", "1.0.0", (SemanticCheck("frame", passed, "ICRS"),)
    )


# --- descriptors and matching -----------------------------------------------------------------


@pytest.mark.req("INT-001")
def test_a_registry_descriptor_carries_its_ivoid_and_verification_date() -> None:
    d = mast_descriptor()
    assert d.ivoid and d.verified_on == date(2026, 10, 28)
    with pytest.raises(InvariantViolation, match="ivoid"):
        replace(d, ivoid=None)
    assert SourceCapabilityDescriptor.from_document(d.to_document()) == d


@pytest.mark.req("INT-002")
def test_a_claim_without_a_passing_conformance_suite_cannot_stand() -> None:
    stripped, removed = mast_descriptor().strip_unproven_claims()
    assert ProductType.IMAGE in stripped.product_types
    assert set(removed) == {ProductType.SPECTRUM, ProductType.CUBE, ProductType.TIMESERIES}
    assert not set(removed) & stripped.product_types
    proven = descriptor_with_conformance(mast_descriptor(), {t.value: True for t in mast_descriptor().product_types})
    assert proven.strip_unproven_claims() == (proven, ())


@pytest.mark.req("INT-003")
def test_a_match_reports_every_field_and_fails_on_any_unsatisfied_one() -> None:
    m = match_requirement(mast_descriptor(), REGION, image_requirement())
    assert m.status is MatchStatus.SATISFIED
    fields = {r.field for r in m.reasons}
    assert {"product_types", "missions", "coverage.spatial", "operations", "access", "adapter.conformance"} <= fields
    g = match_requirement(gaia_descriptor(), REGION, image_requirement())
    assert g.status is MatchStatus.UNSATISFIED
    assert any(r.field == "product_types" and r.outcome is MatchStatus.UNSATISFIED for r in g.reasons)


@pytest.mark.req("INT-003")
def test_unknown_coverage_is_partial_not_satisfied() -> None:
    need = EvidenceRequirement("req-plates", "plate-lightcurve", frozenset({ProductType.TIMESERIES}))
    proven = descriptor_with_conformance(dasch_descriptor(), {"timeseries": True})
    m = match_requirement(proven, REGION, need)
    assert m.status is MatchStatus.PARTIAL
    assert any(r.field == "coverage.spatial" and r.outcome is MatchStatus.PARTIAL for r in m.reasons)


def test_an_unadmitted_adapter_cannot_satisfy_a_requirement() -> None:
    m = match_requirement(mast_descriptor(admitted=False), REGION, image_requirement())
    assert m.status is MatchStatus.UNSATISFIED
    assert any(r.field == "adapter.conformance" and r.outcome is MatchStatus.UNSATISFIED for r in m.reasons)


def test_matches_order_satisfied_first_then_by_source() -> None:
    ms = order_matches(match_requirement(d, REGION, image_requirement()) for d in reversed(registry_descriptors()))
    assert [m.source_id for m in ms] == ["synthetic-mast", "synthetic-dasch", "synthetic-gaia"]


@pytest.mark.req("INT-004")
def test_cutout_support_is_recorded_per_product_type_never_globally() -> None:
    d = mast_descriptor()
    assert dict(d.cutout_support) == {"image": "per-product"}
    with pytest.raises(ValueError):
        replace(d, cutout_support={"everything": "soda"})


def test_a_requirement_never_asks_for_previews() -> None:
    with pytest.raises(InvariantViolation, match="previews"):
        EvidenceRequirement("r", "t", frozenset({ProductType.PREVIEW}))


# --- product envelope -------------------------------------------------------------------------


@pytest.mark.req("INT-005")
def test_a_product_keeps_its_raw_artifact_and_the_checksum_of_the_bytes_read() -> None:
    p = ScientificDataProduct(SHA, image_metadata())
    assert p.raw_artifact_id == p.metadata.checksum
    with pytest.raises(InvariantViolation, match="SHA-256"):
        ScientificDataProduct("sha256:" + "cd" * 32, image_metadata())


@pytest.mark.req("INT-006")
def test_the_envelope_states_frame_time_scale_units_calibration_quality_rights_and_relationships() -> None:
    doc = image_metadata().to_document()
    assert doc["target_position"]["frame"] == "ICRS" and doc["time"]["scale"] == "UTC"
    assert doc["units"] == "MJy/sr" and doc["calibration_level"] == 3
    assert doc["uncertainty_present"] and doc["quality_present"]
    assert doc["access_status"] == "public" and doc["rights"]["citation"]
    assert [r["semantics"] for r in doc["relationships"]] == ["#progenitor", "#progenitor"]
    with pytest.raises(InvariantViolation, match="frame"):
        replace(image_metadata(), frame="")
    with pytest.raises(InvariantViolation, match="time scale"):
        replace(image_metadata(), time_scale="")


@pytest.mark.req("INT-007")
def test_a_curated_product_needs_its_raw_parent_and_a_passing_semantic_report() -> None:
    p = ScientificDataProduct(SHA, image_metadata())
    with pytest.raises(CurationBlocked, match="no semantic report"):
        p.curate("sha256:" + "ef" * 32)
    with pytest.raises(CurationBlocked, match="frame"):
        p.validated(report(False)).curate("sha256:" + "ef" * 32)
    curated = p.validated(report(True)).curate("sha256:" + "ef" * 32)
    assert curated.curated_artifact_id and curated.raw_artifact_id == SHA


@pytest.mark.req("INT-008")
def test_a_preview_is_never_curated_as_science() -> None:
    preview = ScientificDataProduct(SHA, image_metadata(product_type=ProductType.PREVIEW))
    rep = replace(report(True), product_id=preview.metadata.product_id)
    with pytest.raises(CurationBlocked, match="never curated as science"):
        preview.validated(rep).curate("sha256:" + "ef" * 32)


# --- plans ------------------------------------------------------------------------------------


@pytest.mark.req("INT-013")
def test_a_plan_is_content_addressed_and_names_every_item_with_size_purpose_and_access() -> None:
    plan = sample_plan()
    assert plan.plan_id == sample_plan().plan_id
    doc = plan.to_document()
    for item in doc["items"]:
        assert item["expected_bytes"] >= 0 and item["requirement_id"] and item["test_kind"]
        assert item["access_status"] == "public"
    assert doc["cost"]["total_bytes"] == sum(i["expected_bytes"] for i in doc["items"])
    approved = plan.decide(actor="kernel")
    assert approved.plan_id == plan.plan_id, "approval does not change which plan it is"


def test_a_plan_item_must_serve_a_derived_requirement() -> None:
    plan = sample_plan()
    stray = replace(plan.items[0], item_id="item-x", requirement_id="req-nobody-asked-for")
    with pytest.raises(InvariantViolation, match="serves no requirement"):
        replace(plan, items=(*plan.items, stray))


def test_restricted_products_are_gaps_not_items() -> None:
    with pytest.raises(InvariantViolation, match="public"):
        replace(sample_plan().items[0], access_status=AccessStatus.EXCLUSIVE_ACCESS)


@pytest.mark.req("INT-014")
def test_a_plan_above_the_threshold_needs_a_signed_ledger_approval() -> None:
    small = sample_plan(image_bytes=1_000)
    assert small.decide(actor="kernel").approval.decision is ApprovalDecision.AUTO_APPROVED  # type: ignore[union-attr]
    big = sample_plan(image_bytes=50_000_000)
    assert big.requires_signed_approval
    with pytest.raises(IntegrationRefused, match="signed ledger approval"):
        big.decide(actor="leigh")
    approved = big.decide(actor="leigh", ledger_entry_id="ledger-42")
    assert approved.approval is not None and approved.approval.decision is ApprovalDecision.APPROVED


@pytest.mark.req("INT-015")
def test_an_acquisition_no_approved_item_names_is_refused() -> None:
    plan = sample_plan()
    item = plan.items[0]
    with pytest.raises(IntegrationRefused, match="not approved"):
        plan.authorise(item.source_id, item.operation, item.target)
    approved = plan.decide(actor="kernel")
    assert approved.authorise(item.source_id, item.operation, item.target) == item
    with pytest.raises(IntegrationRefused, match="INT-015"):
        approved.authorise("synthetic-mast", Operation.DOWNLOAD, "synthetic:JWST/product/uncal.fits")
    refused = plan.decide(actor="leigh", refuse_reason="too large")
    with pytest.raises(IntegrationRefused, match="not approved"):
        refused.authorise(item.source_id, item.operation, item.target)


def test_a_plan_cannot_exceed_its_byte_budget() -> None:
    with pytest.raises(IntegrationRefused, match="byte budget"):
        sample_plan(image_bytes=2_000_000_000, threshold=1_000)


# --- compatibility ----------------------------------------------------------------------------


@pytest.mark.req("INT-016")
def test_a_verdict_needs_a_passing_compatibility_record_for_every_adapter_and_product_type_used() -> None:
    good = compatibility()
    assert good.passed
    require_admitted([good], [("mast-observations", ProductType.IMAGE)])
    with pytest.raises(IntegrationRefused, match="mast-observations for spectrum"):
        require_admitted([good], [("mast-observations", ProductType.SPECTRUM)])
    bad = compatibility(failing=(ConformanceKind.SEMANTIC,))
    assert bad.missing() == (ConformanceKind.SEMANTIC,)
    with pytest.raises(IntegrationRefused, match="INT-016"):
        require_admitted([bad], [("mast-observations", ProductType.IMAGE)])


def test_plan_items_are_value_objects() -> None:
    item = sample_plan().items[1]
    assert isinstance(item, PlanItem) and item.operation is Operation.CONE
    assert astrometry_requirement().requirement_id == item.requirement_id

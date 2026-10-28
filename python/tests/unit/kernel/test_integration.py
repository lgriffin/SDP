"""T1: the v0.4.0 integration domain: capability matching, the product envelope, acquisition plans
and adapter compatibility, each invariant of v0.4.0 §9 with a test named after it."""

from __future__ import annotations

import math
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
    ProductMetadata,
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
    ledger_approval,
    mast_descriptor,
    registry_descriptors,
    sample_plan,
)

pytestmark = pytest.mark.tier("T1")

REGION = SYNTHETIC_FIELD.query_region()


def report(passed: bool = True, metadata: ProductMetadata | None = None) -> SemanticValidationReport:
    meta = metadata or image_metadata()
    return SemanticValidationReport(
        meta.product_id, "fixture-validator", "1.0.0", (SemanticCheck("frame", passed, "ICRS"),), meta.digest()
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


@pytest.mark.req("INT-003")
@pytest.mark.parametrize(("em_min_m", "em_max_m"), [(1e-3, None), (None, 1e-9)], ids=["lower-only", "upper-only"])
def test_a_one_sided_band_is_still_matched(em_min_m: float | None, em_max_m: float | None) -> None:
    infrared = replace(mast_descriptor(), coverage=replace(mast_descriptor().coverage, em_min_m=6e-7, em_max_m=2.8e-5))
    need = replace(image_requirement(), em_min_m=em_min_m, em_max_m=em_max_m)
    m = match_requirement(infrared, REGION, need)
    assert m.status is MatchStatus.UNSATISFIED
    assert any(r.field == "coverage.spectral" and r.outcome is MatchStatus.UNSATISFIED for r in m.reasons)


@pytest.mark.req("INT-003")
def test_a_calibration_level_the_source_does_not_publish_is_partial() -> None:
    unpublished = replace(mast_descriptor(), calibration_levels=frozenset())
    m = match_requirement(unpublished, REGION, image_requirement())
    assert m.status is MatchStatus.PARTIAL
    raw_only = replace(mast_descriptor(), calibration_levels=frozenset({1}))
    assert match_requirement(raw_only, REGION, image_requirement()).status is MatchStatus.UNSATISFIED


@pytest.mark.req("INT-004")
def test_a_cutout_needs_cutout_support_for_the_product_type() -> None:
    need = replace(image_requirement(), operations=frozenset({Operation.CUTOUT}))
    cutting = replace(mast_descriptor(), operations=mast_descriptor().operations | {Operation.CUTOUT})
    assert match_requirement(cutting, REGION, need).status is MatchStatus.SATISFIED
    none = replace(cutting, cutout_support={"image": "none"})
    m = match_requirement(none, REGION, need)
    assert m.status is MatchStatus.UNSATISFIED
    assert any(r.field == "cutout_support" and r.outcome is MatchStatus.UNSATISFIED for r in m.reasons)
    unknown = replace(cutting, cutout_support={})
    assert match_requirement(unknown, REGION, need).status is MatchStatus.PARTIAL


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
    with pytest.raises(CurationBlocked, match="never curated as science"):
        preview.validated(report(True, preview.metadata)).curate("sha256:" + "ef" * 32)


@pytest.mark.req("INT-007")
def test_a_report_validates_only_the_product_and_envelope_it_checked() -> None:
    p = ScientificDataProduct(SHA, image_metadata())
    other = replace(image_metadata(), product_id="synthetic:JWST/product/other_i2d.fits")
    with pytest.raises(InvariantViolation, match="product it names"):
        ScientificDataProduct(SHA, image_metadata(), report(True, other), "sha256:" + "ef" * 32)
    curated = p.validated(report(True)).curate("sha256:" + "ef" * 32)
    with pytest.raises(InvariantViolation, match="changed one"):
        replace(curated, metadata=replace(image_metadata(), has_wcs=False))


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
    approved = big.decide(actor="leigh", ledger_entry=ledger_approval(big))
    assert approved.approval is not None and approved.approval.decision is ApprovalDecision.APPROVED
    assert approved.approval.ledger_entry_id == "ledger-42"


@pytest.mark.req("INT-014")
def test_a_ledger_entry_approves_only_the_plan_it_names_signed_by_the_actor() -> None:
    big = sample_plan(image_bytes=50_000_000)
    other = sample_plan(image_bytes=60_000_000)
    with pytest.raises(IntegrationRefused, match="does not approve"):
        big.decide(actor="leigh", ledger_entry=ledger_approval(other))
    with pytest.raises(IntegrationRefused, match="does not approve"):
        big.decide(actor="leigh", ledger_entry=ledger_approval(big, kind="predeclaration"))
    with pytest.raises(IntegrationRefused, match="another actor"):
        big.decide(actor="leigh", ledger_entry=ledger_approval(big, signer="mallory"))


@pytest.mark.req("INT-014")
def test_an_automatic_approval_cannot_be_attached_to_a_plan_above_the_threshold() -> None:
    small = sample_plan(image_bytes=1_000).decide(actor="kernel")
    big = sample_plan(image_bytes=50_000_000)
    assert small.approval is not None
    auto = replace(small.approval, plan_id=big.plan_id)
    with pytest.raises(IntegrationRefused, match="signed ledger entry"):
        replace(big, approval=auto)
    lowered = replace(auto, threshold_bytes=100_000_000)
    with pytest.raises(IntegrationRefused, match="approval threshold"):
        replace(big, approval=lowered)


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


@pytest.mark.parametrize(
    ("cost", "match"),
    [
        ({"requests_per_source": {"synthetic-mast": 10_000}}, "requests per source"),
        ({"estimated_wall_seconds": 1e9}, "wall-time"),
        ({"estimated_wall_seconds": math.inf}, "wall-time"),
        ({"nas_headroom_after_bytes": 10}, "NAS headroom"),
    ],
    ids=["requests", "wall-time", "infinite-wall-time", "headroom"],
)
def test_a_plan_cannot_exceed_any_budget_limit(cost: dict[str, object], match: str) -> None:
    plan = sample_plan()
    strict = replace(plan.budget, min_nas_headroom_bytes=1_000)
    with pytest.raises(IntegrationRefused, match=match):
        replace(plan, budget=strict, cost=replace(plan.cost, **cost))  # type: ignore[arg-type]


def test_unknown_nas_headroom_is_left_to_the_acquisition_check() -> None:
    plan = sample_plan()
    assert replace(plan, cost=replace(plan.cost, nas_headroom_after_bytes=None)).cost.nas_headroom_after_bytes is None


def test_a_plan_item_meets_its_requirement_calibration_level() -> None:
    plan = sample_plan()
    low = replace(plan.items[0], calibration_level=1)
    with pytest.raises(IntegrationRefused, match="calibration level 1"):
        replace(plan, items=(low, *plan.items[1:]))


@pytest.mark.req("INT-015")
def test_changing_what_a_plan_hashes_after_approval_cannot_keep_the_approval() -> None:
    field = dict(sample_plan().field)
    approved = replace(sample_plan(), field=field).decide(actor="kernel")
    plan_id = approved.plan_id
    field["changed"] = True
    assert approved.plan_id == plan_id, "the plan keeps its own copy of what it hashes"
    item = approved.items[0]
    with pytest.raises(InvariantViolation, match="names"):
        replace(approved, constraints={"extra": 1}).authorise(item.source_id, item.operation, item.target)


# --- compatibility ----------------------------------------------------------------------------


@pytest.mark.req("INT-016")
def test_a_verdict_needs_a_passing_compatibility_record_for_every_adapter_and_product_type_used() -> None:
    good = compatibility()
    assert good.passed
    require_admitted([good], [("mast-observations", "1.0.0", ProductType.IMAGE)])
    with pytest.raises(IntegrationRefused, match=r"mast-observations 1\.0\.0 for spectrum"):
        require_admitted([good], [("mast-observations", "1.0.0", ProductType.SPECTRUM)])
    with pytest.raises(IntegrationRefused, match=r"mast-observations 1\.1\.0 for image"):
        require_admitted([good], [("mast-observations", "1.1.0", ProductType.IMAGE)])
    bad = compatibility(failing=(ConformanceKind.SEMANTIC,))
    assert bad.missing() == (ConformanceKind.SEMANTIC,)
    with pytest.raises(IntegrationRefused, match="INT-016"):
        require_admitted([bad], [("mast-observations", "1.0.0", ProductType.IMAGE)])


def test_plan_items_are_value_objects() -> None:
    item = sample_plan().items[1]
    assert isinstance(item, PlanItem) and item.operation is Operation.CONE
    assert astrometry_requirement().requirement_id == item.requirement_id

"""T1: the integration records in full: every field reason a capability match gives, the descriptor,
requirement, reference and compatibility documents, the T5s comparison and the refusals each
record gives (INT-001 to INT-008, INT-016).

The behaviour is in test_integration.py; these pin the details mutation testing showed nothing
else checked (docs/testing.md, "Killing a survivor")."""

from __future__ import annotations

import math
import re
from dataclasses import replace
from datetime import date
from typing import Any

import pytest

from sdp.kernel.domain.archive import Box, Cone
from sdp.kernel.domain.errors import CurationBlocked, InvariantViolation
from sdp.kernel.domain.integration import (
    AccessStatus,
    AdapterCompatibilityRecord,
    AdapterInfo,
    CapabilityMatch,
    ConformanceKind,
    ConformanceResult,
    Coverage,
    CutoutMethod,
    CutoutSupport,
    EvidenceRequirement,
    IntegrationRefused,
    MatchStatus,
    Operation,
    PayloadExtension,
    ProductRelationship,
    ProductType,
    ReferenceRecord,
    ReferenceStatus,
    RelationshipSemantics,
    Rights,
    ScientificDataProduct,
    SemanticCheck,
    SemanticValidationReport,
    ServiceLimits,
    SourceCapabilityDescriptor,
    compare_to_reference,
    cutout_support,
    match_requirement,
    order_matches,
    require_admitted,
)
from sdp.testing.integration import (
    SHA,
    SYNTHETIC_FIELD,
    VERIFIED,
    compatibility,
    gaia_descriptor,
    image_metadata,
    image_requirement,
    mast_descriptor,
)

pytestmark = pytest.mark.tier("T1")

REGION = SYNTHETIC_FIELD.query_region()
S, P, U = "satisfied", "partial", "unsatisfied"
NOT_ADMITTED = "no adapter admitted for image, spectrum yet: exploratory evidence only, no verdict until one is"


def exactly(error: type[Exception], message: str) -> Any:
    return pytest.raises(error, match="^" + re.escape(message) + "$")


def rows(m: CapabilityMatch) -> list[tuple[str, str, str]]:
    return [(r.field, r.outcome.value, r.reason) for r in m.reasons]


def bare(**changes: Any) -> SourceCapabilityDescriptor:
    """A hand-written descriptor that publishes nothing but two product types."""
    d = SourceCapabilityDescriptor(
        "bare",
        "Bare",
        "SDP",
        VERIFIED,
        "hand-written",
        product_types=frozenset({ProductType.IMAGE, ProductType.SPECTRUM}),
    )
    return replace(d, **changes)


def demanding() -> EvidenceRequirement:
    """A requirement that exercises every field the matcher reads."""
    return EvidenceRequirement(
        "req-x",
        "t",
        frozenset({ProductType.IMAGE, ProductType.SPECTRUM}),
        min_calibration_level=2,
        missions=("JWST", "HST"),
        instruments=("NIRCAM", "MIRI"),
        epoch_min_mjd=50000.0,
        em_max_m=3e-6,
        operations=frozenset({Operation.CUTOUT}),
    )


# --- matching ---------------------------------------------------------------------------------


@pytest.mark.req("INT-003")
def test_a_match_gives_its_reason_for_every_field_it_checks() -> None:
    m = match_requirement(mast_descriptor(), REGION, image_requirement())
    assert (m.source_id, m.requirement_id, m.status, m.descriptor_verified_on) == (
        "synthetic-mast",
        "req-jwst-image",
        MatchStatus.SATISFIED,
        VERIFIED,
    )
    assert rows(m) == [
        ("product_types", S, "offers image"),
        ("missions", S, "serves JWST"),
        ("coverage.spatial", S, "field centre is covered"),
        ("calibration_level", S, "serves calibration level 3"),
        ("operations", S, "supports every operation needed"),
        ("access", S, "public access"),
        ("adapter.conformance", S, "adapter admitted for image"),
    ]
    assert rows(match_requirement(gaia_descriptor(), REGION, image_requirement())) == [
        ("product_types", U, "needs one of image; offers catalogue"),
        ("missions", U, "does not serve JWST"),
        ("coverage.spatial", S, "field centre is covered"),
        ("calibration_level", P, "calibration levels not published"),
        ("operations", S, "supports every operation needed"),
        ("access", S, "public access"),
        ("adapter.conformance", U, "no product type in common"),
    ]


@pytest.mark.req("INT-003")
def test_a_source_that_publishes_nothing_is_unknown_on_every_field_it_does_not_publish() -> None:
    m = match_requirement(bare(), REGION, demanding())
    assert m.status is MatchStatus.UNSATISFIED
    assert rows(m) == [
        ("product_types", S, "offers image, spectrum"),
        ("missions", P, "missions not published"),
        ("instruments", P, "instruments not published"),
        ("coverage.spatial", P, "spatial coverage unknown"),
        ("coverage.temporal", P, "time coverage unknown"),
        ("coverage.spectral", P, "spectral coverage unknown"),
        ("calibration_level", P, "calibration levels not published"),
        ("operations", U, "lacks cutout, download"),
        ("cutout_support", P, "cutout support not published for image, spectrum"),
        ("access", S, "public access"),
        ("adapter.conformance", P, NOT_ADMITTED),
    ]
    assert rows(match_requirement(bare(product_types=frozenset()), REGION, demanding()))[0] == (
        "product_types",
        U,
        "needs one of image, spectrum; offers none",
    )


@pytest.mark.req("INT-003")
def test_a_source_that_publishes_the_wrong_values_fails_each_field_saying_why() -> None:
    wrong = bare(
        missions=("TESS",),
        instruments=("WFC3",),
        calibration_levels=frozenset({0, 1}),
        coverage=Coverage(False, (Cone(10, 10, 1),), 40000.0, 45000.0, 5e-6, 1e-5),
        cutout_support={"image": "none", "spectrum": "none"},
        access=AccessStatus.LOGIN,
        operations=frozenset({Operation.CONE}),
    )
    assert rows(match_requirement(wrong, REGION, demanding())) == [
        ("product_types", S, "offers image, spectrum"),
        ("missions", U, "does not serve JWST, HST"),
        ("instruments", U, "does not serve NIRCAM, MIRI"),
        ("coverage.spatial", U, "field centre is outside the published coverage"),
        ("coverage.temporal", U, "epoch range outside published time coverage"),
        ("coverage.spectral", U, "band outside published spectral coverage"),
        ("calibration_level", U, "needs calibration level 2; serves at most 1"),
        ("operations", U, "lacks cutout, download"),
        ("cutout_support", U, "no cutouts for image, spectrum"),
        ("access", U, "access is login; the plan never retries with credentials"),
        ("adapter.conformance", P, NOT_ADMITTED),
    ]


def admitted_source(**changes: Any) -> SourceCapabilityDescriptor:
    full = bare(
        missions=("HST", "JWST", "TESS"),
        instruments=("MIRI", "WFC3", "NIRCAM"),
        calibration_levels=frozenset({3, 1, 2}),
        coverage=Coverage(False, (Cone(10, 10, 1), Cone(53.16, -27.78, 1)), 49000.0, 51000.0, 1e-6, 1e-5),
        cutout_support={"image": "soda", "spectrum": "none"},
        operations=frozenset({Operation.CUTOUT, Operation.DOWNLOAD}),
        adapter=AdapterInfo("a", "1.0.0", None, {"spectrum": True, "image": True}),
    )
    return replace(full, **changes)


@pytest.mark.req("INT-003")
def test_a_source_that_serves_everything_satisfies_each_field_for_a_box_field() -> None:
    box = Box(53.0, 53.3, -27.9, -27.6)
    m = match_requirement(admitted_source(), box, demanding())
    assert m.status is MatchStatus.SATISFIED
    assert rows(m) == [
        ("product_types", S, "offers image, spectrum"),
        ("missions", S, "serves HST, JWST"),
        ("instruments", S, "serves MIRI, NIRCAM"),
        ("coverage.spatial", S, "field centre is covered"),
        ("coverage.temporal", S, "epoch range overlaps"),
        ("coverage.spectral", S, "band overlaps"),
        ("calibration_level", S, "serves calibration level 3"),
        ("operations", S, "supports every operation needed"),
        ("cutout_support", S, "cutouts for image"),
        ("access", S, "public access"),
        ("adapter.conformance", S, "adapter admitted for image, spectrum"),
    ]
    elsewhere = Box(9.5, 10.5, 9.5, 10.5)
    assert rows(match_requirement(admitted_source(), elsewhere, demanding()))[3] == (
        "coverage.spatial",
        S,
        "field centre is covered",
    )
    far = Box(100.0, 101.0, 0.0, 1.0)
    assert rows(match_requirement(admitted_source(), far, demanding()))[3][1] == U


@pytest.mark.req("INT-003")
def test_half_published_coverage_and_cutouts_are_partial() -> None:
    half = admitted_source(cutout_support={"image": "none"}, coverage=Coverage(True, (), 40000.0, None, None, 1e-5))
    m = match_requirement(half, REGION, demanding())
    assert m.status is MatchStatus.PARTIAL
    assert rows(m)[4:6] == [
        ("coverage.temporal", P, "time coverage unknown"),
        ("coverage.spectral", P, "spectral coverage unknown"),
    ]
    assert rows(m)[8] == ("cutout_support", P, "cutout support not published for spectrum")


@pytest.mark.req("INT-003")
@pytest.mark.parametrize(
    ("need", "field"),
    [
        ({"epoch_max_mjd": 49500.0}, "coverage.temporal"),
        ({"epoch_min_mjd": 50500.0}, "coverage.temporal"),
        ({"epoch_min_mjd": 51000.0, "epoch_max_mjd": 52000.0}, "coverage.temporal"),
        ({"em_min_m": 5e-6}, "coverage.spectral"),
        ({"em_max_m": 2e-6}, "coverage.spectral"),
        ({"em_min_m": 1e-5, "em_max_m": 2e-5}, "coverage.spectral"),
    ],
    ids=["epoch-upper", "epoch-lower", "epoch-touching", "band-lower", "band-upper", "band-touching"],
)
def test_a_one_sided_or_touching_range_overlaps(need: dict[str, Any], field: str) -> None:
    req = EvidenceRequirement("req-r", "t", frozenset({ProductType.IMAGE}), needs_bytes=False, **need)
    reasons = {r.field: r.outcome for r in match_requirement(admitted_source(), REGION, req).reasons}
    assert reasons[field] is MatchStatus.SATISFIED


def test_a_metadata_only_requirement_needs_no_download_and_may_be_restricted() -> None:
    need = EvidenceRequirement(
        "req-index", "index", frozenset({ProductType.IMAGE}), needs_bytes=False, public_only=False
    )
    m = match_requirement(bare(access=AccessStatus.LOGIN), REGION, need)
    assert [r for r in rows(m) if r[0] in ("operations", "access", "cutout_support")] == [
        ("operations", S, "supports every operation needed")
    ]


def test_matches_rank_satisfied_partial_unsatisfied_within_each_requirement() -> None:
    sat = match_requirement(admitted_source(), REGION, demanding())
    part = match_requirement(
        replace(admitted_source(), source_id="a-part", calibration_levels=frozenset()), REGION, demanding()
    )
    unsat = match_requirement(
        replace(admitted_source(), source_id="a-unsat", access=AccessStatus.LOGIN), REGION, demanding()
    )
    other = match_requirement(mast_descriptor(), REGION, image_requirement())
    assert order_matches([unsat, other, part, sat]) == (other, sat, part, unsat)
    with exactly(InvariantViolation, "a capability match records at least one field reason"):
        replace(sat, reasons=())


@pytest.mark.req("INT-003")
def test_a_match_document_round_trips() -> None:
    m = match_requirement(gaia_descriptor(), REGION, image_requirement())
    doc = m.to_document()
    assert doc["descriptor_verified_on"] == "2026-10-28" and doc["status"] == "unsatisfied"
    assert doc["reasons"][0] == {
        "field": "product_types",
        "outcome": "unsatisfied",
        "reason": "needs one of image; offers catalogue",
    }
    assert CapabilityMatch.from_document(doc) == m


# --- descriptors and requirements ---------------------------------------------------------------


def full_descriptor() -> SourceCapabilityDescriptor:
    return replace(
        mast_descriptor(),
        coverage=Coverage(False, (Cone(10.0, 20.0, 1.5),), 50000.0, 60000.0, 1e-7, 3e-5),
        access=AccessStatus.EXCLUSIVE_ACCESS,
        limits=ServiceLimits(1000, 100, 2.5, 4, True, 1e6),
    )


@pytest.mark.req("INT-001")
def test_a_descriptor_document_round_trips_every_field() -> None:
    d = full_descriptor()
    doc = d.to_document()
    assert doc["schema_version"] == "1.0.0" and doc["verified_on"] == "2026-10-28"
    assert doc["adapter"] == {
        "name": "mast-observations",
        "version": "1.0.0",
        "last_compatibility_check": "2026-10-28",
        "conformance": {"image": True},
    }
    assert doc["calibration_levels"] == [1, 2, 3] and doc["product_types"] == [
        "cube",
        "image",
        "spectrum",
        "timeseries",
    ]
    assert SourceCapabilityDescriptor.from_document(doc) == d
    unchecked = replace(d, adapter=AdapterInfo("x", "1.0.0"))
    assert SourceCapabilityDescriptor.from_document(unchecked.to_document()) == unchecked


def test_a_minimal_descriptor_document_takes_the_stated_defaults() -> None:
    doc = {"source_id": "s", "title": "t", "publisher": "p", "origin": "hand-written", "verified_on": "2026-01-02"}
    d = SourceCapabilityDescriptor.from_document(doc)
    assert d == SourceCapabilityDescriptor("s", "t", "p", date(2026, 1, 2), "hand-written")
    assert d.rights == Rights("unknown", "unknown") and d.adapter is None and d.access is AccessStatus.PUBLIC
    assert d.to_document() == {
        "schema_version": "1.0.0",
        "source_id": "s",
        "ivoid": None,
        "title": "t",
        "publisher": "p",
        "origin": "hand-written",
        "verified_on": "2026-01-02",
        "protocols": [],
        "data_models": [],
        "product_types": [],
        "missions": [],
        "instruments": [],
        "calibration_levels": [],
        "coverage": {
            "all_sky": None,
            "regions": [],
            "t_min_mjd": None,
            "t_max_mjd": None,
            "em_min_m": None,
            "em_max_m": None,
        },
        "operations": [],
        "cutout_support": {},
        "access": "public",
        "limits": {
            "max_rows": None,
            "page_size": None,
            "requests_per_second": None,
            "max_concurrent": None,
            "upload": False,
            "throughput_bytes_per_s": None,
        },
        "rights": {"licence": "unknown", "citation": "unknown", "acknowledgement": ""},
        "adapter": None,
    }
    assert not d.claims(ProductType.IMAGE) and mast_descriptor().claims(ProductType.IMAGE)


def test_a_descriptor_refuses_what_it_cannot_be() -> None:
    d = mast_descriptor()
    with exactly(InvariantViolation, "a descriptor names its source"):
        replace(d, source_id="")
    with exactly(InvariantViolation, "descriptor origin must be registry or hand-written, not 'guessed'"):
        replace(d, origin="guessed")
    with exactly(InvariantViolation, "a descriptor read from the registry carries its ivoid"):
        replace(d, ivoid="")
    with pytest.raises(ValueError, match="'thumbnails'"):
        replace(d, cutout_support={"thumbnails": "soda"})
    for level in (-1, 5):
        with exactly(InvariantViolation, "calibration level is ObsCore 0 to 4"):
            replace(d, calibration_levels=frozenset({level}))
    assert replace(d, calibration_levels=frozenset({0, 4})).calibration_levels == {0, 4}
    with exactly(InvariantViolation, "not a semantic version: '1.0'"):
        AdapterInfo("x", "1.0")


@pytest.mark.req("INT-002")
def test_stripping_claims_keeps_previews_and_needs_an_adapter_for_science() -> None:
    preview = replace(
        mast_descriptor(), product_types=frozenset({ProductType.PREVIEW, ProductType.IMAGE}), adapter=None
    )
    stripped, removed = preview.strip_unproven_claims()
    assert removed == (ProductType.IMAGE,) and stripped.product_types == {ProductType.PREVIEW}
    refused = replace(mast_descriptor(), adapter=AdapterInfo("m", "1.0.0", None, {"image": False}))
    assert refused.strip_unproven_claims()[1] == (
        ProductType.CUBE,
        ProductType.IMAGE,
        ProductType.SPECTRUM,
        ProductType.TIMESERIES,
    )


@pytest.mark.req("INT-003")
def test_a_requirement_document_round_trips_and_defaults() -> None:
    need = replace(demanding(), epoch_max_mjd=60000.0, em_min_m=1e-7, needs_bytes=False, public_only=False)
    doc = need.to_document()
    assert doc == {
        "requirement_id": "req-x",
        "test_kind": "t",
        "product_types": ["image", "spectrum"],
        "min_calibration_level": 2,
        "missions": ["JWST", "HST"],
        "instruments": ["NIRCAM", "MIRI"],
        "epoch_min_mjd": 50000.0,
        "epoch_max_mjd": 60000.0,
        "em_min_m": 1e-7,
        "em_max_m": 3e-6,
        "operations": ["cutout"],
        "needs_bytes": False,
        "public_only": False,
    }
    assert EvidenceRequirement.from_document(doc) == need
    minimal = {"requirement_id": "r", "test_kind": "k", "product_types": ["image"]}
    assert EvidenceRequirement.from_document(minimal) == EvidenceRequirement("r", "k", frozenset({ProductType.IMAGE}))


def test_a_requirement_refuses_what_it_cannot_be() -> None:
    image = frozenset({ProductType.IMAGE})
    for rid, kind in (("", "k"), ("r", "")):
        with exactly(InvariantViolation, "an evidence requirement names itself and the test that needs it"):
            EvidenceRequirement(rid, kind, image)
    with exactly(InvariantViolation, "an evidence requirement names at least one product type"):
        EvidenceRequirement("r", "k", frozenset())
    with exactly(InvariantViolation, "a test cannot require previews, thumbnails or auxiliaries as evidence"):
        EvidenceRequirement("r", "k", frozenset({ProductType.IMAGE, ProductType.PREVIEW}))
    for level in (-1, 5):
        with exactly(InvariantViolation, "calibration level is ObsCore 0 to 4"):
            EvidenceRequirement("r", "k", image, min_calibration_level=level)
    assert EvidenceRequirement("r", "k", image, min_calibration_level=0).min_calibration_level == 0
    assert EvidenceRequirement("r", "k", image, min_calibration_level=4).min_calibration_level == 4
    with exactly(InvariantViolation, "epoch range lower bound exceeds upper bound"):
        EvidenceRequirement("r", "k", image, epoch_min_mjd=2.0, epoch_max_mjd=1.0)
    with exactly(InvariantViolation, "spectral range bounds must be finite or unknown"):
        EvidenceRequirement("r", "k", image, em_max_m=math.inf)


def test_coverage_bounds_are_finite_ordered_and_may_touch() -> None:
    assert Coverage(t_min_mjd=1.0, t_max_mjd=1.0, em_min_m=2.0, em_max_m=2.0).t_max_mjd == 1.0
    with exactly(InvariantViolation, "time coverage lower bound exceeds upper bound"):
        Coverage(t_min_mjd=2.0, t_max_mjd=1.0)
    with exactly(InvariantViolation, "time coverage bounds must be finite or unknown"):
        Coverage(t_max_mjd=math.nan)
    with exactly(InvariantViolation, "spectral coverage lower bound exceeds upper bound"):
        Coverage(em_min_m=2.0, em_max_m=1.0)
    with exactly(InvariantViolation, "spectral coverage bounds must be finite or unknown"):
        Coverage(em_min_m=-math.inf)
    assert Coverage(all_sky=False).covers_position(0.0, 0.0) is False
    assert Coverage().covers_position(0.0, 0.0) is None


# --- cutouts and products -----------------------------------------------------------------------


@pytest.mark.req("INT-004")
def test_cutout_support_is_read_from_the_datalink_row_the_media_type_and_the_range_probe() -> None:
    soda = {"semantics": "#cutout", "service_def": "soda-1", "access_url": "https://example.invalid/soda"}
    assert cutout_support([soda], "image/png", None, "u") == CutoutSupport(
        CutoutMethod.SODA, "DataLink #cutout row, service soda-1", "https://example.invalid/soda"
    )
    for half in ({**soda, "service_def": ""}, {**soda, "access_url": None}, {**soda, "semantics": "#this"}):
        assert cutout_support([half], "image/png", None, "u").method is CutoutMethod.NONE
    assert cutout_support([], "image/png", 206, "u") == CutoutSupport(
        CutoutMethod.NONE, "image/png is not FITS and DataLink names no cutout service"
    )
    assert cutout_support([], " Application/FITS ; charset=x", 206, "u") == CutoutSupport(
        CutoutMethod.RANGE, "HTTP 206 to a byte-range request for u"
    )
    assert cutout_support([], "image/fits", None, "u").evidence == (
        "no DataLink cutout service; a byte-range request was not probed"
    )
    assert cutout_support([], "image/fits", 200, "u").evidence == (
        "no DataLink cutout service; a byte-range request was HTTP 200"
    )


@pytest.mark.req("INT-004")
def test_cutout_support_records_its_evidence_and_a_soda_service() -> None:
    with exactly(InvariantViolation, "cutout support records the evidence it was read from (INT-004)"):
        CutoutSupport(CutoutMethod.RANGE, "")
    with exactly(InvariantViolation, "SODA cutout support names its service"):
        CutoutSupport(CutoutMethod.SODA, "row")
    soda = CutoutSupport(CutoutMethod.SODA, "row", "https://s")
    assert soda.supported and not CutoutSupport(CutoutMethod.NONE, "why").supported
    assert soda.to_document() == {"method": "soda", "service_url": "https://s", "evidence": "row"}


@pytest.mark.req("INT-006")
def test_the_envelope_document_records_access_end_relationships_and_any_cutout() -> None:
    meta = replace(
        image_metadata(),
        exclusive_access_ends=date(2027, 1, 1),
        relationships=(ProductRelationship(RelationshipSemantics.PROGENITOR, "p", "input"),),
        calibration_references=(("R_FLAT", "flat.fits"),),
    )
    doc = meta.to_document()
    assert doc["exclusive_access_ends"] == "2027-01-01" and "cutout" not in doc
    assert doc["relationships"] == [{"semantics": "#progenitor", "product_id": "p", "description": "input"}]
    assert doc["calibration_references"] == [{"keyword": "R_FLAT", "file": "flat.fits"}]
    cut = replace(meta, cutout=CutoutSupport(CutoutMethod.RANGE, "HTTP 206"))
    assert cut.to_document()["cutout"] == {"method": "range", "service_url": None, "evidence": "HTTP 206"}
    assert cut.digest() != meta.digest()


def report_for(meta: Any, *checks: SemanticCheck) -> SemanticValidationReport:
    return SemanticValidationReport(
        meta.product_id, "v", "1.0.0", checks or (SemanticCheck("frame", True),), meta.digest()
    )


@pytest.mark.req("INT-007")
def test_a_product_refuses_a_missing_parent_a_wrong_checksum_and_a_foreign_report() -> None:
    meta = image_metadata()
    with exactly(InvariantViolation, "a product keeps its raw artifact (INT-005)"):
        ScientificDataProduct("", meta)
    with exactly(InvariantViolation, "the envelope checksum is the SHA-256 of the raw bytes actually read"):
        ScientificDataProduct("sha256:" + "0" * 64, meta)
    with exactly(InvariantViolation, "a semantic report validates the product it names"):
        ScientificDataProduct(SHA, meta, replace(report_for(meta), product_id="other"))
    with exactly(InvariantViolation, "a semantic report validates the envelope it checked, not a changed one"):
        ScientificDataProduct(SHA, meta, replace(report_for(meta), envelope_digest="sha256:" + "1" * 64))
    with exactly(InvariantViolation, "a semantic report names the envelope it checked"):
        replace(report_for(meta), envelope_digest="")
    raw = ScientificDataProduct(SHA, meta)
    assert raw.to_document()["validation"] is None and raw.to_document()["schema_version"] == "1.0.0"


@pytest.mark.req("INT-007")
def test_curation_names_every_failed_check() -> None:
    meta = image_metadata()
    failing = report_for(meta, SemanticCheck("frame", False), SemanticCheck("units", False), SemanticCheck("wcs", True))
    with exactly(CurationBlocked, "curation blocked by semantic validation: frame, units (INT-007)"):
        ScientificDataProduct(SHA, meta).validated(failing).curate("sha256:" + "c" * 64)
    with exactly(CurationBlocked, "curation blocked by semantic validation: no semantic report (INT-007)"):
        ScientificDataProduct(SHA, meta).curate("sha256:" + "c" * 64)
    assert failing.failures() == (SemanticCheck("frame", False), SemanticCheck("units", False))
    assert not replace(failing, checks=()).passed
    curated = ScientificDataProduct(SHA, meta).validated(report_for(meta)).curate("sha256:" + "c" * 64)
    assert curated.to_document()["validation"] == report_for(meta).to_document()


# --- reference records and the T5s comparison -------------------------------------------------


def reference(**changes: Any) -> ReferenceRecord:
    base = ReferenceRecord(
        product_id="synthetic:JWST/product/jw00000-o001_t001_nircam_clear-f200w_i2d.fits",
        product_type=ProductType.IMAGE,
        mission="jwst",
        instrument=" NIRCam ",
        frame="ICRS",
        time_scale="UTC",
        units="MJy/sr",
        calibration_level=3,
        extensions={"SCI": "data", "ERR": "error", "DQ": "quality"},
        has_wcs=True,
        documentation="https://jwst-docs.example.invalid/i2d",
        status=ReferenceStatus.VERIFIED,
        detector="NRCA1",
        filter_or_grating="F200W",
        time_start_mjd=60000.0,
        time_end_mjd=60000.1,
        progenitors=("synthetic:JWST/product/b_cal.fits", "synthetic:JWST/product/a_cal.fits"),
        verified_by=("leigh",),
    )
    return replace(base, **changes)


@pytest.mark.req("INT-006")
def test_an_envelope_that_matches_its_reference_passes_every_field_check_case_and_space_blind() -> None:
    checks = compare_to_reference(image_metadata(), reference())
    assert [c.field for c in checks] == [
        "product_id",
        "product_type",
        "mission",
        "instrument",
        "detector",
        "filter_or_grating",
        "frame",
        "time_scale",
        "units",
        "calibration_level",
        "has_wcs",
        "time_start_mjd",
        "time_end_mjd",
        "extensions.DQ",
        "extensions.ERR",
        "extensions.SCI",
        "relationships.progenitors",
    ]
    assert all(c.passed for c in checks)
    assert checks[2].detail == "got 'JWST', reference 'jwst'"
    assert checks[11].detail == "got 60000.0, reference 60000.0"
    assert checks[13].detail == "got 'quality', reference 'quality'"
    assert checks[16].detail == (
        "got ['synthetic:JWST/product/a_cal.fits', 'synthetic:JWST/product/b_cal.fits'], "
        "reference ['synthetic:JWST/product/a_cal.fits', 'synthetic:JWST/product/b_cal.fits']"
    )


@pytest.mark.req("INT-006")
def test_each_difference_from_the_reference_fails_with_the_field_named() -> None:
    meta = replace(
        image_metadata(),
        instrument=None,
        time_start_mjd=60000.0 + 2e-6,
        time_end_mjd=None,
        extensions=(
            PayloadExtension("SCI", "data"),
            PayloadExtension("VAR", "variance"),
            PayloadExtension("X", "other"),
        ),
        relationships=(),
    )
    failed = {c.field: c.detail for c in compare_to_reference(meta, reference()) if not c.passed}
    assert failed == {
        "instrument": "got None, reference ' NIRCam '",
        "time_start_mjd": "got 60000.000002, reference 60000.0",
        "time_end_mjd": "got None, reference 60000.1",
        "extensions.DQ": "got None, reference 'quality'",
        "extensions.ERR": "got None, reference 'error'",
        "extensions.VAR": "not in the reference record",
        "relationships.progenitors": (
            "got [], reference ['synthetic:JWST/product/a_cal.fits', 'synthetic:JWST/product/b_cal.fits']"
        ),
    }
    within = replace(image_metadata(), time_start_mjd=60000.0 + 5e-7)
    assert all(c.passed for c in compare_to_reference(within, reference()))
    unbounded = reference(time_start_mjd=None, time_end_mjd=None, progenitors=(), instrument=None, detector=None)
    fields = [
        c.field for c in compare_to_reference(replace(image_metadata(), instrument=None, detector=None), unbounded)
    ]
    assert "time_start_mjd" not in fields and "relationships.progenitors" not in fields
    assert all(
        c.passed for c in compare_to_reference(replace(image_metadata(), instrument=None, detector=None), unbounded)
    )


def test_a_reference_document_reads_every_field_and_defaults_the_rest() -> None:
    ref = reference()
    doc = {
        "product_id": ref.product_id,
        "product_type": "image",
        "mission": "jwst",
        "instrument": " NIRCam ",
        "detector": "NRCA1",
        "filter_or_grating": "F200W",
        "frame": "ICRS",
        "time_scale": "UTC",
        "time_start_mjd": 60000.0,
        "time_end_mjd": 60000.1,
        "units": "MJy/sr",
        "calibration_level": 3,
        "extensions": {"SCI": "data", "ERR": "error", "DQ": "quality"},
        "has_wcs": 1,
        "progenitors": list(ref.progenitors),
        "documentation": ref.documentation,
        "status": "verified",
        "verified_by": ["leigh"],
    }
    assert ReferenceRecord.from_document(doc) == ref and ref.admits
    minimal = {
        "product_id": "p",
        "product_type": "image",
        "mission": "m",
        "frame": "ICRS",
        "time_scale": "TT",
        "has_wcs": False,
        "documentation": "doc",
        "status": "synthetic",
    }
    assert ReferenceRecord.from_document(minimal) == ReferenceRecord(
        "p", ProductType.IMAGE, "m", None, "ICRS", "TT", None, None, {}, False, "doc", ReferenceStatus.SYNTHETIC
    )
    assert not ReferenceRecord.from_document(minimal).admits
    with exactly(InvariantViolation, "a verified reference record names who verified it"):
        reference(verified_by=(" ",))
    with exactly(InvariantViolation, "a reference record cites the documentation it was read from"):
        reference(documentation="")


# --- adapter compatibility ----------------------------------------------------------------------


@pytest.mark.req("INT-016")
def test_a_compatibility_document_round_trips_and_names_what_is_missing() -> None:
    rec = compatibility(failing=(ConformanceKind.SEMANTIC,))
    doc = rec.to_document()
    assert doc == {
        "schema_version": "1.0.0",
        "adapter": "mast-observations",
        "adapter_version": "1.0.0",
        "product_type": "image",
        "upstream_versions": {"astropy": "7.0.0"},
        "results": [
            {"kind": k.value, "passed": k is not ConformanceKind.SEMANTIC, "ci_run_id": "ci-run-0"}
            for k in ConformanceKind
        ],
        "passed": False,
        "live_smoke_on": "2026-10-28",
        "fixture_digests": [SHA],
    }
    assert AdapterCompatibilityRecord.from_document(doc) == rec
    assert rec.missing() == (ConformanceKind.SEMANTIC,)
    minimal = {"adapter": "a", "adapter_version": "2.0.0", "product_type": "cube"}
    bare_rec = AdapterCompatibilityRecord.from_document(minimal)
    assert bare_rec == AdapterCompatibilityRecord("a", "2.0.0", ProductType.CUBE, ())
    assert bare_rec.missing() == tuple(ConformanceKind) and not bare_rec.passed
    assert bare_rec.to_document()["live_smoke_on"] is None
    with exactly(InvariantViolation, "one result per conformance kind"):
        AdapterCompatibilityRecord(
            "a", "1.0.0", ProductType.IMAGE, (ConformanceResult(ConformanceKind.CONTRACT, True, "1"),) * 2
        )


@pytest.mark.req("INT-016")
def test_a_verdict_refusal_names_every_unadmitted_adapter() -> None:
    with exactly(
        IntegrationRefused,
        "no passing adapter compatibility record: a 1.0.0 for cube, mast-observations 2.0.0 for image; no verdict (INT-016)",
    ):
        require_admitted(
            [compatibility()],
            [
                ("mast-observations", "2.0.0", ProductType.IMAGE),
                ("a", "1.0.0", ProductType.CUBE),
                ("mast-observations", "1.0.0", ProductType.IMAGE),
            ],
        )
    require_admitted([compatibility()], [("mast-observations", "1.0.0", ProductType.IMAGE)])

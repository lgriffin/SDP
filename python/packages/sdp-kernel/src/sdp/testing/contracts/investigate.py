"""Contract suites for the Investigate driven ports (Investigate §8: every binding has a fake that
passes the same suite as the real adapter).

Each suite takes one fixture: a case that pairs the port with an input it can answer.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import astropy.units as u
import numpy as np
import pytest

from sdp.kernel.domain.archive import Cone
from sdp.kernel.domain.dataset import DialectRef
from sdp.kernel.domain.investigation import (
    EvidenceUsed,
    FetchedProduct,
    Field,
    Investigation,
    InvestigationSet,
    InvestigationState,
    ObservationCriteria,
    RankedCandidate,
    Subject,
    TestResult,
)
from sdp.kernel.ports.investigate import (
    ImageFrame,
    ObjectResolver,
    ObservationArchiveGateway,
    ObservationTarget,
    ProductDialect,
    SourceDetector,
    SourceRegistry,
    WcsRefiner,
)
from sdp.kernel.ports.repositories import UnitOfWork
from sdp.kernel.science import products
from sdp.testing.investigate import synthetic_profile

AT = datetime(2026, 10, 28, 12, tzinfo=UTC)


@dataclass(frozen=True)
class ObservationCase:
    gateway: ObservationArchiveGateway
    target: ObservationTarget
    criteria: ObservationCriteria
    field: Field
    expect_records: bool = True


class ObservationArchiveGatewayContract:
    @pytest.mark.req("INV-004")
    def test_search_returns_metadata_with_release_licence_and_query(self, observation_case: ObservationCase) -> None:
        index = observation_case.gateway.search(observation_case.target, observation_case.criteria)
        assert index.archive and index.release_label and index.query_text
        assert index.licence.strip() and index.citation.strip()
        assert index.target == observation_case.target.key
        if observation_case.expect_records:
            assert index.records
        for rec in index.records:
            assert rec.product.uri and rec.product_type and rec.mission
            assert rec.licence.strip()
            if observation_case.criteria.product_types:
                assert rec.product_type in observation_case.criteria.product_types
            if observation_case.criteria.missions:
                assert rec.mission in observation_case.criteria.missions

    def test_search_is_deterministic(self, observation_case: ObservationCase) -> None:
        a = observation_case.gateway.search(observation_case.target, observation_case.criteria)
        b = observation_case.gateway.search(observation_case.target, observation_case.criteria)
        assert a.to_document() == b.to_document()

    @pytest.mark.req("INV-013")
    def test_fetch_reports_where_the_bytes_were_read_from(self, observation_case: ObservationCase) -> None:
        index = observation_case.gateway.search(observation_case.target, observation_case.criteria)
        if not index.records:
            pytest.skip("case has no products to fetch")
        record = index.records[0]
        fetched = observation_case.gateway.fetch(record.product)
        assert fetched.data
        assert fetched.read_from in (record.product.uri, record.product.cloud_uri)
        if record.product.cloud_uri:
            assert fetched.read_from == record.product.cloud_uri
        assert fetched.licence.strip() and fetched.citation.strip() and fetched.release_label

    def test_cutout_is_bounded_to_the_field(self, observation_case: ObservationCase) -> None:
        fetched = observation_case.gateway.cutout(observation_case.field, 1 * u.arcmin)
        assert fetched.data and fetched.read_from


@dataclass(frozen=True)
class DetectorCase:
    detector: SourceDetector
    frame: ImageFrame
    expected: int | None = None
    """How many planted sources a run must find (at least)."""


class SourceDetectorContract:
    @pytest.mark.req("INV-003")
    def test_detections_carry_pixel_and_sky_positions_and_their_wcs(self, detector_case: DetectorCase) -> None:
        found = detector_case.detector.detect(detector_case.frame, {})
        assert found.wcs_header.strip()
        assert found.detector_id == detector_case.detector.detector_id
        for d in found.detections:
            assert all(math.isfinite(v) for v in (d.x, d.y, d.ra_deg, d.dec_deg, d.flux))
            assert 0 <= d.ra_deg < 360 and -90 <= d.dec_deg <= 90
        if detector_case.expected is not None:
            assert len(found.detections) >= detector_case.expected

    def test_detection_is_deterministic(self, detector_case: DetectorCase) -> None:
        a = detector_case.detector.detect(detector_case.frame, {})
        b = detector_case.detector.detect(detector_case.frame, {})
        assert a.detections == b.detections


@dataclass(frozen=True)
class RefinerCase:
    refiner: WcsRefiner
    detector: SourceDetector
    frame: ImageFrame
    reference_ra: np.ndarray
    reference_dec: np.ndarray
    max_rms_arcsec: float = 1.0


class WcsRefinerContract:
    def test_refinement_keeps_every_detection_and_reports_its_fit(self, refiner_case: RefinerCase) -> None:
        found = refiner_case.detector.detect(refiner_case.frame, {})
        refined = refiner_case.refiner.refine(found, refiner_case.reference_ra, refiner_case.reference_dec, {})
        assert len(refined.detections) == len(found.detections)
        assert refined.wcs_header.strip()
        assert refined.rms_arcsec >= 0 and refined.matches >= 0
        assert refined.refiner_id == refiner_case.refiner.refiner_id

    def test_refined_positions_land_on_the_reference(self, refiner_case: RefinerCase) -> None:
        found = refiner_case.detector.detect(refiner_case.frame, {})
        refined = refiner_case.refiner.refine(found, refiner_case.reference_ra, refiner_case.reference_dec, {})
        assert refined.matches > 0
        assert refined.rms_arcsec <= refiner_case.max_rms_arcsec


@dataclass(frozen=True)
class ResolverCase:
    resolver: ObjectResolver
    known_name: str
    known_ra: float
    known_dec: float
    empty_ra: float
    empty_dec: float


class ObjectResolverContract:
    def test_a_known_position_resolves_with_type_and_bibcodes(self, resolver_case: ResolverCase) -> None:
        rec = resolver_case.resolver.by_position(resolver_case.known_ra, resolver_case.known_dec, 5.0)
        assert rec is not None
        assert rec.main_id and rec.object_type
        assert isinstance(rec.bibcodes, tuple)
        assert rec.separation_arcsec is not None and rec.separation_arcsec <= 5.0

    def test_an_empty_position_resolves_to_nothing(self, resolver_case: ResolverCase) -> None:
        assert resolver_case.resolver.by_position(resolver_case.empty_ra, resolver_case.empty_dec, 1.0) is None

    def test_a_name_resolves(self, resolver_case: ResolverCase) -> None:
        rec = resolver_case.resolver.by_name(resolver_case.known_name)
        assert rec is not None and rec.main_id
        assert resolver_case.resolver.release_label


@dataclass(frozen=True)
class RegistryCase:
    registry: SourceRegistry
    field: Field
    standard: str


class SourceRegistryContract:
    def test_services_match_the_requested_standard(self, registry_case: RegistryCase) -> None:
        services = registry_case.registry.services(registry_case.field, registry_case.standard)
        assert services
        for s in services:
            assert s.ivoid.startswith("ivo://") and s.access_url and s.title
            assert s.standard.lower() == registry_case.standard.lower()


@dataclass(frozen=True)
class DialectCase:
    dialect: ProductDialect
    raw: FetchedProduct
    product_type: str
    """A type this dialect curates ``raw`` into (``lightcurve`` or ``astrometry``)."""
    min_epochs: int = 1


class ProductDialectContract:
    @pytest.mark.req("INV-005")
    def test_curation_writes_the_canonical_format_tests_read(self, dialect_case: DialectCase) -> None:
        curated = dialect_case.dialect.curate(dialect_case.product_type, dialect_case.raw)
        assert curated is not None and curated.media_type == "text/csv"
        if dialect_case.product_type == products.LIGHTCURVE:
            lc = products.parse_lightcurve(curated.data)
            assert len(lc.mjd) >= dialect_case.min_epochs
            assert np.all(np.diff(lc.mjd) >= 0) and np.all(lc.mag_err > 0)
        else:
            pos = products.parse_positions(curated.data)
            assert len(pos.epoch_jyear) >= dialect_case.min_epochs
            assert np.all(pos.pos_err_mas > 0)
            assert np.all((pos.ra_deg >= 0) & (pos.ra_deg < 360)) and np.all(np.abs(pos.dec_deg) <= 90)

    def test_curation_is_deterministic_and_versioned(self, dialect_case: DialectCase) -> None:
        a = dialect_case.dialect.curate(dialect_case.product_type, dialect_case.raw)
        b = dialect_case.dialect.curate(dialect_case.product_type, dialect_case.raw)
        assert a == b
        assert dialect_case.dialect.dialect_id
        DialectRef(dialect_case.dialect.dialect_id, dialect_case.dialect.dialect_version)

    def test_types_it_does_not_curate_are_left_raw(self, dialect_case: DialectCase) -> None:
        assert dialect_case.dialect.curate("no-such-product-type", dialect_case.raw) is None


# ---------------------------------------------------------------------------------------------
# InvestigationRepository, through a unit of work

UowFactory = Callable[[], UnitOfWork]


def sample_set(set_id: str = "invset-contract") -> InvestigationSet:
    return InvestigationSet(
        set_id=set_id, field=Field(Cone(120.0, -10.0, 0.5)), profile=synthetic_profile(), seed=1, started_at=AT
    )


def sample_investigation(investigation_id: str, set_id: str = "invset-contract") -> Investigation:
    result = TestResult("pm-excess", "1.0.0", Subject.of_object("gaia:DR3:1"), 7.0, seed=1, p_value=0.01)
    return Investigation.open(
        investigation_id=investigation_id,
        set_id=set_id,
        field_id="field-x",
        candidate=RankedCandidate(result.subject, 1.0, (result,)),
        evidence_bundle_ids=("bundle-1",),
        policy=sample_set().profile.ranking,
        evidence_used=EvidenceUsed(("synthetic-gaia",), ("gaia:DR3",), Cone(120.0, -10.0, 0.5).to_document()),
        at=AT,
    )


class InvestigationRepositoryContract:
    def test_sets_and_investigations_round_trip(self, uow_factory: UowFactory) -> None:
        s = sample_set()
        inv = sample_investigation("inv-1")
        with uow_factory() as uow:
            uow.investigations.save_set(s)
            uow.investigations.save(inv)
            uow.commit()
        with uow_factory() as uow:
            got_set = uow.investigations.get_set(s.set_id)
            got = uow.investigations.get("inv-1")
        assert got_set is not None and got_set.to_document() == s.to_document()
        assert got is not None and got.to_document() == inv.to_document()

    @pytest.mark.req("INV-010")
    def test_parked_and_closed_investigations_are_retained_with_reasons(self, uow_factory: UowFactory) -> None:
        s = sample_set()
        parked, closed = sample_investigation("inv-p"), sample_investigation("inv-c")
        parked.park("waiting for Gaia DR4", "leigh", AT)
        closed.close("image artefact", "leigh", AT)
        with uow_factory() as uow:
            uow.investigations.save_set(s)
            uow.investigations.save(parked)
            uow.investigations.save(closed)
            uow.commit()
        with uow_factory() as uow:
            kept = {i.investigation_id: i for i in uow.investigations.for_set(s.set_id)}
            sets = uow.investigations.list_sets()
        assert kept["inv-p"].state is InvestigationState.PARKED
        assert kept["inv-p"].transitions[-1].reason == "waiting for Gaia DR4"
        assert kept["inv-c"].state is InvestigationState.CLOSED
        assert kept["inv-c"].transitions[-1].reason == "image artefact"
        assert [x.set_id for x in sets] == [s.set_id]

    def test_saving_again_updates_state_without_losing_history(self, uow_factory: UowFactory) -> None:
        s = sample_set()
        inv = sample_investigation("inv-h")
        with uow_factory() as uow:
            uow.investigations.save_set(s)
            uow.investigations.save(inv)
            uow.commit()
        inv.park("needs a later epoch", "leigh", AT)
        with uow_factory() as uow:
            uow.investigations.save(inv)
            uow.commit()
        with uow_factory() as uow:
            got = uow.investigations.get("inv-h")
        assert got is not None
        assert [t.to_state for t in got.transitions] == [InvestigationState.OPEN, InvestigationState.PARKED]

    def test_uncommitted_work_is_rolled_back(self, uow_factory: UowFactory) -> None:
        with uow_factory() as uow:
            uow.investigations.save_set(sample_set("invset-rolled"))
        with uow_factory() as uow:
            assert uow.investigations.get_set("invset-rolled") is None

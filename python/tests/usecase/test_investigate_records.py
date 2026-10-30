"""T4: InvestigateField, TriageInvestigation and QueryInvestigations in full: the queries each stage
reads, the records it writes with their exact payloads, the evidence graph, the stage summaries and
manifest, the dossier a candidate carries, and the exact refusal each guard gives (INV-001 to INV-013).

The behaviour is in test_investigate.py; these pin the details mutation testing showed nothing else
checked (docs/testing.md, "Killing a survivor")."""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field, replace
from typing import Any

import pytest
from tests.products.synthetic_products import image, raw_product
from tests.products.test_jwst_retrieve import FaithfulDialect
from tests.usecase.test_investigate import World, frozen_confirmatory, make_world
from tests.usecase.test_planning import World as PlanningWorld
from tests.usecase.test_planning import make_world as planning_world

from sdp.adapters.products import EnvelopeValidator, JwstDialect
from sdp.kernel.application.investigate import (
    CATALOGUE_ROWS,
    InvestigateFieldUseCase,
    ProductEnvelopes,
    SignificanceRanker,
    _MeteredStore,
    _StoreCheckpoints,
    _warn_if_product_lost,
    catalogue_query,
    cutout_ref,
    field_search_cone,
    identify_query,
    mjd_to_jyear,
    observation_target,
    query_operation,
    set_path,
)
from sdp.kernel.application.media import JSON, decode_artifact
from sdp.kernel.domain.archive import Box, CatalogueQuery, Cone, Filter, idempotency_key
from sdp.kernel.domain.canonical import canonical_json
from sdp.kernel.domain.dataset import DatasetArtifact, DatasetStatus, DialectRef, ProductUse
from sdp.kernel.domain.errors import (
    CurationBlocked,
    IntegrityError,
    InvariantViolation,
    PlanInvalid,
    PredeclarationNotFrozen,
    PromotionRefused,
)
from sdp.kernel.domain.ids import ArtifactId
from sdp.kernel.domain.integration import (
    CutoutMethod,
    CutoutSupport,
    IntegrationRefused,
    Operation,
    PlanGate,
    SemanticCheck,
    SemanticValidationReport,
)
from sdp.kernel.domain.investigation import (
    EXPLORATORY,
    ArchiveRole,
    ArchiveSelection,
    FetchedProduct,
    Field,
    FieldImage,
    IdentifyRoute,
    InvestigationState,
    ObservationCriteria,
    ObservationRecord,
    ProductEvidence,
    ProductRef,
    RankedCandidate,
    RankingPolicy,
    Stage,
    Subject,
)
from sdp.kernel.domain.provenance import qualified
from sdp.kernel.domain.sources import SourceKind
from sdp.kernel.ports.integration import ProductRows
from sdp.kernel.ports.investigate import (
    CuratedProduct,
    Detection,
    ImageFrame,
    InvestigateFieldCommand,
    InvestigationRun,
    ObservationTarget,
)
from sdp.kernel.science.investigation import PRODUCTS_SERVICE
from sdp.kernel.science.plan import Backend, ComputeTask
from sdp.testing.fakes.integration import StaticProductInventory
from sdp.testing.fakes.investigate import (
    FakeObservationArchive,
    PlacedRecord,
    PlantedDetector,
    ThinningDialect,
    tan_header,
)
from sdp.testing.investigate import (
    PM_POLICY,
    ProperMotionExcessTest,
    observation_archive,
    synthetic_catalogue,
    synthetic_field,
    synthetic_profile,
)
from sdp.testing.wiring import InMemoryKernel

pytestmark = pytest.mark.tier("T4")

LIGHTCURVES = ("lightcurve",)
USE = ProductUse("mast-observations", "1.0.0", "image")


def events(k: InMemoryKernel, name: str) -> list[tuple[str, Any, dict[str, Any]]]:
    return [
        (e.event.aggregate_id, e.event.occurred_at, dict(e.event.payload))
        for e in k.db.state.events
        if e.event.name == name
    ]


def use_case(world: World) -> InvestigateFieldUseCase:
    uc = world.k.kernel.investigate_field
    assert uc is not None
    return uc


# --- the pure helpers ----------------------------------------------------------------------------


def test_a_set_keeps_its_files_under_its_own_path() -> None:
    assert set_path("invset-7", "manifest.json") == "investigations/invset-7/manifest.json"


def test_identify_reads_the_field_with_the_profile_columns_cuts_and_cap() -> None:
    profile = synthetic_profile()
    cuts = (Filter("g_mag", "<", 18.0),)
    profile = replace(profile, identify=replace(profile.identify, quality_cuts=cuts, row_cap=70_000))
    query = identify_query(synthetic_field(), profile)
    assert query == CatalogueQuery(
        source_id="synthetic-gaia",
        release_label="gaia:DR3",
        region=Cone(120.0, -10.0, 0.5),
        columns=profile.identify.columns,
        filters=cuts,
        row_cap=70_000,
        chunk_rows=50_000,
    )
    small = replace(profile, identify=replace(profile.identify, row_cap=20))
    assert identify_query(synthetic_field(), small).chunk_rows == 20
    capped = replace(profile, identify=replace(profile.identify, row_cap=50_001))
    assert identify_query(synthetic_field(), capped).chunk_rows == 50_000


def test_a_catalogue_archive_is_read_over_the_field_and_its_match_radius_in_one_page() -> None:
    selection = ArchiveSelection("synthetic-2mass", ArchiveRole.CATALOGUE, "twomass:psc", ("ra_deg",), 36.0)
    query = catalogue_query(synthetic_field(), selection)
    assert query == CatalogueQuery(
        source_id="synthetic-2mass",
        release_label="twomass:psc",
        region=Cone(120.0, -10.0, 0.51),
        columns=("ra_deg",),
        row_cap=CATALOGUE_ROWS,
        chunk_rows=CATALOGUE_ROWS,
    )
    assert CATALOGUE_ROWS == 50_000


def test_an_observation_target_follows_the_gaia_motion_only_when_it_is_whole() -> None:
    row = {"pm_ra_cosdec_masyr": 25.0, "pm_dec_masyr": -18.0, "epoch_jyear": 2016.0}
    assert observation_target("gaia:1", 120.0, -10.0, [row]) == ObservationTarget(
        "gaia:1", 120.0, -10.0, 25.0, -18.0, 2016.0
    )
    for partial in ({**row, "pm_dec_masyr": None}, {**row, "epoch_jyear": math.nan}, {}):
        assert observation_target("gaia:1", 120.0, -10.0, [partial]) == ObservationTarget("gaia:1", 120.0, -10.0)
    assert observation_target("gaia:1", 120.0, -10.0, []) == ObservationTarget("gaia:1", 120.0, -10.0)


def test_a_query_over_a_box_is_a_region_operation() -> None:
    base = identify_query(synthetic_field(), synthetic_profile())
    assert query_operation(base) is Operation.CONE
    assert query_operation(replace(base, region=Box(119.0, 121.0, -11.0, -9.0))) is Operation.REGION


def test_a_field_wide_search_covers_the_field() -> None:
    assert field_search_cone(synthetic_field(0.5)) == (120.0, -10.0, 1800.0)
    ra, dec, radius = field_search_cone(Field(Box(119.0, 121.0, -11.0, -9.0)))
    assert (ra, dec) == (120.0, -10.0)
    # Half the diagonal of a box 2 deg tall and 2 cos(10 deg) wide on the sky.
    assert radius == pytest.approx(5052.644, abs=1e-3)
    assert radius == pytest.approx(math.hypot(2 * math.cos(math.radians(10.0)), 2.0) / 2 * 3600.0, rel=1e-12)


def test_a_modified_julian_date_is_a_julian_year() -> None:
    assert mjd_to_jyear(51544.5) == 2000.0
    assert mjd_to_jyear(51544.5 + 365.25) == 2001.0
    assert mjd_to_jyear(9000.0) == pytest.approx(1883.5195071868584, rel=1e-15)


def test_a_product_use_that_cannot_be_added_to_a_record_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    existing = _raw(product=None)
    with caplog.at_level(logging.WARNING, logger="sdp.kernel.investigate"):
        _warn_if_product_lost(existing, None)
        _warn_if_product_lost(replace(existing, product=USE), USE)
        assert caplog.records == []
        _warn_if_product_lost(existing, USE)
    (record,) = caplog.records
    assert record.getMessage() == "artifact already registered with another product use"
    assert record.__dict__["artifact_id"] == existing.artifact_id.value
    assert record.__dict__["had"] == "None" and record.__dict__["offered"] == str(USE)


def _raw(product: ProductUse | None) -> DatasetArtifact:
    world = make_world()
    return use_case(world)._register_bytes(
        "invset-x",
        data=b"raw bytes",
        media_type="text/csv",
        archive="fake-plates",
        release_label="dasch:DR7",
        licence="fixture licence",
        citation="fixture citation",
        rendered="GET https://fake.invalid/lc-0.csv",
        product=product,
    )


def test_the_metered_store_counts_only_bytes_it_adds() -> None:
    k = make_world().k
    meter = _MeteredStore(k.store)
    assert meter.take() == 0
    first = meter.put(b"12345", "text/plain", logical_path="a/b.txt")
    assert k.store.resolve("a/b.txt") == first == meter.resolve("a/b.txt")
    assert meter.media_type(first) == "text/plain" and meter.exists(first) and meter.get(first) == b"12345"
    assert not meter.exists(ArtifactId.of_bytes(b"never stored"))
    meter.put(b"12345", "text/plain")
    meter.put(b"678", "text/plain")
    meter.add(10)
    assert meter.take() == 18
    assert meter.take() == 0
    meter.link("c.txt", first)
    assert k.store.resolve("c.txt") == first
    assert meter.free_bytes() == k.store.free_bytes()


# --- task checkpoints ----------------------------------------------------------------------------


def test_a_task_checkpoint_is_stored_under_the_set_and_announced() -> None:
    k = make_world().k
    sink = _StoreCheckpoints(k.store, k.db.unit_of_work, k.clock, "invset-9")
    assert sink.completed("run:t@1") == (False, None)
    value = {"results": [1.5, 2]}
    sink.write(ComputeTask("run:t@1", "kind", PM_POLICY), value, 1.23456789, {})
    artifact = k.store.resolve("investigations/invset-9/tasks/run:t@1.json")
    assert artifact is not None and k.store.media_type(artifact) == JSON
    assert k.store.get(artifact) == canonical_json(value).encode()
    assert sink.completed("run:t@1") == (True, value)
    assert sink.completed("run:t@2") == (False, None)
    ((aggregate, at, payload),) = events(k, "CheckpointWritten")
    assert aggregate == "invset-9" and at is not None
    assert payload == {"task_id": "run:t@1", "artifact_id": artifact.value, "duration_s": 1.234568}


# --- registering what Retrieve reads -------------------------------------------------------------


def test_bytes_read_from_an_archive_are_registered_raw_with_their_source() -> None:
    world = make_world()
    uc = use_case(world)
    artifact = uc._register_bytes(
        "invset-1",
        data=b"mjd,mag\n1,2\n",
        media_type="text/csv",
        archive="fake-plates",
        release_label="dasch:DR7",
        licence="fixture licence",
        citation="fixture citation",
        rendered="GET https://fake.invalid/lc-0.csv",
        product=USE,
    )
    checksum = ArtifactId.of_bytes(b"mjd,mag\n1,2\n")
    assert artifact.artifact_id == checksum and artifact.status is DatasetStatus.ACQUIRED
    assert artifact.media_type == "text/csv" and artifact.byte_count == 12 and artifact.product == USE
    assert world.k.store.media_type(checksum) == "text/csv"
    source = artifact.source_ref
    assert (source.kind, source.identity, source.licence, source.citation, source.release_label, source.query_text) == (
        SourceKind.ARCHIVE,
        "fake-plates",
        "fixture licence",
        "fixture citation",
        "dasch:DR7",
        "GET https://fake.invalid/lc-0.csv",
    )
    acquisition = artifact.acquisition
    assert acquisition is not None
    assert acquisition.rendered_query == "GET https://fake.invalid/lc-0.csv"
    assert acquisition.idempotency_key == idempotency_key("GET https://fake.invalid/lc-0.csv", "dasch:DR7", ())
    assert acquisition.source_release == "dasch:DR7" and acquisition.acquired_at is not None
    assert acquisition.checksum == checksum and acquisition.byte_count == 12
    assert world.k.db.state.datasets.get(checksum) == artifact
    ((aggregate, at, payload),) = events(world.k, "DatasetAcquired")
    assert aggregate == checksum.value and at is not None
    assert payload == {"source": "fake-plates", "release": "dasch:DR7", "bytes": 12}
    graph = world.k.db.state.graph
    assert (qualified("investigation/invset-1"), qualified(checksum.value)) in graph.relations["used"]


def test_bytes_registered_before_are_reused_and_used_again(caplog: pytest.LogCaptureFixture) -> None:
    world = make_world()
    uc = use_case(world)
    kw: dict[str, Any] = {
        "data": b"payload",
        "media_type": "application/json",
        "archive": "fake-plates",
        "release_label": "dasch:DR7",
        "licence": "l",
        "citation": "c",
        "rendered": "q",
    }
    first = uc._register_bytes("invset-1", **kw)
    with caplog.at_level(logging.WARNING, logger="sdp.kernel.investigate"):
        again = uc._register_bytes("invset-2", **kw, product=USE)
    assert again == first and again.product is None
    assert [r.getMessage() for r in caplog.records] == ["artifact already registered with another product use"]
    assert len(events(world.k, "DatasetAcquired")) == 1
    used = world.k.db.state.graph.relations["used"]
    assert (qualified("investigation/invset-2"), qualified(first.artifact_id.value)) in used


@pytest.mark.parametrize(("licence", "citation"), [(" ", "c"), ("l", " ")])
def test_bytes_without_a_licence_or_citation_are_refused(licence: str, citation: str) -> None:
    with pytest.raises(
        CurationBlocked, match=r"^fake-plates declares no licence or citation; retrieval is blocked \(DSI-006\)$"
    ):
        use_case(make_world())._register_bytes(
            "s",
            data=b"x",
            media_type="text/csv",
            archive="fake-plates",
            release_label="r",
            licence=licence,
            citation=citation,
            rendered="q",
        )


@dataclass
class Tampered:
    """A store whose read-back differs from what was written, or that files bytes under another id."""

    inner: Any
    misfile: bool = False

    def put(self, data: bytes, media_type: str, *, logical_path: str | None = None) -> ArtifactId:
        stored = self.inner.put(data, media_type, logical_path=logical_path)
        return ArtifactId.of_bytes(b"elsewhere") if self.misfile else stored

    def get(self, artifact_id: ArtifactId) -> bytes:
        return b"tampered" + bytes(self.inner.get(artifact_id))

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


@pytest.mark.parametrize("misfile", [False, True])
def test_bytes_that_do_not_read_back_as_written_are_refused(misfile: bool) -> None:
    world = make_world()
    uc = use_case(world)
    uc.store = Tampered(uc.store, misfile=misfile)
    checksum = ArtifactId.of_bytes(b"x")
    with pytest.raises(IntegrityError, match=rf"^fake-plates: stored bytes do not hash to {checksum}$"):
        uc._register_bytes(
            "s",
            data=b"x",
            media_type="text/csv",
            archive="fake-plates",
            release_label="r",
            licence="l",
            citation="c",
            rendered="q",
        )


# --- curating a product --------------------------------------------------------------------------


def _fetched(data: bytes, product_id: str = "lc-0") -> FetchedProduct:
    ref = ProductRef("fake-plates", product_id, f"https://fake.invalid/{product_id}.csv")
    return FetchedProduct(ref, data, "text/csv", "dasch:DR7", "l", "c", ref.uri)


LIGHTCURVE = b"mjd,mag\n1,10\n2,11\n3,12\n"


def _register(uc: InvestigateFieldUseCase, data: bytes, product: ProductUse | None = None) -> DatasetArtifact:
    return uc._register_bytes(
        "invset-1",
        data=data,
        media_type="text/csv",
        archive="fake-plates",
        release_label="dasch:DR7",
        licence="l",
        citation="c",
        rendered="q",
        product=product,
    )


def test_a_curated_product_is_stamped_registered_and_recorded() -> None:
    world = make_world()
    uc = use_case(world)
    raw = _register(uc, LIGHTCURVE, USE)
    curated = uc._curate_product("invset-1", raw, _fetched(LIGHTCURVE), "lightcurve", ThinningDialect())
    assert curated is not None
    stamp = f"# sdp.dialect=fixture-thinning@1.0.0 sdp.raw_parent={raw.artifact_id.value}\n".encode()
    data = stamp + b"mjd,mag\n1,10\n3,12\n"
    assert curated.artifact_id == ArtifactId.of_bytes(data) and world.k.store.get(curated.artifact_id) == data
    assert world.k.store.media_type(curated.artifact_id) == "text/csv"
    assert curated.status is DatasetStatus.CURATED and curated.parents == (raw.artifact_id,)
    assert curated.byte_count == len(data) and curated.media_type == "text/csv" and curated.product == USE
    assert curated.dialect == DialectRef("fixture-thinning", "1.0.0")
    assert world.k.db.state.datasets.get(curated.artifact_id) == curated
    aggregate, at, payload = events(world.k, "DatasetAcquired")[-1]
    assert aggregate == curated.artifact_id.value and at is not None
    assert payload == {
        "curated_from": raw.artifact_id.value,
        "dialect": "fixture-thinning",
        "dialect_version": "1.0.0",
        "product_type": "lightcurve",
    }
    graph = world.k.db.state.graph
    curation, curated_q, raw_q = (
        qualified(f"curation/{curated.artifact_id.value}"),
        qualified(curated.artifact_id.value),
        qualified(raw.artifact_id.value),
    )
    assert graph.activities[curation] == {
        "sdp:type": "Curation",
        "sdp:dialect": "fixture-thinning",
        "sdp:dialect_version": "1.0.0",
    }
    assert graph.entities[curated_q] == {"sdp:type": "DatasetArtifact", "sdp:kind": "curated"}
    assert (curation, raw_q) in graph.relations["used"]
    assert (curated_q, curation) in graph.relations["wasGeneratedBy"]
    assert (curated_q, raw_q) in graph.relations["wasDerivedFrom"]
    assert (qualified("investigation/invset-1"), curated_q) in graph.relations["used"]


@dataclass(frozen=True)
class FixedForm:
    """A dialect that curates every product to the same binary bytes, as a FITS form may."""

    dialect_id: str = "fixed"
    dialect_version: str = "1.0.0"

    def curate(self, product_type: str, raw: FetchedProduct) -> CuratedProduct | None:
        return CuratedProduct(b"SIMPLE  = T", "application/fits")


def test_a_curated_form_two_raw_products_share_is_registered_once(caplog: pytest.LogCaptureFixture) -> None:
    world = make_world()
    uc = use_case(world)
    first = _register(uc, b"a,b\n1,2\n")
    second = _register(uc, b"a,b\n3,4\n", USE)
    curated = uc._curate_product("invset-1", first, _fetched(b"a,b\n1,2\n"), "image", FixedForm())
    assert curated is not None and world.k.store.get(curated.artifact_id) == b"SIMPLE  = T"
    with caplog.at_level(logging.WARNING, logger="sdp.kernel.investigate"):
        again = uc._curate_product("invset-2", second, _fetched(b"a,b\n3,4\n", "lc-1"), "image", FixedForm())
    assert again == curated
    assert [r.getMessage() for r in caplog.records] == ["artifact already registered with another product use"]
    assert caplog.records[0].__dict__["offered"] == str(USE)
    assert len([p for _, _, p in events(world.k, "DatasetAcquired") if "curated_from" in p]) == 1
    used = world.k.db.state.graph.relations["used"]
    assert (qualified("investigation/invset-2"), qualified(curated.artifact_id.value)) in used


def test_a_guard_that_refuses_the_curated_form_leaves_the_raw_product(caplog: pytest.LogCaptureFixture) -> None:
    world = make_world()
    uc = use_case(world)
    raw = _register(uc, LIGHTCURVE)
    report = SemanticValidationReport(
        "lc-0",
        "v",
        "1.0.0",
        (SemanticCheck("arrays", False), SemanticCheck("units", True), SemanticCheck("wcs", False)),
        "sha256:" + "0" * 64,
    )
    with caplog.at_level(logging.WARNING, logger="sdp.kernel.investigate"):
        curated = uc._curate_product(
            "invset-1", raw, _fetched(LIGHTCURVE), "lightcurve", ThinningDialect(), guard=lambda c: report
        )
    assert curated is None
    (record,) = caplog.records
    assert record.getMessage() == "curation refused"
    assert record.__dict__["artifact_id"] == raw.artifact_id.value and record.__dict__["failed"] == "arrays, wcs"


# --- a whole run ---------------------------------------------------------------------------------


@dataclass
class Run:
    world: World
    run: InvestigationRun

    @property
    def set_id(self) -> str:
        return self.run.investigation_set.set_id


@pytest.fixture(scope="module")
def full() -> Run:
    """A run that fetches light curves and curates them, with the fixture resolver."""
    world = make_world(
        ProperMotionExcessTest(needs_products=LIGHTCURVES), product_dialects={"fake-plates": ThinningDialect()}
    )
    return Run(world, world.run(profile=synthetic_profile(needs_products=LIGHTCURVES)))


def test_a_run_announces_its_set_and_records_who_started_it(full: Run) -> None:
    profile = synthetic_profile(needs_products=LIGHTCURVES)
    ((aggregate, at, payload),) = events(full.world.k, "InvestigationSetStarted")
    assert aggregate == full.set_id and full.set_id.startswith("invset-") and at is not None
    assert payload == {
        "field_id": synthetic_field().field_id,
        "profile_id": "synthetic-comoving",
        "profile_version": "1.0.0",
        "profile_hash": profile.profile_hash,
        "seed": 11,
        "code_revision": full.run.manifest["code_revision"],
        "actor": "kernel",
        "plan_artifact_id": None,
    }
    assert full.run.manifest["code_revision"] == "0" * 40
    graph = full.world.k.db.state.graph
    activity = qualified(f"investigation/{full.set_id}")
    assert graph.activities[activity] == {
        "sdp:type": "Investigate",
        "sdp:label": EXPLORATORY,
        "sdp:profile_hash": profile.profile_hash,
    }
    assert qualified("kernel") in graph.agents
    assert (activity, qualified("kernel")) in graph.relations["wasAssociatedWith"]


def test_each_stage_stores_its_document_under_the_set_and_in_the_evidence_graph(full: Run) -> None:
    k, s = full.world.k, full.run.investigation_set
    graph = k.db.state.graph
    activity = qualified(f"investigation/{full.set_id}")
    for stage, record in s.stages.items():
        assert record.artifact_id is not None and record.finished_at is not None and record.error is None
        assert k.store.resolve(f"investigations/{full.set_id}/{stage.value}.json") == record.artifact_id
        assert k.store.media_type(record.artifact_id) == JSON
        entity = qualified(record.artifact_id.value)
        assert graph.entities[entity] == {"sdp:type": f"Investigate{stage.value.title()}", "sdp:label": EXPLORATORY}
        assert (entity, activity) in graph.relations["wasGeneratedBy"]
    identify = decode_artifact(k.store.get(s.stages[Stage.IDENTIFY].artifact_id), JSON)  # type: ignore[arg-type]
    assert identify["field_id"] == synthetic_field().field_id and len(identify["objects"]) == 60
    test = decode_artifact(k.store.get(s.stages[Stage.TEST].artifact_id), JSON)  # type: ignore[arg-type]
    assert len(test["results"]) == 60 and test["null_results"][0]["trials"] == 5


def test_each_stage_summarises_what_it_did_and_the_bytes_it_stored(full: Run) -> None:
    summaries = {stage: dict(r.summary) for stage, r in full.run.investigation_set.stages.items()}
    stored = {stage: summary.pop("stored_bytes") for stage, summary in summaries.items()}
    assert summaries == {
        Stage.IDENTIFY: {"objects": 60, "unmatched": 0, "resolved": 1, "route": "catalogue"},
        Stage.RETRIEVE: {"bundles": 60, "observation_indices": 60, "products": 5},
        Stage.TEST: {"results": 60, "tests": 1},
        Stage.CANDIDATES: {"candidates": 5, "ranked": 5},
    }
    assert all(isinstance(n, int) and n > 0 for n in stored.values())
    completed = [p for _, _, p in events(full.world.k, "InvestigationStageCompleted")]
    assert [{k: v for k, v in p.items() if k not in ("artifact_id", "stored_bytes")} for p in completed] == [
        {"stage": stage.value, **summary} for stage, summary in summaries.items()
    ]


def test_the_manifest_records_the_environment_and_the_tests_with_their_thresholds(full: Run) -> None:
    k, m = full.world.k, full.run.manifest
    assert "manifest_id" not in m
    assert m["environment"] == k.kernel.investigate_field.environment.record(Backend.CPU).to_document()  # type: ignore[union-attr]
    assert m["tests"][0]["thresholds"] == {"score": 5.0} and m["tests"][0]["version"] == "1.0.0"
    stored = k.store.resolve(f"investigations/{full.set_id}/manifest.json")
    assert stored is not None and stored == full.run.investigation_set.manifest_id
    assert k.store.media_type(stored) == JSON and decode_artifact(k.store.get(stored), JSON) == m
    assert full.run.investigation_set.tests_applied == (
        {
            "name": "pm-excess",
            "version": "1.0.0",
            "thresholds": {"score": 5.0},
            "policy": PM_POLICY.to_document(),
        },
    )


def test_the_tests_write_a_checkpoint_for_each_task_of_the_set(full: Run) -> None:
    k = full.world.k
    written = [(a, p) for a, _, p in events(k, "CheckpointWritten") if a == full.set_id]
    assert [p["task_id"] for _, p in written] == ["run:pm-excess@1.0.0", "null:pm-excess@1.0.0"]
    for _, payload in written:
        found = k.store.resolve(f"investigations/{full.set_id}/tasks/{payload['task_id']}.json")
        assert found is not None and payload["artifact_id"] == found.value


def test_each_candidate_is_an_exploratory_investigation_derived_from_its_evidence(full: Run) -> None:
    k = full.world.k
    graph = k.db.state.graph
    activity = qualified(f"investigation/{full.set_id}")
    bundles = {b.bundle_id: b for b in full.run.bundles}
    for inv in full.run.investigations:
        assert inv.investigation_id.startswith("inv-") and inv.field_id == synthetic_field().field_id
        node = qualified(inv.investigation_id)
        assert graph.entities[node] == {"sdp:type": "Investigation", "sdp:label": EXPLORATORY}
        assert (node, activity) in graph.relations["wasGeneratedBy"]
        derived = {o for s, o in graph.relations["wasDerivedFrom"] if s == node}
        expected = {qualified(a.value) for b in inv.evidence_bundle_ids for a in bundles[b].artifact_ids()}
        assert derived == expected and len(expected) >= 3
        dossier = k.store.resolve(f"investigations/{full.set_id}/dossiers/{inv.investigation_id}.json")
        assert dossier is not None and dossier.value == inv.links["dossier_id"]
        assert k.store.media_type(dossier) == JSON


def test_a_dossier_carries_the_candidate_its_objects_and_the_set_it_came_from(full: Run) -> None:
    k = full.world.k
    query = k.kernel.query_investigations
    assert query is not None
    inv = next(i for i in full.run.investigations if i.candidate.object_key == "gaia:DR3:0")
    bundle = next(b for b in full.run.bundles if b.object_key == "gaia:DR3:0")
    profile = synthetic_profile(needs_products=LIGHTCURVES)
    dossier = query.dossier(inv.investigation_id)
    assert dossier is not None
    null = full.run.investigation_set.null_results[0].to_document()
    assert dossier == {
        "kind": "investigation-dossier",
        "label": EXPLORATORY,
        "investigation_id": inv.investigation_id,
        "set_id": full.set_id,
        "field": synthetic_field().to_document(),
        "profile": {"profile_id": "synthetic-comoving", "version": "1.0.0", "hash": profile.profile_hash},
        "candidate": inv.candidate.to_document(),
        "rank": inv.rank.to_document(),
        "objects": [
            {
                "object": bundle.object.to_document(),
                "bundle_id": bundle.bundle_id,
                "catalogue_rows": bundle.to_document()["catalogue_rows"],
                "catalogue_artifacts": {k: v.value for k, v in sorted(bundle.catalogue_artifacts.items())},
                "observation_index": [
                    {
                        "archive": "fake-plates",
                        "release_label": "dasch:DR7",
                        "records": 3,
                        "reach_back_mjd": 9000,
                        "reach_back_jyear": mjd_to_jyear(9000.0),
                    }
                ],
                "products": [p.to_document() for p in bundle.products],
            }
        ],
        "test_results": [{**r.to_document(), "null": null} for r in inv.test_results],
        "set_false_positive_rates": {"pm-excess@1.0.0": null},
        "multiplicity": {"objects_examined": 60, "tests_applied": 1},
        "evidence_used": inv.evidence_used.to_document(),
    }
    assert len(dossier["objects"][0]["products"]) == 1 and len(dossier["objects"][0]["catalogue_artifacts"]) == 2


def test_the_evidence_an_investigation_used_spans_its_archives_releases_and_epochs(full: Run) -> None:
    inv = next(i for i in full.run.investigations if i.candidate.object_key == "gaia:DR3:0")
    assert inv.evidence_used.to_document() == {
        "archives": ["fake-plates", "synthetic-2mass", "synthetic-gaia"],
        "releases": ["dasch:DR7", "gaia:DR3", "simbad:fixture", "twomass:psc"],
        "region": synthetic_field().region.to_document(),
        "epoch_min_jyear": mjd_to_jyear(9000.0),
        "epoch_max_jyear": 2016.0,
    }


def test_the_query_side_reads_the_set_and_nothing_for_what_is_not_there(full: Run) -> None:
    query = full.world.k.kernel.query_investigations
    assert query is not None
    found = query.set(full.set_id)
    assert found is not None and found.set_id == full.set_id
    assert query.set("invset-unknown") is None
    assert query.images("invset-unknown") == []
    assert query.images(full.set_id) == []  # a catalogue-first field reads no image
    assert query.manifest("invset-unknown") is None
    assert query.dossier("inv-unknown") is None


# --- a stage that fails, and a run that resumes --------------------------------------------------


def failures(k: InMemoryKernel) -> list[dict[str, Any]]:
    return [p for _, at, p in events(k, "InvestigationStageFailed") if at is not None]


def failed_set(world: World) -> Any:
    query = world.k.kernel.query_investigations
    assert query is not None
    (s,) = query.sets()
    return s


def test_a_failed_stage_records_the_error_by_type_and_message() -> None:
    world = make_world()
    world.plates.licence = ""
    with pytest.raises(CurationBlocked):
        world.run()
    error = "CurationBlocked: fake-plates declares no licence or citation; retrieval is blocked (DSI-006)"
    assert failures(world.k) == [{"stage": "retrieve", "error": error}]
    record = failed_set(world).stages[Stage.RETRIEVE]
    assert record.error == error and record.finished_at is not None


@dataclass(frozen=True)
class FailingTest(ProperMotionExcessTest):
    __test__ = False

    def run(self, bundles: Any, thresholds: Any, context: Any) -> Any:
        raise ValueError("grid has no points")


def test_a_test_that_fails_fails_the_test_stage() -> None:
    world = make_world(FailingTest())
    with pytest.raises(Exception, match="grid has no points"):
        world.run()
    (failure,) = failures(world.k)
    assert failure["stage"] == "test" and "grid has no points" in failure["error"]


class BrokenRanker:
    ranker_id = "broken"

    def rank(self, results: Any, policy: Any) -> Any:
        raise RuntimeError("ranker down")


def test_a_ranker_that_fails_fails_the_candidates_stage() -> None:
    world = make_world()
    use_case(world).ports.ranker = BrokenRanker()
    with pytest.raises(RuntimeError, match="ranker down"):
        world.run()
    assert failures(world.k) == [{"stage": "candidates", "error": "RuntimeError: ranker down"}]


def test_a_resumed_run_redoes_only_the_stage_that_failed() -> None:
    world = make_world()
    world.plates.licence = ""
    with pytest.raises(CurationBlocked):
        world.run()
    set_id = failed_set(world).set_id
    world.plates.licence = "fixture licence"
    run = world.run(set_id=set_id)
    stages = [p["stage"] for _, _, p in events(world.k, "InvestigationStageCompleted")]
    assert stages == ["identify", "retrieve", "test", "candidates"]
    assert run.investigation_set.complete and len(run.investigations) == 5


def test_a_resume_names_the_set_and_keeps_its_field() -> None:
    world = make_world()
    with pytest.raises(InvariantViolation, match=r"^no investigation set invset-404 to resume$"):
        world.run(set_id="invset-404")
    set_id = world.run().investigation_set.set_id
    moved = Field(Cone(120.0, -10.0, 0.4), epoch_jyear=2016.0, label="synthetic field")
    with pytest.raises(
        InvariantViolation,
        match=r"^a resumed run keeps its field and profile; a changed profile is a new version and a new run$",
    ):
        world.run(set_id=set_id, field=moved)


def test_one_use_case_runs_set_after_set() -> None:
    world = make_world()
    first, second = world.run(), world.run()
    assert first.investigation_set.set_id != second.investigation_set.set_id
    assert second.investigation_set.complete


# --- the image-first route -----------------------------------------------------------------------


@dataclass
class Recording:
    """A detector that keeps the frames it was handed."""

    inner: PlantedDetector
    frames: list[ImageFrame]
    detector_id: str = "recording"

    def detect(self, frame: ImageFrame, params: Any) -> Any:
        self.frames.append(frame)
        assert params == {}
        return self.inner.detect(frame, params)


def test_the_image_route_needs_a_detector_and_an_image() -> None:
    profile = synthetic_profile(route=IdentifyRoute.IMAGE, with_observations=False)
    world = make_world()
    with pytest.raises(InvariantViolation, match=r"^the image-first route needs a SourceDetector$"):
        world.run(profile=profile)
    assert failures(world.k) == [
        {"stage": "identify", "error": "InvariantViolation: the image-first route needs a SourceDetector"}
    ]
    use_case(world).ports.detector = PlantedDetector({})
    with pytest.raises(InvariantViolation, match=r"^the image-first route needs at least one image in the field$"):
        world.run(profile=profile)


def test_each_image_is_handed_over_with_its_wcs_and_every_detection_in_the_field_is_kept() -> None:
    rows = synthetic_catalogue().to_pylist()
    world = make_world()
    store = world.k.store
    blank = store.put(b"SIMPLE  = T / blank", "application/fits")
    wcs = store.put(tan_header(120.0, -10.0).encode(), "text/plain")
    frame = store.put(b"SIMPLE  = T / frame", "image/fits")
    outside = Detection(1.0, 1.0, 125.0, -10.0, 10.0)
    star = Detection(2.0, 3.0, rows[0]["ra_deg"], rows[0]["dec_deg"], 900.0)
    stray = Detection(500.0, 500.0, 120.05, -10.05, 50.0)
    frames: list[ImageFrame] = []
    use_case(world).ports.detector = Recording(PlantedDetector({frame.value: [outside, star, stray]}), frames)
    field = Field(synthetic_field().region, images=(FieldImage(blank), FieldImage(frame, wcs_id=wcs)))
    run = world.run(field=field, profile=synthetic_profile(route=IdentifyRoute.IMAGE, with_observations=False))
    assert [(f.artifact_id, f.data, f.media_type, f.wcs) for f in frames] == [
        (blank.value, b"SIMPLE  = T / blank", "application/fits", None),
        (frame.value, b"SIMPLE  = T / frame", "image/fits", tan_header(120.0, -10.0).encode()),
    ]
    objects = {b.object.object_key: b.object for b in run.bundles}
    assert sorted(objects) == sorted([rows[0]["source_key"], f"{field.field_id}:1:2"])
    matched = objects[rows[0]["source_key"]]
    assert matched.pixel_xy == (2.0, 3.0) and matched.match_radius_arcsec == 1.0
    assert matched.match_distance_arcsec == pytest.approx(0.0, abs=1e-6)
    assert matched.wcs_artifact is not None and store.media_type(matched.wcs_artifact) == "text/plain"
    assert store.get(matched.wcs_artifact) == tan_header(120.0, -10.0).encode()
    summary = run.investigation_set.stages[Stage.IDENTIFY].summary
    assert (summary["objects"], summary["unmatched"], summary["resolved"], summary["route"]) == (2, 1, 0, "image")


# --- candidates ----------------------------------------------------------------------------------


@dataclass
class Odd:
    """Ranks a subject no bundle names and a two-star structure ahead of the kernel's own candidates."""

    ranker_id: str = "odd"

    def rank(self, results: Any, policy: Any) -> list[RankedCandidate]:
        real = SignificanceRanker().rank(results, policy)
        group = Subject(structure_id="pair", members=("gaia:DR3:0", "gaia:DR3:1"))
        return [
            RankedCandidate(Subject.of_object("gaia:DR3:999"), 1.0, real[0].results),
            RankedCandidate(group, 1.0, real[0].results),
            *real,
        ]


def test_a_structure_opens_one_investigation_over_its_members_and_an_unknown_subject_none(
    caplog: pytest.LogCaptureFixture,
) -> None:
    world = make_world()
    use_case(world).ports.ranker = Odd()
    with caplog.at_level(logging.WARNING, logger="sdp.kernel.investigate"):
        run = world.run()
    assert [r.getMessage() for r in caplog.records] == ["candidate gaia:DR3:999 has no evidence bundle; skipped"]
    assert len(run.investigations) == 6
    pair = run.investigations[0]
    assert pair.candidate.structure_id == "pair"
    bundles = {b.bundle_id: b.object_key for b in run.bundles}
    assert [bundles[b] for b in pair.evidence_bundle_ids] == ["gaia:DR3:0", "gaia:DR3:1"]
    summary = run.investigation_set.stages[Stage.CANDIDATES].summary
    assert (summary["candidates"], summary["ranked"]) == (6, 7)


# --- triage --------------------------------------------------------------------------------------


@pytest.fixture
def opened() -> tuple[World, str]:
    world = make_world()
    return world, world.run().investigations[0].investigation_id


def test_triage_names_an_investigation_and_an_action(opened: tuple[World, str]) -> None:
    world, inv_id = opened
    with pytest.raises(InvariantViolation, match=r"^no investigation inv-404$"):
        world.triage("inv-404", "park", reason="r")
    with pytest.raises(InvariantViolation, match=r"^unknown triage action 'explode'$"):
        world.triage(inv_id, "explode", reason="r")


def test_a_reopened_investigation_records_who_reopened_it_and_why(opened: tuple[World, str]) -> None:
    world, inv_id = opened
    world.triage(inv_id, "park", reason="wait for DR4")
    reopened: Any = world.triage(inv_id, "reopen", reason="DR4 is out")
    last = reopened.transitions[-1]
    assert (last.from_state, last.to_state, last.reason, last.actor) == (
        InvestigationState.PARKED,
        InvestigationState.OPEN,
        "DR4 is out",
        "leigh",
    )
    assert last.at > reopened.transitions[-2].at


def test_each_promotion_refusal_is_recorded_with_its_reason(opened: tuple[World, str]) -> None:
    world, inv_id = opened
    with pytest.raises(PromotionRefused, match=r"^promotion names a confirmatory predeclaration \(INV-009\)$"):
        world.triage(inv_id, "promote")
    missing = "sha256:" + "0" * 64
    with pytest.raises(
        PredeclarationNotFrozen, match=rf"^{missing} is not in the ledger; promotion refused \(INV-009\)$"
    ):
        world.triage(inv_id, "promote", predeclaration_hash=missing)
    refusals = events(world.k, "InvestigationPromotionRefused")
    assert [(a, p) for a, _, p in refusals] == [
        (
            inv_id,
            {
                "predeclaration_hash": None,
                "actor": "leigh",
                "reason": "promotion names a confirmatory predeclaration (INV-009)",
            },
        ),
        (
            inv_id,
            {
                "predeclaration_hash": missing,
                "actor": "leigh",
                "reason": f"{missing} is not in the ledger; promotion refused (INV-009)",
            },
        ),
    ]
    assert all(at is not None for _, at, _ in refusals)


def test_a_promotion_names_the_experiment_it_opens(opened: tuple[World, str]) -> None:
    world, inv_id = opened
    fresh = frozen_confirmatory(world, {"kind": "release", "value": "gaia:DR4"})
    promoted: Any = world.triage(inv_id, "promote", predeclaration_hash=fresh, experiment_id="exp-7")
    assert promoted.links["experiment_id"] == "exp-7"


# --- Retrieve ------------------------------------------------------------------------------------


def observations(**changes: Any) -> Any:
    """The synthetic profile with its plate archive selection changed, needing light curves."""
    profile = synthetic_profile(needs_products=LIGHTCURVES)
    archives = tuple(replace(a, **changes) if a.role is ArchiveRole.OBSERVATIONS else a for a in profile.archives)
    return replace(profile, archives=archives)


def recorded_searches(world: World, monkeypatch: pytest.MonkeyPatch) -> list[tuple[ObservationTarget, Any]]:
    seen: list[tuple[ObservationTarget, Any]] = []
    real = world.plates.search

    def search(target: ObservationTarget, criteria: Any) -> Any:
        seen.append((target, criteria))
        return real(target, criteria)

    monkeypatch.setattr(world.plates, "search", search)
    return seen


def test_each_object_is_searched_with_its_motion_and_the_selection_criteria(monkeypatch: pytest.MonkeyPatch) -> None:
    world = make_world(ProperMotionExcessTest(needs_products=LIGHTCURVES))
    seen = recorded_searches(world, monkeypatch)
    profile = observations(missions=("DASCH",), product_types=("lightcurve",), min_calibration_level=1)
    run = world.run(profile=profile)
    assert len(seen) == 60
    criteria = ObservationCriteria(
        missions=("DASCH",), product_types=("lightcurve",), radius_arcsec=2.0, min_calibration_level=1
    )
    assert {c for _, c in seen} == {criteria}
    row = synthetic_catalogue().to_pylist()[0]
    assert seen[0][0] == ObservationTarget(
        "gaia:DR3:0", row["ra_deg"], row["dec_deg"], row["pm_ra_cosdec_masyr"], row["pm_dec_masyr"], 2016.0
    )
    bundle = next(b for b in run.bundles if b.object_key == "gaia:DR3:0")
    (index,) = bundle.observation_indices
    (artifact,) = bundle.observation_artifacts
    assert world.k.store.get(artifact) == canonical_json(index.to_document()).encode()
    assert world.k.store.media_type(artifact) == JSON
    registered = world.k.db.state.datasets.get(artifact)
    assert registered is not None and registered.acquisition is not None
    assert registered.acquisition.rendered_query == index.query_text and index.query_text.startswith("FAKE-OBS ")
    assert run.investigation_set.archive_releases == ("dasch:DR7", "gaia:DR3", "simbad:fixture", "twomass:psc")


def test_a_product_is_evidence_for_its_object_read_from_where_it_was_read(full: Run) -> None:
    bundle = next(b for b in full.run.bundles if b.object_key == "gaia:DR3:0")
    (product,) = bundle.products
    assert product.product.product_id == "lc-0" and product.product_type == "lightcurve"
    assert product.read_from == "s3://fake-bucket/lc-0.csv" and product.raw_artifact_id is not None
    assert product.raw_artifact_id == ArtifactId.of_bytes(full.world.plates.products["lc-0"])
    raw = full.world.k.db.state.datasets.get(product.raw_artifact_id)
    assert raw is not None and raw.media_type == "application/octet-stream"
    assert full.world.k.store.media_type(product.raw_artifact_id) == "application/octet-stream"
    curated = full.world.k.db.state.datasets.get(product.artifact_id)
    assert curated is not None and curated.parents == (product.raw_artifact_id,)
    used = full.world.k.db.state.graph.relations["used"]
    activity = qualified(f"investigation/{full.set_id}")
    assert (activity, qualified(product.raw_artifact_id.value)) in used
    assert (activity, qualified(product.artifact_id.value)) in used


@dataclass
class Cutting(FakeObservationArchive):
    """The synthetic plates, cut around a position; a product named in ``refuse`` cannot be cut."""

    refuse: tuple[str, ...] = ()
    cuts: list[tuple[str, float, float, float]] = field(default_factory=list)

    def cutout_product(self, product: ProductRef, ra_deg: float, dec_deg: float, side_arcsec: float) -> FetchedProduct:
        if product.product_id in self.refuse:
            raise LookupError(f"{product.product_id} is compressed")
        self.cuts.append((product.product_id, ra_deg, dec_deg, side_arcsec))
        got = self.fetch(product)
        return replace(got, product=cutout_ref(product, ra_deg, dec_deg, side_arcsec))


def cutting(*refuse: str) -> Cutting:
    plates = observation_archive()
    return Cutting(plates.archive_id, plates.release_label, plates.records, plates.products, refuse=refuse)


def test_a_cutout_selection_searches_the_field_once_and_shares_each_cutout(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    plates = cutting("lc-1")
    world = make_world(ProperMotionExcessTest(needs_products=LIGHTCURVES), plates=plates)
    seen = recorded_searches(world, monkeypatch)
    with caplog.at_level(logging.WARNING, logger="sdp.kernel.investigate"):
        run = world.run(profile=observations(cutout_arcsec=30.0, min_calibration_level=2))
    ((target, criteria),) = seen
    assert target == ObservationTarget("field", 120.0, -10.0)
    assert criteria == ObservationCriteria(radius_arcsec=1802.0, min_calibration_level=2)
    assert [c[0] for c in plates.cuts] == ["lc-0", "lc-2", "lc-3", "lc-4"]
    assert {c[1:] for c in plates.cuts} == {(120.0, -10.0, 30.0)}
    assert plates.fetches == ["lc-0", "lc-2", "lc-3", "lc-4"]
    refused = cutout_ref(ProductRef("fake-plates", "lc-1", "https://fake.invalid/lc-1.csv"), 120.0, -10.0, 30.0)
    (record,) = caplog.records
    assert record.getMessage() == "cutout not made"
    assert record.__dict__["product"] == refused.product_id and record.__dict__["error"] == "lc-1 is compressed"
    for bundle in run.bundles:
        assert [p.product.product_id for p in bundle.products] == [
            cutout_ref(ProductRef("fake-plates", f"lc-{i}", ""), 120.0, -10.0, 30.0).product_id for i in (0, 2, 3, 4)
        ]
        assert len(bundle.observation_indices) == 1
    assert run.investigation_set.stages[Stage.RETRIEVE].summary["products"] == 240


def test_cutouts_asked_of_a_gateway_that_cannot_cut_fetch_whole_products(caplog: pytest.LogCaptureFixture) -> None:
    world = make_world(ProperMotionExcessTest(needs_products=LIGHTCURVES))
    with caplog.at_level(logging.WARNING, logger="sdp.kernel.investigate"):
        world.run(profile=observations(cutout_arcsec=30.0))
    (record,) = caplog.records
    assert record.getMessage() == "cutouts asked of a gateway that cannot cut"
    assert record.__dict__["source"] == "fake-plates"
    assert world.plates.searches == 60 and sorted(world.plates.fetches) == [f"lc-{i}" for i in range(5)]


def test_an_observation_archive_without_a_gateway_is_refused() -> None:
    world = make_world()
    use_case(world).ports.observation_gateways = {}
    with pytest.raises(InvariantViolation, match=r"^no ObservationArchiveGateway for 'fake-plates' \(INV-004\)$"):
        world.run()


# --- products with an envelope -------------------------------------------------------------------

NAME = "jw99999-o001_t001_nircam_clear-f200w_i2d.fits"


def enveloped(
    data: bytes, normalizer: Any = None, *, listed: bool = True, cutout: CutoutSupport | None = None
) -> tuple[World, list[ProductEvidence]]:
    """One JWST image near a planted star, enveloped by the JWST dialect and the envelope validator."""
    star = next(r for r in synthetic_catalogue().to_pylist() if r["source_key"] == "gaia:DR3:0")
    ref = ProductRef("fake-plates", "jw99999-o001_t001_nircam_clear-f200w", f"https://example.invalid/{NAME}")
    record = ObservationRecord(ref, "JWST", "NIRCAM", "image", 60000.0, len(data), "public domain", "MAST")
    archive = FakeObservationArchive(
        "fake-plates", "mast:caom", (PlacedRecord(star["ra_deg"], star["dec_deg"], record),), {ref.product_id: data}
    )
    inventory = StaticProductInventory()
    if listed:
        template = raw_product(data, NAME)
        inventory.prime(
            ref, ProductRows(template.inventory_row, template.obscore_row, (), "inventory for " + NAME, cutout)
        )
    world = make_world(ProperMotionExcessTest(needs_products=("image",)), plates=archive)
    use_case(world).ports.product_envelopes = {
        "fake-plates": ProductEnvelopes(
            "mast-observations", "1.0.0", normalizer or JwstDialect(), EnvelopeValidator(), inventory
        )
    }
    run = world.run(profile=synthetic_profile(needs_products=("image",)))
    return world, [p for b in run.bundles for p in b.products]


def test_an_envelope_is_stored_beside_its_product_with_how_it_was_normalised() -> None:
    world, (product,) = enveloped(image(), FaithfulDialect())
    assert product.product.product_id == "jw99999-o001_t001_nircam_clear-f200w" and product.product_type == "image"
    assert product.read_from == f"https://example.invalid/{NAME}"
    raw, envelope = product.raw_artifact_id, product.envelope_artifact_id
    assert raw is not None and envelope is not None and product.artifact_id != raw
    assert world.k.store.media_type(envelope) == JSON
    doc = decode_artifact(world.k.store.get(envelope), JSON)
    assert doc["raw_artifact_id"] == raw.value and doc["curated_artifact_id"] == product.artifact_id.value
    graph = world.k.db.state.graph
    activity, node, raw_node = (
        qualified(f"normalisation/{envelope.value}"),
        qualified(envelope.value),
        qualified(raw.value),
    )
    assert graph.activities[activity] == {
        "sdp:type": "Normalisation",
        "sdp:dialect": "mast-jwst",
        "sdp:dialect_version": "1.0.0",
        "sdp:validator": "envelope-validator",
        "sdp:validator_version": "1.0.0",
        "sdp:passed": True,
    }
    assert graph.entities[node] == {"sdp:type": "ProductEnvelope", "sdp:product_type": "image"}
    assert (activity, raw_node) in graph.relations["used"]
    assert (node, activity) in graph.relations["wasGeneratedBy"]
    assert (node, raw_node) in graph.relations["wasDerivedFrom"]
    set_id = next(iter(world.k.db.state.events)).event.aggregate_id
    assert (qualified(f"investigation/{set_id}"), qualified(product.artifact_id.value)) in graph.relations["used"]
    query = world.k.kernel.query_investigations
    assert query is not None
    assert [i["product_id"] for i in query.images(set_id)] == ["jw99999-o001_t001_nircam_clear-f200w"]


def test_a_failing_envelope_is_never_curated_even_by_a_dialect_that_would() -> None:
    world, (product,) = enveloped(image(bunit="furlongs per fortnight squiggle"), FaithfulDialect())
    assert product.raw_artifact_id is None and product.envelope_artifact_id is not None
    activity = qualified(f"normalisation/{product.envelope_artifact_id.value}")
    assert world.k.db.state.graph.activities[activity]["sdp:passed"] is False
    assert world.k.db.state.datasets.children(product.artifact_id) == []


def test_cutout_support_read_per_product_fills_the_envelope() -> None:
    support = CutoutSupport(CutoutMethod.RANGE, "HTTP 206 for bytes=0-2879")
    world, (product,) = enveloped(image(), cutout=support)
    assert product.envelope_artifact_id is not None
    doc = decode_artifact(world.k.store.get(product.envelope_artifact_id), JSON)
    assert doc["metadata"]["cutout"] == support.to_document()
    other, (plain,) = enveloped(image())
    assert plain.envelope_artifact_id is not None
    assert "cutout" not in decode_artifact(other.k.store.get(plain.envelope_artifact_id), JSON)["metadata"]


def test_a_product_that_cannot_be_enveloped_is_kept_raw_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="sdp.kernel.investigate"):
        _, (product,) = enveloped(image(), listed=False)
    (record,) = caplog.records
    assert record.getMessage() == "product not enveloped"
    assert record.__dict__["artifact_id"] == product.artifact_id.value
    assert record.__dict__["error"] == f"'no inventory rows primed for https://example.invalid/{NAME}'"
    assert product.product_type == "image" and product.read_from == f"https://example.invalid/{NAME}"
    assert product.envelope_artifact_id is None and product.raw_artifact_id is None


# --- a run under an approved plan ----------------------------------------------------------------


def planned(*dropped: str) -> tuple[PlanningWorld, PlanGate]:
    """The planning world and a gate over its plan, approved without the items named."""
    world = planning_world(LIGHTCURVES)
    plan = world.plan()
    kept = tuple(i for i in plan.items if (i.source_id, i.target) not in {tuple(d.split(" ")) for d in dropped})
    cost = replace(plan.cost, total_bytes=sum(i.expected_bytes for i in kept))
    approved = replace(plan, approval=None, items=kept, cost=cost).decide(actor="leigh", ledger_entry=None)
    return world, PlanGate(approved)


def gated_run(world: PlanningWorld, gate: PlanGate | None, **kw: Any) -> InvestigationRun:
    uc = world.k.kernel.investigate_field
    assert uc is not None
    return uc.execute(InvestigateFieldCommand(world.field, world.profile, seed=11, gate=gate, **kw))


def item(gate: PlanGate, source_id: str) -> str:
    return next(i.item_id for i in gate.plan.items if i.source_id == source_id)


def test_a_planned_run_records_what_it_acquired_against_each_item() -> None:
    world, gate = planned()
    run = gated_run(world, gate)
    started = events(world.k, "InvestigationSetStarted")[0][2]
    assert started["plan_artifact_id"] == gate.plan.plan_id == run.investigation_set.plan_artifact_id
    gaia = next(a for b in run.bundles for s, a in b.catalogue_artifacts.items() if s == "synthetic-gaia")
    assert gate.acquired[item(gate, "synthetic-gaia")] == gaia.value
    products = {p.product.product_id: p.artifact_id.value for b in run.bundles for p in b.products}
    lc0 = next(i.item_id for i in gate.plan.items if i.target == "lc-0")
    assert gate.acquired[lc0] == products["lc-0"] and gate.refused == []
    assert gated_run(world, gate, set_id=run.investigation_set.set_id).investigation_set.complete


def test_a_resumed_planned_run_keeps_its_plan() -> None:
    world, gate = planned()
    set_id = gated_run(world, gate).investigation_set.set_id
    with pytest.raises(
        InvariantViolation, match=r"^a resumed run executes the plan it started under, and only that plan$"
    ):
        gated_run(world, None, set_id=set_id)


def test_identify_refuses_a_plan_without_its_catalogue_query() -> None:
    world, gate = planned()
    world, gate = planned(
        f"synthetic-gaia {next(i.target for i in gate.plan.items if i.source_id == 'synthetic-gaia')}"
    )
    message = (
        f"plan {gate.plan.plan_id} names no item for the synthetic-gaia query Identify needs; "
        "nothing is acquired that the plan does not name (INT-015)"
    )
    with pytest.raises(IntegrationRefused, match=rf"^{re.escape(message)}$"):
        gated_run(world, gate)
    assert failures(world.k) == [{"stage": "identify", "error": f"IntegrationRefused: {message}"}]


def test_a_catalogue_or_product_the_plan_does_not_name_is_left_out(caplog: pytest.LogCaptureFixture) -> None:
    world, gate = planned()
    twomass = next(i.target for i in gate.plan.items if i.source_id == "synthetic-2mass")
    world, gate = planned(f"synthetic-2mass {twomass}", "fake-plates lc-1")
    with caplog.at_level(logging.WARNING, logger="sdp.kernel.investigate"):
        run = gated_run(world, gate)
    (record,) = caplog.records
    assert record.getMessage() == "catalogue query refused by the plan"
    assert record.__dict__["source"] == "synthetic-2mass"
    assert not any(b.rows("synthetic-2mass") for b in run.bundles)
    assert sorted(world.plates.fetches) == ["lc-0", "lc-2", "lc-3", "lc-4"]
    assert [r["target"] for r in gate.refused] == [twomass, "lc-1"]


# --- the rest of a run's shape -------------------------------------------------------------------


def test_the_tests_run_as_a_plan_of_one_run_and_one_null_task_each(monkeypatch: pytest.MonkeyPatch) -> None:
    world = make_world()
    plans: list[Any] = []
    real = world.k.executor.execute

    def execute(plan: Any, registry: Any, **kw: Any) -> Any:
        plans.append((plan, kw["services"]))
        return real(plan, registry, **kw)

    monkeypatch.setattr(world.k.executor, "execute", execute)
    world.run()
    ((plan, services),) = plans
    assert plan.outputs == ("run:pm-excess@1.0.0", "null:pm-excess@1.0.0")
    assert [(t.task_id, t.policy) for t in plan.tasks] == [
        ("run:pm-excess@1.0.0", PM_POLICY),
        ("null:pm-excess@1.0.0", PM_POLICY),
    ]
    assert plan.tasks[1].params["trials"] == 5 and plan.tasks[1].params["alpha"] == 0.1
    assert services[PRODUCTS_SERVICE] == {}


def test_a_ranking_policy_without_a_ranker_names_what_is_registered() -> None:
    profile = replace(synthetic_profile(), ranking=RankingPolicy("unheard-of", "1.0.0", 0.5))
    message = (
        "no ranker for ranking policy 'unheard-of'; registered: ['significance'] "
        "(science-module rankers come with the science extra, e.g. sdp-cli[science])"
    )
    with pytest.raises(PlanInvalid, match=rf"^{re.escape(message)}$"):
        make_world().run(profile=profile)


def test_a_resume_after_the_last_stage_failed_reads_the_others_back() -> None:
    world = make_world()
    uc = use_case(world)
    uc.ports.ranker = BrokenRanker()
    with pytest.raises(RuntimeError):
        world.run()
    set_id = failed_set(world).set_id
    executed = list(world.k.executor.executed)
    uc.ports.ranker = None
    run = world.run(set_id=set_id)
    assert world.k.executor.executed == executed, "the tests were read back, not run again"
    stages = [p["stage"] for _, _, p in events(world.k, "InvestigationStageCompleted")]
    assert stages == ["identify", "retrieve", "test", "candidates"]
    again = world.run(set_id=set_id)
    assert [i.investigation_id for i in again.investigations] == [i.investigation_id for i in run.investigations]
    assert len(again.investigations) == 5


def test_a_profile_that_does_not_resolve_leaves_the_resolver_alone() -> None:
    profile = synthetic_profile()
    quiet = replace(profile, identify=replace(profile.identify, resolve=False))
    run = make_world().run(profile=quiet)
    assert run.investigation_set.stages[Stage.IDENTIFY].summary["resolved"] == 0
    assert "simbad:fixture" not in run.investigation_set.archive_releases


def test_archives_are_read_whatever_order_the_profile_lists_them_in(caplog: pytest.LogCaptureFixture) -> None:
    world = make_world(ProperMotionExcessTest(needs_products=("astrometry",)))
    profile = synthetic_profile(needs_products=("astrometry",))
    with caplog.at_level(logging.WARNING, logger="sdp.kernel.investigate"):
        run = world.run(profile=replace(profile, archives=tuple(reversed(profile.archives))))
    assert caplog.records == []
    assert len([b for b in run.bundles if b.rows("synthetic-2mass")]) == 30
    assert sorted(world.plates.fetches) == [f"pos-{i}" for i in range(5)]
    bundle = next(b for b in run.bundles if b.object_key == "gaia:DR3:0")
    assert bundle.releases == ("dasch:DR7", "gaia:DR3", "twomass:psc")
    activity = qualified(f"investigation/{run.investigation_set.set_id}")
    used = world.k.db.state.graph.relations["used"]
    for artifact in (*bundle.catalogue_artifacts.values(), *bundle.observation_artifacts):
        assert (activity, qualified(artifact.value)) in used


def test_a_gateway_that_can_cut_fetches_whole_products_when_no_cutout_is_asked() -> None:
    plates = cutting()
    world = make_world(ProperMotionExcessTest(needs_products=LIGHTCURVES), plates=plates)
    world.run(profile=synthetic_profile(needs_products=LIGHTCURVES))
    assert plates.searches == 60 and plates.cuts == []
    assert sorted(plates.fetches) == [f"lc-{i}" for i in range(5)]

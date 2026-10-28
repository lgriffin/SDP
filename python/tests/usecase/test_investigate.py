"""T4: InvestigateField, triage and promotion, with the kernel wired to fakes (INV-001..010, INV-013)."""

from __future__ import annotations

from dataclasses import dataclass, replace

import pyarrow as pa
import pytest

from sdp.kernel.application.investigate import SignificanceRanker
from sdp.kernel.application.media import JSON, decode_artifact
from sdp.kernel.domain.dataset import DatasetStatus
from sdp.kernel.domain.errors import (
    CurationBlocked,
    InvalidTransition,
    PlanInvalid,
    PredeclarationNotFrozen,
    PromotionRefused,
)
from sdp.kernel.domain.gates import Comparator, Gate
from sdp.kernel.domain.hypothesis import Hypothesis
from sdp.kernel.domain.investigation import (
    EXPLORATORY,
    Field,
    FieldImage,
    IdentificationMethod,
    IdentifyRoute,
    InvestigationState,
    RankingPolicy,
    Stage,
    StageState,
    TestSelection,
)
from sdp.kernel.domain.predeclaration import Predeclaration
from sdp.kernel.ports.driving import FreezePredeclarationCommand
from sdp.kernel.ports.investigate import Detection, InvestigateFieldCommand, InvestigationRun, TriageCommand
from sdp.testing.fakes.archives import FakeArchive
from sdp.testing.fakes.investigate import (
    FakeObservationArchive,
    NearestWcsRefiner,
    PlantedDetector,
    ThinningDialect,
    tan_header,
)
from sdp.testing.investigate import (
    PLANTED,
    ProperMotionExcessTest,
    gaia_archive,
    investigate_ports,
    observation_archive,
    synthetic_catalogue,
    synthetic_field,
    synthetic_profile,
    twomass_archive,
)
from sdp.testing.wiring import InMemoryKernel, build_in_memory_kernel

pytestmark = pytest.mark.tier("T4")


@dataclass
class World:
    k: InMemoryKernel
    gaia: FakeArchive
    twomass: FakeArchive
    plates: FakeObservationArchive

    def run(self, **kw: object) -> InvestigationRun:
        field = kw.pop("field", synthetic_field())
        profile = kw.pop("profile", synthetic_profile())
        assert self.k.kernel.investigate_field is not None
        return self.k.kernel.investigate_field.execute(
            InvestigateFieldCommand(field, profile, seed=11, **kw)  # type: ignore[arg-type]
        )

    def events(self) -> list[str]:
        return [e.event.name for e in self.k.db.state.events]

    def triage(self, investigation_id: str, action: str, **kw: object) -> object:
        assert self.k.kernel.triage_investigation is not None
        return self.k.kernel.triage_investigation.execute(
            TriageCommand(investigation_id, action, actor="leigh", **kw)  # type: ignore[arg-type]
        )


def make_world(*tests: object, plates: FakeObservationArchive | None = None, **ports: object) -> World:
    catalogue = synthetic_catalogue()
    gaia, twomass = gaia_archive(catalogue), twomass_archive(catalogue)
    plates = plates or observation_archive(catalogue)
    k = build_in_memory_kernel(
        gateways={"synthetic-gaia": gaia, "synthetic-2mass": twomass},
        investigate=investigate_ports(*tests, catalogue=catalogue, observations=plates, **ports),  # type: ignore[arg-type]
    )
    return World(k, gaia, twomass, plates)


@pytest.fixture
def world() -> World:
    return make_world()


# -- the whole interaction --------------------------------------------------------------------------


@pytest.mark.req("INV-007")
def test_one_investigation_per_candidate_and_the_set_records_what_was_examined(world: World) -> None:
    run = world.run()
    assert sorted(i.candidate.object_key or "" for i in run.investigations) == sorted(PLANTED)
    assert all(i.state is InvestigationState.OPEN for i in run.investigations)
    assert run.manifest["multiplicity"] == {
        "objects_examined": 60,
        "tests_applied": 1,
        "comparisons": 60,
        "candidates_opened": 5,
    }
    assert len(run.investigation_set.objects_examined) == 60
    assert run.investigation_set.complete
    assert world.events().count("InvestigationOpened") == 5
    stages = [
        e.event.payload["stage"] for e in world.k.db.state.events if e.event.name == "InvestigationStageCompleted"
    ]
    assert stages == ["identify", "retrieve", "test", "candidates"]


@pytest.mark.req("INV-001", "INV-002")
def test_the_manifest_records_field_profile_hash_releases_revision_and_seed(world: World) -> None:
    profile = synthetic_profile()
    run = world.run(profile=profile)
    m = run.manifest
    assert m["profile_hash"] == profile.profile_hash and m["profile"] == profile.to_document()
    assert m["field_id"] == synthetic_field().field_id
    assert m["seed"] == 11 and m["code_revision"]
    assert {"gaia:DR3", "twomass:psc", "dasch:DR7", "simbad:fixture"} <= set(m["archive_releases"])
    assert m["label"] == EXPLORATORY
    assert m["tests"][0]["name"] == "pm-excess" and m["tests"][0]["null_model"]
    assert world.k.kernel.query_investigations is not None
    stored = world.k.kernel.query_investigations.manifest(run.investigation_set.set_id)
    assert stored == m
    started = next(e.event for e in world.k.db.state.events if e.event.name == "InvestigationSetStarted")
    assert started.payload["profile_hash"] == profile.profile_hash


@pytest.mark.req("INV-001")
def test_a_changed_profile_is_a_new_run_not_a_resume(world: World) -> None:
    run = world.run()
    changed = synthetic_profile(version="1.0.1")
    with pytest.raises(Exception, match="new version and a new run"):
        world.run(profile=changed, set_id=run.investigation_set.set_id)


@pytest.mark.req("INV-006")
def test_only_registered_tests_with_a_null_model_can_run(world: World) -> None:
    unknown = replace(synthetic_profile(), tests=(TestSelection("not-registered", "1.0.0"),))
    with pytest.raises(PlanInvalid, match="not registered"):
        world.run(profile=unknown)
    assert "InvestigationSetStarted" not in world.events()
    with pytest.raises(Exception, match="null model"):
        make_world(ProperMotionExcessTest(null_model=" "))


@pytest.mark.req("INV-007")
def test_a_ranking_policy_without_a_ranker_is_refused_before_anything_runs(world: World) -> None:
    unknown = replace(synthetic_profile(), ranking=RankingPolicy("unheard-of", "1.0.0", 0.5))
    with pytest.raises(PlanInvalid, match="no ranker for ranking policy 'unheard-of'"):
        world.run(profile=unknown)
    assert "InvestigationSetStarted" not in world.events()


@pytest.mark.req("INV-007")
def test_the_ranking_policy_picks_its_ranker() -> None:
    class Everyone:
        ranker_id = "everyone"

        def rank(self, results: object, policy: RankingPolicy) -> list[object]:
            return [replace(c, score=1.0) for c in SignificanceRanker().rank(results, policy)]  # type: ignore[arg-type]

    world = make_world(rankers={"everyone": Everyone()})
    run = world.run(profile=replace(synthetic_profile(), ranking=RankingPolicy("everyone", "1.0.0", 1.0)))
    assert len(run.investigations) == 60
    assert {i.rank.policy_id for i in run.investigations} == {"everyone"}


@pytest.mark.req("INV-003")
def test_catalogue_first_objects_carry_their_identification_records(world: World) -> None:
    run = world.run()
    gaia_artifacts = {
        d.artifact_id for d in world.k.db.state.datasets.list() if d.source_ref.identity == "synthetic-gaia"
    }
    for b in run.bundles:
        o = b.object
        assert o.method is IdentificationMethod.CATALOGUE
        assert o.gaia_source_key == o.object_key
        assert set(o.source_artifacts) <= gaia_artifacts
    resolved = [b.object for b in run.bundles if b.object.resolver_record]
    assert [o.object_key for o in resolved] == ["gaia:DR3:1"]
    inv = next(i for i in run.investigations if i.candidate.object_key == "gaia:DR3:1")
    assert inv.links["simbad_id"] == "HD 999999"


@pytest.mark.req("INV-003")
def test_image_first_detections_cross_identify_with_gaia_and_keep_unmatched_sources() -> None:
    rows = synthetic_catalogue().to_pylist()
    image_bytes = b"SIMPLE  = T / synthetic frame"
    world = make_world()
    image = world.k.store.put(image_bytes, "application/fits")
    offset = 0.6 / 3600  # a WCS zero-point error the refiner removes
    dets = [Detection(10.0 + i, 20.0 + i, r["ra_deg"] + offset, r["dec_deg"], 1000.0) for i, r in enumerate(rows[:8])]
    dets.append(Detection(500.0, 500.0, 120.05, -10.05, 50.0))  # no Gaia counterpart
    world.k.kernel.investigate_field.ports.detector = PlantedDetector(  # type: ignore[union-attr]
        {image.value: dets}, {image.value: tan_header(120.0, -10.0)}
    )
    world.k.kernel.investigate_field.ports.refiner = NearestWcsRefiner()  # type: ignore[union-attr]
    field = Field(synthetic_field().region, images=(FieldImage(image),))
    run = world.run(field=field, profile=synthetic_profile(route=IdentifyRoute.IMAGE, with_observations=False))
    objects = {b.object.object_key: b.object for b in run.bundles}
    assert len(objects) == 9
    matched = [o for o in objects.values() if o.gaia_source_key]
    assert {o.object_key for o in matched} == {r["source_key"] for r in rows[:8]}
    for o in matched:
        assert o.method is IdentificationMethod.DETECTION
        assert o.pixel_xy is not None and o.wcs_artifact is not None and o.image_artifact == image
        assert o.match_distance_arcsec is not None and o.match_distance_arcsec < 0.05  # after refinement
    unmatched = [o for o in objects.values() if o.unmatched]
    assert len(unmatched) == 1 and unmatched[0].gaia_source_key is None
    assert unmatched[0].wcs_artifact is not None
    wcs = world.k.store.get(unmatched[0].wcs_artifact)
    assert b"nearest-shift" in wcs


# -- Retrieve ------------------------------------------------------------------------------------


@pytest.mark.req("INV-004", "INV-005")
def test_retrieve_goes_through_the_gateways_and_registers_everything_first(world: World) -> None:
    run = world.run()
    registered = {d.artifact_id: d for d in world.k.db.state.datasets.list()}
    for b in run.bundles:
        for source, artifact in b.catalogue_artifacts.items():
            assert artifact in registered and registered[artifact].source_ref.identity == source
        assert len(b.observation_artifacts) == 1
        index_artifact = registered[b.observation_artifacts[0]]
        assert index_artifact.status is DatasetStatus.ACQUIRED
        assert index_artifact.source_ref.licence and index_artifact.source_ref.citation
        assert index_artifact.source_ref.release_label == "dasch:DR7"
    assert world.plates.searches == 60  # the observation index is cheap and fetched for every object
    assert world.gaia.fetch_calls >= 1 and world.twomass.fetch_calls == 1
    with_ir = [b for b in run.bundles if b.rows("synthetic-2mass")]
    assert len(with_ir) == 30


@pytest.mark.req("INV-004")
def test_products_are_fetched_only_when_a_test_declares_it_needs_them(world: World) -> None:
    world.run()
    assert world.plates.fetches == []
    needing = make_world(ProperMotionExcessTest(needs_products=("lightcurve",)))
    run = needing.run(profile=synthetic_profile(needs_products=("lightcurve",)))
    assert sorted(needing.plates.fetches) == sorted(f"lc-{i}" for i in range(5))
    with_products = [b for b in run.bundles if b.products]
    assert {b.object_key for b in with_products} == set(PLANTED)


@pytest.mark.req("INV-004")
def test_a_product_offered_as_two_types_is_fetched_once() -> None:
    world = make_world(ProperMotionExcessTest(needs_products=("lightcurve", "astrometry")))
    # Every light curve is also offered as a position history, as DASCH offers its light curves.
    world.plates.records += tuple(
        replace(p, record=replace(p.record, product_type="astrometry"))
        for p in world.plates.records
        if p.record.product_type == "lightcurve"
    )
    run = world.run(profile=synthetic_profile(needs_products=("lightcurve", "astrometry")))
    light_curves = [f for f in world.plates.fetches if f.startswith("lc-")]
    assert sorted(light_curves) == sorted(f"lc-{i}" for i in range(5))
    as_positions = {p.product.product_id for b in run.bundles for p in b.products if p.product_type == "astrometry"}
    assert {f"lc-{i}" for i in range(5)} <= as_positions


@pytest.mark.req("INV-013")
def test_cloud_uris_and_checksums_of_bytes_actually_read_are_recorded() -> None:
    world = make_world(ProperMotionExcessTest(needs_products=("lightcurve",)))
    run = world.run(profile=synthetic_profile(needs_products=("lightcurve",)))
    product = next(p for b in run.bundles for p in b.products if p.product.product_id == "lc-0")
    assert product.read_from == "s3://fake-bucket/lc-0.csv" == product.product.cloud_uri
    artifact = world.k.db.state.datasets.get(product.artifact_id)
    assert artifact is not None and artifact.acquisition is not None
    assert artifact.acquisition.rendered_query == "GET s3://fake-bucket/lc-0.csv"
    assert artifact.acquisition.checksum == product.artifact_id
    assert world.k.store.get(product.artifact_id) == world.plates.products["lc-0"]


@pytest.mark.req("INV-005")
def test_a_product_dialect_registers_a_curated_child_and_tests_read_it() -> None:
    world = make_world(
        ProperMotionExcessTest(needs_products=("lightcurve",)), product_dialects={"fake-plates": ThinningDialect()}
    )
    run = world.run(profile=synthetic_profile(needs_products=("lightcurve",)))
    product = next(p for b in run.bundles for p in b.products if p.product.product_id == "lc-1")
    assert product.raw_artifact_id is not None and product.artifact_id != product.raw_artifact_id
    raw = world.k.db.state.datasets.get(product.raw_artifact_id)
    curated = world.k.db.state.datasets.get(product.artifact_id)
    assert raw is not None and curated is not None
    assert raw.status is DatasetStatus.ACQUIRED and curated.status is DatasetStatus.CURATED
    assert curated.parents == (raw.artifact_id,)
    assert curated.dialect is not None and curated.dialect.dialect_id == "fixture-thinning"
    assert world.k.store.get(product.raw_artifact_id) == world.plates.products["lc-1"]
    raw_rows = world.plates.products["lc-1"].decode().splitlines()
    stamp, *rows = world.k.store.get(product.artifact_id).decode().splitlines()
    assert rows == [raw_rows[0], *raw_rows[1::2]]
    # Equal rows curated from another raw product would be another artifact, with its own lineage.
    assert stamp == f"# sdp.dialect=fixture-thinning@1.0.0 sdp.raw_parent={raw.artifact_id.value}"
    resumed = world.run(profile=synthetic_profile(needs_products=("lightcurve",)), set_id=run.investigation_set.set_id)
    assert resumed.bundles == run.bundles


def test_a_dialect_that_changes_nothing_leaves_the_raw_product_to_be_read() -> None:
    world = make_world(
        ProperMotionExcessTest(needs_products=("lightcurve",)),
        product_dialects={"fake-plates": ThinningDialect(every=1)},
    )
    run = world.run(profile=synthetic_profile(needs_products=("lightcurve",)))
    for p in (p for b in run.bundles for p in b.products):
        assert p.raw_artifact_id is None
        assert world.k.db.state.datasets.get(p.artifact_id).status is DatasetStatus.ACQUIRED  # type: ignore[union-attr]


@pytest.mark.req("INV-005")
def test_an_archive_without_a_licence_blocks_retrieval_before_any_test_runs() -> None:
    plates = observation_archive()
    plates.licence = ""
    world = make_world(plates=plates)
    with pytest.raises(CurationBlocked):
        world.run()
    s = world.k.kernel.query_investigations.sets()[0]  # type: ignore[union-attr]
    assert s.stages[Stage.IDENTIFY].state is StageState.COMPLETED
    assert s.stages[Stage.RETRIEVE].state is StageState.FAILED
    assert s.stages[Stage.TEST].state is StageState.PENDING
    assert world.k.store.resolve(f"investigations/{s.set_id}/tasks/run:pm-excess@1.0.0.json") is None


def test_a_failed_stage_keeps_earlier_work_and_a_resume_completes_it() -> None:
    plates = observation_archive()
    plates.licence = ""
    world = make_world(plates=plates)
    with pytest.raises(CurationBlocked):
        world.run()
    set_id = world.k.kernel.query_investigations.sets()[0].set_id  # type: ignore[union-attr]
    gaia_calls = world.gaia.fetch_calls
    plates.licence = "fixture licence"
    run = world.run(set_id=set_id)
    assert run.investigation_set.set_id == set_id and run.investigation_set.complete
    assert len(run.investigations) == 5
    assert world.gaia.fetch_calls == gaia_calls  # Identify was read back, Gaia not queried again
    assert "InvestigationStageFailed" in world.events()


def test_tests_read_bundles_only_and_resume_from_checkpoints(world: World) -> None:
    run = world.run()
    set_id = run.investigation_set.set_id
    for name in ("run:pm-excess@1.0.0", "null:pm-excess@1.0.0"):
        assert world.k.store.resolve(f"investigations/{set_id}/tasks/{name}.json") is not None
    assert world.k.executor.executed == ["run:pm-excess@1.0.0", "null:pm-excess@1.0.0"]
    null = run.investigation_set.null_results[0]
    assert null.trials == 5 and null.examined == 300


# -- Candidates and triage ------------------------------------------------------------------------


@pytest.mark.req("INV-008")
def test_investigations_and_their_dossiers_are_exploratory(world: World) -> None:
    run = world.run()
    for inv in run.investigations:
        assert inv.label == EXPLORATORY and "verdict" not in inv.to_document()
        assert all(r.label == EXPLORATORY for r in inv.test_results)
        dossier = world.k.kernel.query_investigations.dossier(inv.investigation_id)  # type: ignore[union-attr]
        assert dossier is not None and dossier["label"] == EXPLORATORY
        assert dossier["set_false_positive_rates"]["pm-excess@1.0.0"]["trials"] == 5
        assert dossier["objects"][0]["observation_index"][0]["reach_back_jyear"] < 1900
        assert dossier["test_results"][0]["null"] is not None
    assert all(not e.startswith("Verdict") for e in world.events())


def frozen_confirmatory(world: World, *evidence: dict[str, str]) -> str:
    predeclaration = Predeclaration(
        predeclaration_id="pre-confirm-group",
        protocol_id="uc01-stellar",
        protocol_version="1.0.0",
        title="Confirm the co-moving group with Gaia DR4 epochs",
        hypotheses=(Hypothesis("H-1", 1, "the group shares a space motion"),),
        gates=(Gate("STL-G-09", "membership", "membership", Comparator.GE, 0.5),),
        parameters={"confirmatory_evidence": list(evidence)},
    )
    frozen, _ = world.k.kernel.freeze_predeclaration.execute(FreezePredeclarationCommand(predeclaration, "leigh"))
    return frozen.hash


@pytest.mark.req("INV-009")
def test_promotion_without_a_frozen_predeclaration_is_refused_and_recorded(world: World) -> None:
    inv = world.run().investigations[0]
    with pytest.raises(PredeclarationNotFrozen):
        world.triage(inv.investigation_id, "promote", predeclaration_hash="sha256:" + "0" * 64)
    with pytest.raises(PromotionRefused):
        world.triage(inv.investigation_id, "promote")
    refusals = [e.event for e in world.k.db.state.events if e.event.name == "InvestigationPromotionRefused"]
    assert len(refusals) == 2 and refusals[0].aggregate_id == inv.investigation_id
    stored = world.k.kernel.query_investigations.investigation(inv.investigation_id)  # type: ignore[union-attr]
    assert stored is not None and stored.state is InvestigationState.OPEN


@pytest.mark.req("INV-009")
def test_promotion_needs_evidence_the_investigation_did_not_use(world: World) -> None:
    inv = world.run().investigations[0]
    used = frozen_confirmatory(world, {"kind": "release", "value": "gaia:DR3"})
    with pytest.raises(PromotionRefused, match="INV-009"):
        world.triage(inv.investigation_id, "promote", predeclaration_hash=used)
    assert world.events().count("InvestigationPromotionRefused") == 1
    fresh = frozen_confirmatory(world, {"kind": "release", "value": "gaia:DR4"})
    promoted = world.triage(inv.investigation_id, "promote", predeclaration_hash=fresh, experiment_id="exp-1")
    assert promoted.state is InvestigationState.PROMOTED  # type: ignore[attr-defined]
    assert "InvestigationPromoted" in world.events()
    with pytest.raises(InvalidTransition):
        world.triage(inv.investigation_id, "annotate", reason="edit after promotion")


@pytest.mark.req("INV-010")
def test_parked_and_closed_investigations_keep_their_dossiers_and_reasons(world: World) -> None:
    run = world.run()
    a, b = run.investigations[0], run.investigations[1]
    world.triage(a.investigation_id, "park", reason="revisit when Gaia DR4 lands")
    world.triage(b.investigation_id, "close", reason="artefact at triage")
    q = world.k.kernel.query_investigations
    assert q is not None
    kept = {i.investigation_id: i for i in q.investigations(run.investigation_set.set_id)}
    assert len(kept) == 5
    assert kept[a.investigation_id].state is InvestigationState.PARKED
    assert kept[a.investigation_id].transitions[-1].reason == "revisit when Gaia DR4 lands"
    assert kept[b.investigation_id].state is InvestigationState.CLOSED
    for inv_id in (a.investigation_id, b.investigation_id):
        assert q.dossier(inv_id) is not None
    with pytest.raises(InvalidTransition):
        world.triage(b.investigation_id, "reopen", reason="closed is final")


def test_lineage_runs_from_an_investigation_back_to_the_archive_rows(world: World) -> None:
    run = world.run()
    inv = run.investigations[0]
    lineage = world.k.kernel.query_evidence.lineage(inv.investigation_id)
    gaia = next(d for d in world.k.db.state.datasets.list() if d.source_ref.identity == "synthetic-gaia" and d.dialect)
    assert any(gaia.artifact_id.value in node for node in lineage)


def test_an_empty_field_opens_nothing_but_still_records_the_set() -> None:
    world = make_world()
    world.gaia.table = synthetic_catalogue().slice(0, 0)
    empty: pa.Table = world.gaia.table
    assert empty.num_rows == 0
    run = world.run()
    assert run.investigations == () and run.manifest["multiplicity"]["objects_examined"] == 0
    candidates = run.investigation_set.stages[Stage.CANDIDATES].artifact_id
    assert candidates is not None
    assert decode_artifact(world.k.store.get(candidates), JSON) == {"investigations": []}

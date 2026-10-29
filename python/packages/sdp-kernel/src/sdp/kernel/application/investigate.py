"""InvestigateField, TriageInvestigation and QueryInvestigations (Investigate §1–§6, INV-001..010).

Four stages over a declared field under a hashed profile: Identify and Retrieve are the only
stages that reach archives; Test reads the evidence bundles they produced; Candidates opens one
Investigation per candidate above the ranking threshold. Each stage writes its output to the
artifact store under ``investigations/<set_id>/`` and emits a domain event, so a failed stage
leaves everything before it intact and a resumed run reads completed stages back.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from importlib.metadata import entry_points
from typing import Any

import astropy.units as u
import pyarrow as pa

from sdp.kernel.application.acquisition import AcquireDatasetUseCase
from sdp.kernel.application.media import JSON, PARQUET, decode_artifact
from sdp.kernel.domain import events as ev
from sdp.kernel.domain.archive import CatalogueQuery, Cone, idempotency_key
from sdp.kernel.domain.canonical import canonical_json, hash_document
from sdp.kernel.domain.dataset import (
    ADMITTABLE_PRODUCT_TYPES,
    AcquisitionRecord,
    DatasetArtifact,
    DatasetStatus,
    DialectRef,
    ProductUse,
)
from sdp.kernel.domain.errors import (
    CurationBlocked,
    IntegrityError,
    InvalidTransition,
    InvariantViolation,
    PlanInvalid,
    PredeclarationNotFrozen,
    PromotionRefused,
)
from sdp.kernel.domain.ids import ArtifactId
from sdp.kernel.domain.integration import (
    IntegrationRefused,
    Operation,
    PlanGate,
    PlanItem,
    ScientificDataProduct,
    SemanticValidationReport,
)
from sdp.kernel.domain.investigation import (
    EXPLORATORY,
    UNMATCHED,
    ArchiveRole,
    ArchiveSelection,
    EvidenceBundle,
    EvidenceUsed,
    FetchedProduct,
    Field,
    IdentificationMethod,
    IdentifiedObject,
    IdentifyRoute,
    Investigation,
    InvestigationSet,
    NullFieldResult,
    ObjectSet,
    ObservationCriteria,
    ObservationIndex,
    ProductEvidence,
    ProductRef,
    RankedCandidate,
    RankingPolicy,
    Stage,
    StageState,
    TestProfile,
    TestResult,
    rank_by_significance,
)
from sdp.kernel.domain.provenance import ProvGraph
from sdp.kernel.domain.sources import SourceKind, SourceRef
from sdp.kernel.ports.archives import CrossMatchService
from sdp.kernel.ports.compute import ComputeExecutor, EnvironmentProbe
from sdp.kernel.ports.determinism import Clock, IdGenerator
from sdp.kernel.ports.driving import AcquireDatasetCommand
from sdp.kernel.ports.integration import ProductInventory, ProductNormalizer, ProductValidator, RawProduct
from sdp.kernel.ports.investigate import (
    CandidateRanker,
    CuratedProduct,
    ImageFrame,
    InvestigateFieldCommand,
    InvestigationRun,
    ObjectResolver,
    ObservationArchiveGateway,
    ObservationTarget,
    ProductCutouts,
    ProductDialect,
    SourceDetector,
    TriageCommand,
    WcsRefiner,
    cutout_product_id,
)
from sdp.kernel.ports.ledger import Ledger
from sdp.kernel.ports.repositories import UnitOfWork
from sdp.kernel.ports.storage import ArtifactStore
from sdp.kernel.science.investigation import (
    PRODUCTS_SERVICE,
    TEST_ENTRY_POINT_GROUP,
    InvestigationTestRegistry,
    describe,
    null_task_kind,
    task_kind,
)
from sdp.kernel.science.plan import Backend, ComputePlan, ComputeTask

log = logging.getLogger("sdp.kernel.investigate")

TEXT = "text/plain"


def set_path(set_id: str, name: str) -> str:
    return f"investigations/{set_id}/{name}"


def identify_query(field_: Field, profile: TestProfile) -> CatalogueQuery:
    """The Gaia cone Identify reads (and Retrieve reads again, resumed from the same artifact)."""
    spec = profile.identify
    return CatalogueQuery(
        source_id=spec.source_id,
        release_label=spec.release_label,
        region=field_.query_region(0.0),
        columns=spec.columns,
        filters=spec.quality_cuts,
        row_cap=spec.row_cap,
        chunk_rows=min(spec.row_cap, 50_000),
    )


#: The stage summary key naming each source whose read filled its row cap, with the cap (#59)
ROW_CAP_REACHED = "row_cap_reached"

CATALOGUE_ROWS = 50_000
"""The most rows Retrieve reads from a catalogue archive, in a single call."""


def catalogue_query(field_: Field, selection: ArchiveSelection) -> CatalogueQuery:
    """The cone a catalogue archive is read over: the field plus its match radius."""
    return CatalogueQuery(
        source_id=selection.source_id,
        release_label=selection.release_label,
        region=field_.query_region(selection.match_radius_arcsec / 3600.0),
        columns=selection.columns,
        # One page: within every declared source's per-call limit, and readable by a source that cannot page.
        row_cap=CATALOGUE_ROWS,
        chunk_rows=CATALOGUE_ROWS,
    )


def observation_target(key: str, ra_deg: float, dec_deg: float, rows: Sequence[Mapping[str, Any]]) -> ObservationTarget:
    """An object as an observation search sees it, with the motion and epoch of its Gaia row if any."""
    row = rows[0] if rows else {}
    motion = [row.get(c) for c in ("pm_ra_cosdec_masyr", "pm_dec_masyr", "epoch_jyear")]
    numbers = [float(v) for v in motion if isinstance(v, int | float) and math.isfinite(v)]
    if len(numbers) < len(motion):
        return ObservationTarget(key, ra_deg, dec_deg)
    pm_ra, pm_dec, epoch = numbers
    return ObservationTarget(key, ra_deg, dec_deg, pm_ra, pm_dec, epoch)


def query_operation(query: CatalogueQuery) -> Operation:
    return Operation.CONE if isinstance(query.region, Cone) else Operation.REGION


def query_target(query: CatalogueQuery) -> str:
    """How a plan item names a catalogue query: the hash of the query document, so the executor
    authorises exactly the query that was planned."""
    return hash_document(query.to_document())


def field_search_cone(field_: Field) -> tuple[float, float, float]:
    """The centre of a field and the radius (arcsec) that covers it: where a field-wide search looks."""
    region = field_.query_region()
    if isinstance(region, Cone):
        return region.ra_deg, region.dec_deg, region.radius_deg * 3600.0
    ra, dec = region.centre
    width = (region.ra_max_deg - region.ra_min_deg) * math.cos(math.radians(dec))
    return ra, dec, math.hypot(width, region.dec_max_deg - region.dec_min_deg) / 2 * 3600.0


def cutout_ref(product: ProductRef, ra_deg: float, dec_deg: float, side_arcsec: float) -> ProductRef:
    """The reference a cutout of ``product`` goes by: its own id, read from the product's URL."""
    return replace(product, product_id=cutout_product_id(product.product_id, ra_deg, dec_deg, side_arcsec))


def mjd_to_jyear(mjd: float) -> float:
    return 2000.0 + (mjd - 51544.5) / 365.25


def _warn_if_product_lost(existing: DatasetArtifact, product: ProductUse | None) -> None:
    """Records are immutable (KRN-014): bytes first registered without a product use keep none."""
    if product is not None and existing.product != product:
        log.warning(
            "artifact already registered with another product use",
            extra={"artifact_id": existing.artifact_id.value, "had": str(existing.product), "offered": str(product)},
        )


@dataclass(frozen=True)
class ProductEnvelopes:
    """How one archive's products get their ScientificDataProduct envelope (Integration v0.4.0 §3).

    ``adapter`` and ``adapter_version`` are what admission is checked under (an
    ``AdapterCompatibilityRecord``'s ``adapter`` and ``adapter_version``); they are stamped on each
    raw product so the manifest can name them (INT-016). ``missions``, when set, limits the envelopes
    to records of those missions (MAST serves every mission; the JWST dialect reads only JWST); other
    records take the archive's ordinary product path.
    """

    adapter: str
    adapter_version: str
    normalizer: ProductNormalizer
    validator: ProductValidator
    inventory: ProductInventory
    missions: frozenset[str] = frozenset()

    def covers(self, mission: str) -> bool:
        return not self.missions or mission.strip().upper() in {m.upper() for m in self.missions}


@dataclass
class InvestigatePorts:
    """The driven ports Investigate adds to the kernel's own."""

    tests: InvestigationTestRegistry
    cross_match: CrossMatchService
    observation_gateways: Mapping[str, ObservationArchiveGateway] = field(default_factory=dict)
    detector: SourceDetector | None = None
    refiner: WcsRefiner | None = None
    resolver: ObjectResolver | None = None
    ranker: CandidateRanker | None = None
    services: Mapping[str, Any] = field(default_factory=dict)
    """Kernel ports a test may use (for example ``clusterer``: a ClusterSearchAlgorithm)."""
    product_dialects: Mapping[str, ProductDialect] = field(default_factory=dict)
    """Dialects that curate an archive's native products into canonical ones, by archive source id."""
    rankers: Mapping[str, CandidateRanker] = field(default_factory=dict)
    """Candidate rankers by ranking policy id; ``significance`` is the kernel's own. ``ranker``, when
    set, overrides the choice for every policy (fixtures)."""
    product_envelopes: Mapping[str, ProductEnvelopes] = field(default_factory=dict)
    """Normaliser, validator and inventory by archive source id. A source listed here has every
    product normalised and validated before any curation, and curation is refused when either fails
    (INT-006, INT-007); its normaliser is also its product dialect."""


def load_investigation_tests(group: str = TEST_ENTRY_POINT_GROUP) -> InvestigationTestRegistry:
    """Every test registered by an installed package under ``group``."""
    registry = InvestigationTestRegistry()
    for ep in entry_points(group=group):
        for test in ep.load()():
            registry.register(test)
    return registry


SIGNIFICANCE_POLICY = "significance"


RANKER_ENTRY_POINT_GROUP = "sdp.candidate_rankers"


def load_candidate_rankers(group: str = RANKER_ENTRY_POINT_GROUP) -> dict[str, CandidateRanker]:
    """Every CandidateRanker an installed package registers under ``group``, by ranking policy id."""
    return {ep.name: ep.load()() for ep in entry_points(group=group)}


@dataclass(frozen=True)
class SignificanceRanker:
    """The kernel's reference CandidateRanker (rank_by_significance)."""

    ranker_id: str = "kernel-significance"

    def rank(self, results: Sequence[TestResult], policy: RankingPolicy) -> Sequence[RankedCandidate]:
        return rank_by_significance(results, policy)


@dataclass
class _StoreCheckpoints:
    """Task checkpoints as canonical JSON under the set's logical paths, so a run resumes."""

    store: ArtifactStore
    uow: Callable[[], UnitOfWork]
    clock: Clock
    set_id: str

    def completed(self, task_id: str) -> tuple[bool, Any]:
        found = self.store.resolve(set_path(self.set_id, f"tasks/{task_id}.json"))
        if found is None:
            return False, None
        return True, decode_artifact(self.store.get(found), JSON)

    def write(self, task: ComputeTask, value: Any, duration_s: float, resources: Mapping[str, float]) -> None:
        artifact = self.store.put(canonical_json(value).encode(), JSON)
        self.store.link(set_path(self.set_id, f"tasks/{task.task_id}.json"), artifact)
        with self.uow() as uow:
            uow.events.append(
                [
                    ev.CheckpointWritten(
                        self.set_id,
                        self.clock.now(),
                        {"task_id": task.task_id, "artifact_id": artifact.value, "duration_s": round(duration_s, 6)},
                    )
                ]
            )
            uow.commit()


def build_set_manifest(
    investigation_set: InvestigationSet, environment: Mapping[str, Any], tests: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """The investigation-set manifest (INV-002, INV-007): field, profile hash, archive releases,
    code revision, seed, and the multiplicity — every object examined and every test applied."""
    doc = investigation_set.to_document()
    doc.pop("manifest_id", None)
    n_objects = len(investigation_set.objects_examined)
    n_tests = len(investigation_set.profile.tests)
    doc["environment"] = dict(environment)
    doc["code_revision"] = environment.get("code_revision")
    doc["tests"] = [dict(t) for t in tests]
    doc["multiplicity"] = {
        "objects_examined": n_objects,
        "tests_applied": n_tests,
        "comparisons": n_objects * n_tests,
        "candidates_opened": len(investigation_set.investigation_ids),
    }
    return doc


@dataclass
class InvestigateFieldUseCase:
    uow: Callable[[], UnitOfWork]
    store: ArtifactStore
    clock: Clock
    ids: IdGenerator
    environment: EnvironmentProbe
    executor: ComputeExecutor
    acquire: AcquireDatasetUseCase
    ports: InvestigatePorts
    _gate: PlanGate | None = field(default=None, init=False, repr=False)

    # -- orchestration -------------------------------------------------------------------------

    def execute(self, command: InvestigateFieldCommand) -> InvestigationRun:
        profile = command.profile
        tests = self.ports.tests.validate(profile.tests)  # INV-006: before anything runs
        self._ranker(profile.ranking)  # an unknown ranking policy fails here, not after the tests ran
        tests_described = [
            {**describe(t), "thresholds": dict(s.thresholds)} for s, t in zip(profile.tests, tests, strict=True)
        ]
        investigation_set = self._open_set(command, tests_described)
        self._gate = command.gate
        try:
            return self._run(investigation_set, tests_described)
        finally:
            self._gate = None

    def _run(self, investigation_set: InvestigationSet, tests_described: list[dict[str, Any]]) -> InvestigationRun:
        objects: ObjectSet | None = None
        bundles: list[EvidenceBundle] = []
        results: list[TestResult] = []
        nulls: list[NullFieldResult] = []
        stage = Stage.IDENTIFY
        try:
            objects = self._identify(investigation_set)
            stage = Stage.RETRIEVE
            bundles = self._retrieve(investigation_set, objects)
            stage = Stage.TEST
            results, nulls = self._test(investigation_set, bundles)
            stage = Stage.CANDIDATES
            investigations = self._candidates(investigation_set, bundles, results, nulls, tests_described)
        except Exception as exc:
            with self.uow() as uow:
                investigation_set.fail_stage(stage, f"{type(exc).__name__}: {exc}", self.clock.now())
                self._save_set(uow, investigation_set, tests_described)
                uow.commit()
            raise
        with self.uow() as uow:
            manifest = self._save_set(uow, investigation_set, tests_described)
            uow.commit()
        return InvestigationRun(investigation_set, tuple(investigations), tuple(bundles), manifest)

    def _open_set(self, command: InvestigateFieldCommand, tests: Sequence[Mapping[str, Any]]) -> InvestigationSet:
        if command.set_id is not None:
            with self.uow() as uow:
                existing = uow.investigations.get_set(command.set_id)
            if existing is None:
                raise InvariantViolation(f"no investigation set {command.set_id} to resume")
            if existing.profile_hash != command.profile.profile_hash or existing.field != command.field:
                raise InvariantViolation(
                    "a resumed run keeps its field and profile; a changed profile is a new version and a new run"
                )
            planned = command.gate.plan.plan_id if command.gate else None
            if existing.plan_artifact_id != planned:
                raise InvariantViolation("a resumed run executes the plan it started under, and only that plan")
            for stage, record in existing.stages.items():
                if record.state is StageState.FAILED:
                    existing.stages[stage].state = StageState.PENDING
            return existing
        now = self.clock.now()
        investigation_set = InvestigationSet(
            set_id=self.ids.new("invset"),
            field=command.field,
            profile=command.profile,
            seed=command.seed,
            started_at=now,
            plan_artifact_id=command.gate.plan.plan_id if command.gate else None,
        )
        environment = self.environment.record(Backend.CPU).to_document()
        with self.uow() as uow:
            uow.events.append(
                [
                    ev.InvestigationSetStarted(
                        investigation_set.set_id,
                        now,
                        {
                            "field_id": command.field.field_id,
                            "profile_id": command.profile.profile_id,
                            "profile_version": command.profile.version,
                            "profile_hash": command.profile.profile_hash,
                            "seed": command.seed,
                            "code_revision": environment.get("code_revision"),
                            "actor": command.actor,
                            "plan_artifact_id": investigation_set.plan_artifact_id,
                        },
                    )
                ]
            )
            graph = ProvGraph()
            activity = graph.activity(
                f"investigation/{investigation_set.set_id}",
                type="Investigate",
                label=EXPLORATORY,
                profile_hash=command.profile.profile_hash,
            )
            graph.agent(command.actor)
            graph.relate("wasAssociatedWith", activity, command.actor)
            uow.evidence.record(graph)
            self._save_set(uow, investigation_set, tests)
            uow.commit()
        return investigation_set

    def _save_set(
        self, uow: UnitOfWork, investigation_set: InvestigationSet, tests: Sequence[Mapping[str, Any]]
    ) -> dict[str, Any]:
        environment = self.environment.record(Backend.CPU).to_document()
        manifest = build_set_manifest(investigation_set, environment, tests)
        manifest_id = self.store.put(canonical_json(manifest).encode(), JSON)
        self.store.link(set_path(investigation_set.set_id, "manifest.json"), manifest_id)
        investigation_set.manifest_id = manifest_id
        uow.investigations.save_set(investigation_set)
        uow.events.append(investigation_set.pull_events())
        return manifest

    def _complete(
        self, investigation_set: InvestigationSet, stage: Stage, document: Mapping[str, Any], **summary: Any
    ) -> ArtifactId:
        artifact = self.store.put(canonical_json(document).encode(), JSON)
        self.store.link(set_path(investigation_set.set_id, f"{stage.value}.json"), artifact)
        with self.uow() as uow:
            investigation_set.complete_stage(stage, artifact, self.clock.now(), **summary)
            graph = ProvGraph()
            activity = f"investigation/{investigation_set.set_id}"
            graph.activity(activity)
            graph.entity(artifact.value, type=f"Investigate{stage.value.title()}", label=EXPLORATORY)
            graph.relate("wasGeneratedBy", artifact.value, activity)
            uow.evidence.record(graph)
            uow.investigations.save_set(investigation_set)
            uow.events.append(investigation_set.pull_events())
            uow.commit()
        return artifact

    def _stage_document(self, investigation_set: InvestigationSet, stage: Stage) -> Any:
        record = investigation_set.stages[stage]
        if record.state is not StageState.COMPLETED or record.artifact_id is None:
            return None
        return decode_artifact(self.store.get(record.artifact_id), JSON)

    @staticmethod
    def _used(uow: UnitOfWork, set_id: str, artifact: DatasetArtifact) -> None:
        graph = ProvGraph()
        activity = graph.activity(f"investigation/{set_id}")
        graph.entity(artifact.artifact_id.value)
        graph.relate("used", activity, artifact.artifact_id.value)
        uow.evidence.record(graph)

    # -- stage 1: Identify ---------------------------------------------------------------------

    def _authorise(self, source_id: str, operation: Operation, target: str) -> tuple[bool, PlanItem | None]:
        """Whether an acquisition may run: always outside a plan; under one, only when an approved
        item names it (INT-015). The refusal is kept on the gate for the execution record."""
        if self._gate is None:
            return True, None
        item = self._gate.authorise(source_id, operation, target)
        return item is not None, item

    def _catalogue_table(
        self,
        investigation_set: InvestigationSet,
        query: CatalogueQuery,
        *,
        required: bool,
        cut_off: dict[str, int] | None = None,
    ) -> tuple[pa.Table, DatasetArtifact] | None:
        """Acquire one catalogue query. A read that fills its row cap is noted in ``cut_off`` (source id to
        cap): the region may hold more rows than were read, and the stage summary says so (#59)."""
        allowed, item = self._authorise(query.source_id, query_operation(query), query_target(query))
        if not allowed:
            if required:
                raise IntegrationRefused(
                    f"plan {investigation_set.plan_artifact_id} names no item for the {query.source_id} query "
                    "Identify needs; nothing is acquired that the plan does not name (INT-015)"
                )
            log.warning("catalogue query refused by the plan", extra={"source": query.source_id})
            return None
        result = self.acquire.execute(AcquireDatasetCommand(query))
        if cut_off is not None and result.reached_row_cap:
            cut_off[query.source_id] = query.row_cap
        artifact = result.curated or result.raw
        if artifact.status is DatasetStatus.REJECTED:
            raise IntegrityError(f"{query.source_id} acquisition was rejected: {artifact.rejection_reason}")
        if self._gate is not None and item is not None:
            self._gate.record(item, artifact.artifact_id.value)
        with self.uow() as uow:
            self._used(uow, investigation_set.set_id, artifact)
            uow.commit()
        table = decode_artifact(self.store.get(artifact.artifact_id), PARQUET)
        investigation_set.archive_releases = tuple(sorted({*investigation_set.archive_releases, query.release_label}))
        return table, artifact

    def _identify(self, investigation_set: InvestigationSet) -> ObjectSet:
        done = self._stage_document(investigation_set, Stage.IDENTIFY)
        if done is not None:
            restored = ObjectSet.from_document(done)
            investigation_set.objects_examined = restored.keys()
            return restored
        spec = investigation_set.profile.identify
        field_ = investigation_set.field
        cut_off: dict[str, int] = {}
        identified = self._catalogue_table(
            investigation_set, identify_query(field_, investigation_set.profile), required=True, cut_off=cut_off
        )
        assert identified is not None
        gaia, gaia_artifact = identified
        objects: list[IdentifiedObject]
        if spec.route is IdentifyRoute.CATALOGUE:
            objects = self._from_catalogue(gaia, gaia_artifact.artifact_id, investigation_set)
        else:
            objects = self._from_images(gaia, gaia_artifact.artifact_id, investigation_set)
        if spec.resolve and self.ports.resolver is not None:
            resolver = self.ports.resolver
            objects = [
                o.with_resolution(resolver.by_position(o.ra_deg, o.dec_deg, spec.resolver_radius_arcsec))
                for o in objects
            ]
            investigation_set.archive_releases = tuple(
                sorted({*investigation_set.archive_releases, resolver.release_label})
            )
        object_set = ObjectSet(field_.field_id, tuple(objects))
        investigation_set.objects_examined = object_set.keys()
        self._complete(
            investigation_set,
            Stage.IDENTIFY,
            object_set.to_document(),
            objects=len(objects),
            unmatched=sum(1 for o in objects if o.unmatched),
            resolved=sum(1 for o in objects if o.resolver_record is not None),
            route=spec.route.value,
            **({ROW_CAP_REACHED: cut_off} if cut_off else {}),
        )
        return object_set

    def _from_catalogue(
        self, gaia: pa.Table, artifact: ArtifactId, investigation_set: InvestigationSet
    ) -> list[IdentifiedObject]:
        out = []
        for row in gaia.to_pylist():
            ra, dec = float(row["ra_deg"]), float(row["dec_deg"])
            if not investigation_set.field.contains(ra, dec):
                continue
            key = str(row["source_key"])
            out.append(
                IdentifiedObject(
                    object_key=key,
                    method=IdentificationMethod.CATALOGUE,
                    ra_deg=ra,
                    dec_deg=dec,
                    source_artifacts=(artifact,),
                    gaia_source_key=key,
                )
            )
        return out

    def _from_images(
        self, gaia: pa.Table, gaia_artifact: ArtifactId, investigation_set: InvestigationSet
    ) -> list[IdentifiedObject]:
        detector = self.ports.detector
        if detector is None:
            raise InvariantViolation("the image-first route needs a SourceDetector")
        field_ = investigation_set.field
        if not field_.images:
            raise InvariantViolation("the image-first route needs at least one image in the field")
        radius = investigation_set.profile.identify.match_radius_arcsec
        out: dict[str, IdentifiedObject] = {}
        for index, image in enumerate(field_.images):
            frame = ImageFrame(
                artifact_id=image.image_id.value,
                data=self.store.get(image.image_id),
                media_type=self.store.media_type(image.image_id),
                wcs=self.store.get(image.wcs_id) if image.wcs_id else None,
            )
            found = detector.detect(frame, {})
            detections = found.detections
            header = found.wcs_header
            if self.ports.refiner is not None and gaia.num_rows:
                refined = self.ports.refiner.refine(
                    found,
                    gaia.column("ra_deg").to_numpy(zero_copy_only=False),
                    gaia.column("dec_deg").to_numpy(zero_copy_only=False),
                    {},
                )
                detections, header = refined.detections, refined.wcs_header
            wcs_artifact = self.store.put(header.encode(), TEXT)
            if not detections:
                continue
            det_table = pa.table({"ra_deg": [d.ra_deg for d in detections], "dec_deg": [d.dec_deg for d in detections]})
            match = self.ports.cross_match.match(det_table, gaia, radius * u.arcsec)
            matched = {
                int(left): (int(right), float(sep))
                for left, right, sep in zip(
                    match.left_index, match.right_index, match.separation.to_value(u.arcsec), strict=True
                )
            }
            for i, d in enumerate(detections):
                if not field_.contains(d.ra_deg, d.dec_deg):
                    continue
                common: dict[str, Any] = {
                    "method": IdentificationMethod.DETECTION,
                    "ra_deg": d.ra_deg,
                    "dec_deg": d.dec_deg,
                    "source_artifacts": (image.image_id, wcs_artifact, gaia_artifact),
                    "pixel_xy": (d.x, d.y),
                    "wcs_artifact": wcs_artifact,
                    "image_artifact": image.image_id,
                    "match_radius_arcsec": radius,
                }
                if i in matched:
                    right, sep = matched[i]
                    key = str(gaia.column("source_key")[right].as_py())
                    obj = IdentifiedObject(object_key=key, gaia_source_key=key, match_distance_arcsec=sep, **common)
                else:
                    key = f"{field_.field_id}:{index}:{i}"
                    obj = IdentifiedObject(object_key=key, flags=(UNMATCHED,), **common)
                out.setdefault(key, obj)
        return list(out.values())

    # -- stage 2: Retrieve ---------------------------------------------------------------------

    def _register_bytes(
        self,
        set_id: str,
        *,
        data: bytes,
        media_type: str,
        archive: str,
        release_label: str,
        licence: str,
        citation: str,
        rendered: str,
        product: ProductUse | None = None,
    ) -> DatasetArtifact:
        """Register bytes read from an archive as a raw DatasetArtifact before any test reads them
        (INV-005); the checksum is of the bytes actually read (INV-013). ``product`` names the
        adapter and product type for admission. Records are immutable (KRN-014), so bytes registered
        earlier keep what they were given; a product use that cannot be added is logged."""
        if not licence.strip() or not citation.strip():
            raise CurationBlocked(f"{archive} declares no licence or citation; retrieval is blocked (DSI-006)")
        checksum = ArtifactId.of_bytes(data)
        stored = self.store.put(data, media_type)
        if stored != checksum or ArtifactId.of_bytes(self.store.get(stored)) != checksum:
            raise IntegrityError(f"{archive}: stored bytes do not hash to {checksum}")
        with self.uow() as uow:
            existing = uow.datasets.get(checksum)
            if existing is not None:
                _warn_if_product_lost(existing, product)
                self._used(uow, set_id, existing)
                uow.commit()
                return existing
            artifact = DatasetArtifact.acquired_raw(
                actual=checksum,
                media_type=media_type,
                byte_count=len(data),
                source_ref=SourceRef(
                    kind=SourceKind.ARCHIVE,
                    identity=archive,
                    licence=licence,
                    citation=citation,
                    release_label=release_label,
                    query_text=rendered,
                ),
                acquisition=AcquisitionRecord(
                    rendered_query=rendered,
                    idempotency_key=idempotency_key(rendered, release_label, ()),
                    source_release=release_label,
                    acquired_at=self.clock.now(),
                    checksum=checksum,
                    byte_count=len(data),
                ),
            )
            if product is not None:
                artifact = replace(artifact, product=product)
            uow.datasets.add(artifact)
            uow.events.append(
                [
                    ev.DatasetAcquired(
                        checksum.value,
                        self.clock.now(),
                        {"source": archive, "release": release_label, "bytes": len(data)},
                    )
                ]
            )
            self._used(uow, set_id, artifact)
            uow.commit()
        return artifact

    def _curate_product(
        self,
        set_id: str,
        raw: DatasetArtifact,
        fetched: FetchedProduct,
        product_type: str,
        dialect: ProductDialect,
        guard: Callable[[CuratedProduct], SemanticValidationReport] | None = None,
    ) -> DatasetArtifact | None:
        """The canonical form of a raw product, registered as its curated child naming the dialect
        (DSI-005); None when the dialect does not curate this product type, or when ``guard``
        refuses the curated form (INT-007)."""
        canonical = dialect.curate(product_type, fetched)
        if canonical is None:
            return None
        if guard is not None:
            verdict = guard(canonical)
            if not verdict.passed:
                failed = ", ".join(c.field for c in verdict.failures())
                log.warning("curation refused", extra={"artifact_id": raw.artifact_id.value, "failed": failed})
                return None
        ref = DialectRef(dialect.dialect_id, dialect.dialect_version)
        if ArtifactId.of_bytes(canonical.data) == raw.artifact_id:
            return None  # already canonical: the raw product is what the test reads
        # The stamp keeps a text curated artifact unique to its raw parent and dialect, as the
        # catalogue path's Parquet metadata does: equal rows from two raw products are two artifacts.
        # A binary form (FITS) is never stamped, since a prefix would make it unreadable; equal bytes
        # from two raw products are then one artifact, and the provenance graph names each parent.
        data = canonical.data
        if canonical.media_type.startswith("text/"):
            stamp = f"# sdp.dialect={ref.dialect_id}@{ref.dialect_version} sdp.raw_parent={raw.artifact_id.value}\n"
            data = stamp.encode() + data
        curated_id = self.store.put(data, canonical.media_type)
        with self.uow() as uow:
            existing = uow.datasets.get(curated_id)
            if existing is not None:
                _warn_if_product_lost(existing, raw.product)
                self._used(uow, set_id, existing)
                uow.commit()
                return existing
            curated = raw.curate(
                curated_id=curated_id, byte_count=len(data), dialect=ref, media_type=canonical.media_type
            )
            uow.datasets.add(curated)
            graph = ProvGraph()
            activity = graph.activity(
                f"curation/{curated_id.value}",
                type="Curation",
                dialect=ref.dialect_id,
                dialect_version=ref.dialect_version,
            )
            graph.entity(curated_id.value, type="DatasetArtifact", kind="curated")
            graph.entity(raw.artifact_id.value)
            graph.relate("used", activity, raw.artifact_id.value)
            graph.relate("wasGeneratedBy", curated_id.value, activity)
            graph.relate("wasDerivedFrom", curated_id.value, raw.artifact_id.value)
            uow.evidence.record(graph)
            uow.events.append(
                [
                    ev.DatasetAcquired(
                        curated_id.value,
                        self.clock.now(),
                        {
                            "curated_from": raw.artifact_id.value,
                            "dialect": ref.dialect_id,
                            "dialect_version": ref.dialect_version,
                            "product_type": product_type,
                        },
                    )
                ]
            )
            self._used(uow, set_id, curated)
            uow.commit()
        return curated

    def _enveloped(
        self,
        set_id: str,
        raw: DatasetArtifact,
        fetched: FetchedProduct,
        product_type: str,
        envelopes: ProductEnvelopes,
    ) -> ProductEvidence:
        """Normalise and validate a raw product, curate it only when both pass, and store the
        envelope with its report beside the product (Integration v0.4.0 §3, INT-006 to INT-008)."""
        rows = envelopes.inventory.rows(fetched.product)
        raw_product = RawProduct(
            fetched, raw.artifact_id.value, rows.rendered_query, rows.inventory_row, rows.obscore_row, rows.links
        )
        metadata = envelopes.normalizer.normalize(raw_product)
        if metadata.cutout is None and rows.cutout is not None:
            metadata = replace(metadata, cutout=rows.cutout)  # read per product, whatever the dialect (INT-004)
        report = envelopes.validator.validate(metadata, raw_product)
        envelope = ScientificDataProduct(raw.artifact_id.value, metadata).validated(report)
        curated = None
        if report.passed and metadata.product_type.is_science:
            curated = self._curate_product(
                set_id,
                raw,
                fetched,
                product_type,
                envelopes.normalizer,
                guard=lambda c: envelopes.validator.validate_curation(metadata, raw_product, c),
            )
            if curated is not None:
                envelope = envelope.curate(curated.artifact_id.value)
        envelope_id = self.store.put(canonical_json(envelope.to_document()).encode(), JSON)
        with self.uow() as uow:
            graph = ProvGraph()
            activity = graph.activity(
                f"normalisation/{envelope_id.value}",
                type="Normalisation",
                dialect=envelopes.normalizer.dialect_id,
                dialect_version=envelopes.normalizer.dialect_version,
                validator=envelopes.validator.validator_id,
                validator_version=envelopes.validator.validator_version,
                passed=report.passed,
            )
            graph.entity(envelope_id.value, type="ProductEnvelope", product_type=metadata.product_type.value)
            graph.entity(raw.artifact_id.value)
            graph.relate("used", activity, raw.artifact_id.value)
            graph.relate("wasGeneratedBy", envelope_id.value, activity)
            graph.relate("wasDerivedFrom", envelope_id.value, raw.artifact_id.value)
            uow.evidence.record(graph)
            uow.commit()
        return ProductEvidence(
            fetched.product,
            product_type,
            curated.artifact_id if curated else raw.artifact_id,
            fetched.read_from,
            raw_artifact_id=raw.artifact_id if curated else None,
            envelope_artifact_id=envelope_id,
        )

    def _retrieve(self, investigation_set: InvestigationSet, objects: ObjectSet) -> list[EvidenceBundle]:
        done = self._stage_document(investigation_set, Stage.RETRIEVE)
        if done is not None:
            return [EvidenceBundle.from_document(b) for b in done["bundles"]]
        profile = investigation_set.profile
        keys = objects.keys()
        rows: dict[str, dict[str, tuple[Mapping[str, Any], ...]]] = {k: {} for k in keys}
        artifacts: dict[str, dict[str, ArtifactId]] = {k: {} for k in keys}
        releases: dict[str, set[str]] = {k: set() for k in keys}

        identify = profile.identify
        cut_off: dict[str, int] = {}
        identified = self._catalogue_table(
            investigation_set, identify_query(investigation_set.field, profile), required=True, cut_off=cut_off
        )
        assert identified is not None
        gaia, gaia_artifact = identified
        by_key = {str(r["source_key"]): r for r in gaia.to_pylist()}
        for o in objects.objects:
            if o.gaia_source_key and o.gaia_source_key in by_key:
                rows[o.object_key][identify.source_id] = (by_key[o.gaia_source_key],)
                artifacts[o.object_key][identify.source_id] = gaia_artifact.artifact_id
                releases[o.object_key].add(identify.release_label)

        positions = pa.table(
            {"ra_deg": [o.ra_deg for o in objects.objects], "dec_deg": [o.dec_deg for o in objects.objects]}
        )
        for selection in profile.archives:
            if selection.role is not ArchiveRole.CATALOGUE:
                continue
            read = self._catalogue_table(
                investigation_set, catalogue_query(investigation_set.field, selection), required=False, cut_off=cut_off
            )
            if read is None:
                continue
            table, artifact = read
            if not keys or table.num_rows == 0:
                continue
            match = self.ports.cross_match.match(positions, table, selection.match_radius_arcsec * u.arcsec)
            table_rows = table.to_pylist()
            for left, right in zip(match.left_index, match.right_index, strict=True):
                key = keys[int(left)]
                rows[key][selection.source_id] = (*rows[key].get(selection.source_id, ()), table_rows[int(right)])
                artifacts[key][selection.source_id] = artifact.artifact_id
                releases[key].add(selection.release_label)

        needed = {p for t in profile.tests for p in t.needs_products}
        indices: dict[str, list[tuple[ObservationIndex, ArtifactId]]] = {k: [] for k in keys}
        products: dict[str, list[ProductEvidence]] = {k: [] for k in keys}
        for selection in profile.archives:
            if selection.role is not ArchiveRole.OBSERVATIONS:
                continue
            gateway = self.ports.observation_gateways.get(selection.source_id)
            if gateway is None:
                raise InvariantViolation(f"no ObservationArchiveGateway for {selection.source_id!r} (INV-004)")
            # A cutout selection is field-wide: one search and one cutout around the field centre,
            # shared by every object, as the planner plans it (INT-004). Otherwise each object is
            # searched on its own and whole products are fetched.
            side = selection.cutout_arcsec
            cutouts = gateway if side is not None and isinstance(gateway, ProductCutouts) else None
            if side is not None and cutouts is None:
                log.warning("cutouts asked of a gateway that cannot cut", extra={"source": selection.source_id})
            if cutouts is not None:
                ra, dec, radius = field_search_cone(investigation_set.field)
                criteria = ObservationCriteria(
                    missions=selection.missions,
                    product_types=selection.product_types,
                    radius_arcsec=radius + selection.match_radius_arcsec,
                    min_calibration_level=selection.min_calibration_level,
                )
                targets = [(ObservationTarget("field", ra, dec), list(keys))]
            else:
                criteria = ObservationCriteria(
                    missions=selection.missions,
                    product_types=selection.product_types,
                    radius_arcsec=selection.match_radius_arcsec,
                    min_calibration_level=selection.min_calibration_level,
                )
                targets = [
                    (
                        observation_target(
                            o.object_key, o.ra_deg, o.dec_deg, rows[o.object_key].get(identify.source_id, ())
                        ),
                        [o.object_key],
                    )
                    for o in objects.objects
                ]
            for target, owners in targets:
                index = gateway.search(target, criteria)
                registered = self._register_bytes(
                    investigation_set.set_id,
                    data=canonical_json(index.to_document()).encode(),
                    media_type=JSON,
                    archive=index.archive,
                    release_label=index.release_label,
                    licence=index.licence,
                    citation=index.citation,
                    rendered=index.query_text,
                )
                for key in owners:
                    indices[key].append((index, registered.artifact_id))
                    releases[key].add(index.release_label)
                investigation_set.archive_releases = tuple(
                    sorted({*investigation_set.archive_releases, index.release_label})
                )
                # Products only when a test in the profile declares it needs them; a product offered
                # as several types (a DASCH light curve is also a position history) is fetched once.
                retrieved: dict[ProductRef, tuple[FetchedProduct, DatasetArtifact]] = {}
                envelopes = self.ports.product_envelopes.get(selection.source_id)
                # One artifact per product, so one use: the first admittable type it is offered as.
                uses: dict[ProductRef, ProductUse] = {}
                if envelopes is not None:
                    for record in index.records:
                        if (
                            record.product_type in needed
                            and record.product_type in ADMITTABLE_PRODUCT_TYPES
                            and envelopes.covers(record.mission)
                        ):
                            uses.setdefault(
                                record.product,
                                ProductUse(envelopes.adapter, envelopes.adapter_version, record.product_type),
                            )
                for record in index.records:
                    if record.product_type not in needed:
                        continue
                    ref = record.product
                    if cutouts is not None:
                        assert side is not None
                        ref = cutout_ref(record.product, target.ra_deg, target.dec_deg, side)
                    if record.product not in retrieved:
                        operation = Operation.CUTOUT if cutouts is not None else Operation.DOWNLOAD
                        allowed, item = self._authorise(selection.source_id, operation, ref.product_id)
                        if not allowed:
                            continue
                        if cutouts is not None:
                            assert side is not None
                            try:
                                got = cutouts.cutout_product(record.product, target.ra_deg, target.dec_deg, side)
                            except (LookupError, ValueError, RuntimeError) as exc:
                                # This product cannot be cut here (compressed, no range support, off
                                # the image): it is left out, never fetched whole behind the plan's back.
                                log.warning("cutout not made", extra={"product": ref.product_id, "error": str(exc)})
                                continue
                        else:
                            got = gateway.fetch(record.product)
                        retrieved[record.product] = (
                            got,
                            self._register_bytes(
                                investigation_set.set_id,
                                data=got.data,
                                media_type=got.media_type,
                                archive=index.archive,
                                release_label=got.release_label,
                                licence=got.licence,
                                citation=got.citation,
                                rendered=f"GET {got.read_from}",
                                product=uses.get(record.product),
                            ),
                        )
                        if self._gate is not None and item is not None:
                            self._gate.record(item, retrieved[record.product][1].artifact_id.value)
                    fetched, product = retrieved[record.product]
                    if envelopes is not None and envelopes.covers(record.mission):
                        try:
                            evidence = self._enveloped(
                                investigation_set.set_id, product, fetched, record.product_type, envelopes
                            )
                        except (LookupError, ValueError, RuntimeError) as exc:
                            # An inventory miss, unreadable bytes or a transport failure (an adapter's
                            # TransportError is a RuntimeError): the raw product is still evidence, but
                            # without an envelope it is never curated (INT-006).
                            log.warning(
                                "product not enveloped",
                                extra={"artifact_id": product.artifact_id.value, "error": str(exc)},
                            )
                            evidence = ProductEvidence(ref, record.product_type, product.artifact_id, fetched.read_from)
                        for key in owners:
                            products[key].append(evidence)
                        continue
                    dialect = self.ports.product_dialects.get(selection.source_id)
                    curated = (
                        self._curate_product(investigation_set.set_id, product, fetched, record.product_type, dialect)
                        if dialect is not None
                        else None
                    )
                    for key in owners:
                        products[key].append(
                            ProductEvidence(
                                ref,
                                record.product_type,
                                curated.artifact_id if curated else product.artifact_id,
                                fetched.read_from,
                                raw_artifact_id=product.artifact_id if curated else None,
                            )
                        )

        bundles = [
            EvidenceBundle(
                object=o,
                catalogue_rows=rows[o.object_key],
                catalogue_artifacts=artifacts[o.object_key],
                observation_indices=tuple(i for i, _ in indices[o.object_key]),
                observation_artifacts=tuple(a for _, a in indices[o.object_key]),
                products=tuple(products[o.object_key]),
                releases=tuple(sorted(releases[o.object_key])),
            )
            for o in objects.objects
        ]
        self._complete(
            investigation_set,
            Stage.RETRIEVE,
            {"bundles": [b.to_document() for b in bundles]},
            bundles=len(bundles),
            observation_indices=sum(len(v) for v in indices.values()),
            products=sum(len(v) for v in products.values()),
            **({ROW_CAP_REACHED: cut_off} if cut_off else {}),
        )
        return bundles

    # -- stage 3: Test -------------------------------------------------------------------------

    def _test(
        self, investigation_set: InvestigationSet, bundles: Sequence[EvidenceBundle]
    ) -> tuple[list[TestResult], list[NullFieldResult]]:
        done = self._stage_document(investigation_set, Stage.TEST)
        if done is not None:
            nulls = [NullFieldResult.from_document(n) for n in done["null_results"]]
            investigation_set.null_results = tuple(nulls)
            return [TestResult.from_document(r) for r in done["results"]], nulls
        profile = investigation_set.profile
        registry = self.ports.tests
        bundle_docs = [b.to_document() for b in bundles]
        tasks = []
        for selection in profile.tests:
            test = registry.get(selection.name, selection.version)
            common = {"bundles": bundle_docs, "thresholds": dict(selection.thresholds)}
            tasks.append(
                ComputeTask(f"run:{selection.key}", task_kind(test.name, test.version), test.policy, params=common)
            )
            tasks.append(
                ComputeTask(
                    f"null:{selection.key}",
                    null_task_kind(test.name, test.version),
                    test.policy,
                    params={**common, "trials": profile.null_fields.trials, "alpha": profile.ranking.alpha},
                )
            )
        plan = ComputePlan(tuple(tasks), outputs=tuple(t.task_id for t in tasks))
        sink = _StoreCheckpoints(self.store, self.uow, self.clock, investigation_set.set_id)
        products = {p.artifact_id.value: self.store.get(p.artifact_id) for b in bundles for p in b.products}
        services = {**self.ports.services, PRODUCTS_SERVICE: products}
        values = self.executor.execute(
            plan, registry.task_kinds(), seed=investigation_set.seed, sink=sink, services=services
        )
        results = [
            TestResult.from_document(doc) for selection in profile.tests for doc in values[f"run:{selection.key}"]
        ]
        nulls = [NullFieldResult.from_document(values[f"null:{s.key}"]) for s in profile.tests]
        investigation_set.null_results = tuple(nulls)
        investigation_set.tests_applied = tuple(
            {
                "name": s.name,
                "version": s.version,
                "thresholds": dict(s.thresholds),
                "policy": registry.get(s.name, s.version).policy.to_document(),
            }
            for s in profile.tests
        )
        self._complete(
            investigation_set,
            Stage.TEST,
            {"results": [r.to_document() for r in results], "null_results": [n.to_document() for n in nulls]},
            results=len(results),
            tests=len(profile.tests),
        )
        return results, nulls

    # -- stage 4: Candidates -------------------------------------------------------------------

    def _evidence_used(self, investigation_set: InvestigationSet, bundles: Sequence[EvidenceBundle]) -> EvidenceUsed:
        archives = {
            investigation_set.profile.identify.source_id,
            *(a.source_id for a in investigation_set.profile.archives),
        }
        epochs: list[float] = []
        for b in bundles:
            for source_rows in b.catalogue_rows.values():
                epochs.extend(float(r["epoch_jyear"]) for r in source_rows if r.get("epoch_jyear") is not None)
            for index in b.observation_indices:
                archives.add(index.archive)
                for rec in index.records:
                    if rec.epoch_mjd is not None:
                        epochs.append(mjd_to_jyear(rec.epoch_mjd))
                    if rec.epoch_end_mjd is not None:
                        epochs.append(mjd_to_jyear(rec.epoch_end_mjd))
        epochs = [e for e in epochs if math.isfinite(e)]
        return EvidenceUsed(
            archives=tuple(sorted(archives)),
            releases=tuple(sorted({*investigation_set.archive_releases, *(r for b in bundles for r in b.releases)})),
            region=investigation_set.field.region.to_document(),
            epoch_min_jyear=min(epochs) if epochs else None,
            epoch_max_jyear=max(epochs) if epochs else None,
        )

    def _ranker(self, policy: RankingPolicy) -> CandidateRanker:
        """The ranker the policy names; a policy never silently falls back to another (Investigate §6)."""
        if self.ports.ranker is not None:
            return self.ports.ranker
        if SIGNIFICANCE_POLICY in self.ports.rankers:
            raise PlanInvalid(f"{SIGNIFICANCE_POLICY!r} is the kernel's own ranking policy; a ranker cannot replace it")
        rankers: dict[str, CandidateRanker] = {SIGNIFICANCE_POLICY: SignificanceRanker(), **self.ports.rankers}
        if policy.policy_id not in rankers:
            raise PlanInvalid(
                f"no ranker for ranking policy {policy.policy_id!r}; registered: {sorted(rankers)} "
                "(science-module rankers come with the science extra, e.g. sdp-cli[science])"
            )
        return rankers[policy.policy_id]

    def _candidates(
        self,
        investigation_set: InvestigationSet,
        bundles: Sequence[EvidenceBundle],
        results: Sequence[TestResult],
        nulls: Sequence[NullFieldResult],
        tests: Sequence[Mapping[str, Any]],
    ) -> list[Investigation]:
        if investigation_set.stage_done(Stage.CANDIDATES):
            with self.uow() as uow:
                return list(uow.investigations.for_set(investigation_set.set_id))
        policy = investigation_set.profile.ranking
        ranker = self._ranker(policy)
        ranked = [c for c in ranker.rank(results, policy) if c.score >= policy.threshold]
        by_key = {b.object_key: b for b in bundles}
        investigations: list[Investigation] = []
        now = self.clock.now()
        for candidate in ranked:
            subject = candidate.subject
            members = (subject.object_key,) if subject.object_key is not None else subject.members
            own = [by_key[k] for k in members if k in by_key]
            if not own:
                log.warning("candidate %s has no evidence bundle; skipped", subject.key)
                continue
            investigation = Investigation.open(
                investigation_id=self.ids.new("inv"),
                set_id=investigation_set.set_id,
                field_id=investigation_set.field.field_id,
                candidate=candidate,
                evidence_bundle_ids=tuple(b.bundle_id for b in own),
                policy=policy,
                evidence_used=self._evidence_used(investigation_set, own),
                at=now,
            )
            resolved = next((b.object.resolver_record for b in own if b.object.resolver_record), None)
            if resolved is not None:
                investigation.link("simbad_id", resolved.main_id)
            dossier = build_investigation_dossier(investigation_set, investigation, own, nulls)
            dossier_id = self.store.put(canonical_json(dossier).encode(), JSON)
            self.store.link(
                set_path(investigation_set.set_id, f"dossiers/{investigation.investigation_id}.json"), dossier_id
            )
            investigation.link("dossier_id", dossier_id.value)
            investigations.append(investigation)

        with self.uow() as uow:
            graph = ProvGraph()
            activity = graph.activity(f"investigation/{investigation_set.set_id}")
            for investigation in investigations:
                uow.investigations.save(investigation)
                uow.events.append(investigation.pull_events())
                graph.entity(investigation.investigation_id, type="Investigation", label=EXPLORATORY)
                graph.relate("wasGeneratedBy", investigation.investigation_id, activity)
                for artifact in {
                    a for b in bundles if b.bundle_id in investigation.evidence_bundle_ids for a in b.artifact_ids()
                }:
                    graph.entity(artifact.value)
                    graph.relate("wasDerivedFrom", investigation.investigation_id, artifact.value)
            uow.evidence.record(graph)
            uow.commit()
        investigation_set.investigation_ids = tuple(i.investigation_id for i in investigations)
        self._complete(
            investigation_set,
            Stage.CANDIDATES,
            {"investigations": [i.to_document() for i in investigations]},
            candidates=len(investigations),
            ranked=len(ranked),
        )
        return investigations


def build_investigation_dossier(
    investigation_set: InvestigationSet,
    investigation: Investigation,
    bundles: Sequence[EvidenceBundle],
    nulls: Sequence[NullFieldResult],
) -> dict[str, Any]:
    """The candidate's dossier (Investigate §6): the canonical rows by source, the observation index
    with its reach-back dates, products fetched, every test result with its null comparison and the
    set-level false-positive rate. Exported as an RO-Crate draft by the evidence UI."""
    null_by_test = {f"{n.test_name}@{n.test_version}": n.to_document() for n in nulls}
    return {
        "kind": "investigation-dossier",
        "label": EXPLORATORY,
        "investigation_id": investigation.investigation_id,
        "set_id": investigation_set.set_id,
        "field": investigation_set.field.to_document(),
        "profile": {
            "profile_id": investigation_set.profile.profile_id,
            "version": investigation_set.profile.version,
            "hash": investigation_set.profile_hash,
        },
        "candidate": investigation.candidate.to_document(),
        "rank": investigation.rank.to_document(),
        "objects": [
            {
                "object": b.object.to_document(),
                "bundle_id": b.bundle_id,
                "catalogue_rows": b.to_document()["catalogue_rows"],
                "catalogue_artifacts": {k: v.value for k, v in sorted(b.catalogue_artifacts.items())},
                "observation_index": [
                    {
                        "archive": i.archive,
                        "release_label": i.release_label,
                        "records": len(i.records),
                        "reach_back_mjd": i.reach_back_mjd,
                        "reach_back_jyear": mjd_to_jyear(i.reach_back_mjd) if i.reach_back_mjd is not None else None,
                    }
                    for i in b.observation_indices
                ],
                "products": [p.to_document() for p in b.products],
            }
            for b in bundles
        ],
        "test_results": [{**r.to_document(), "null": null_by_test.get(r.test_key)} for r in investigation.test_results],
        "set_false_positive_rates": null_by_test,
        "multiplicity": {
            "objects_examined": len(investigation_set.objects_examined),
            "tests_applied": len(investigation_set.profile.tests),
        },
        "evidence_used": investigation.evidence_used.to_document(),
    }


@dataclass
class TriageInvestigationUseCase:
    """Park, reopen, close, annotate or promote. Promotion is the one transition with a precondition
    the kernel checks: a frozen confirmatory predeclaration is in the ledger and names evidence the
    investigation did not use; otherwise it is refused and the refusal recorded (INV-009)."""

    uow: Callable[[], UnitOfWork]
    ledger: Ledger
    clock: Clock

    def execute(self, command: TriageCommand) -> Investigation:
        now = self.clock.now()
        try:
            with self.uow() as uow:
                investigation = uow.investigations.get(command.investigation_id)
                if investigation is None:
                    raise InvariantViolation(f"no investigation {command.investigation_id}")
                if command.action == "promote":
                    self._promote(uow, investigation, command, now)
                elif command.action == "park":
                    investigation.park(command.reason, command.actor, now)
                elif command.action == "reopen":
                    investigation.reopen(command.reason, command.actor, now)
                elif command.action == "close":
                    investigation.close(command.reason, command.actor, now)
                elif command.action == "annotate":
                    investigation.annotate(command.reason, command.actor, now)
                else:
                    raise InvariantViolation(f"unknown triage action {command.action!r}")
                uow.investigations.save(investigation)
                uow.events.append(investigation.pull_events())
                uow.commit()
        except (PromotionRefused, PredeclarationNotFrozen, InvalidTransition) as refused:
            if command.action == "promote":
                self._record_refusal(command, str(refused))
            raise
        return investigation

    def _promote(self, uow: UnitOfWork, investigation: Investigation, command: TriageCommand, now: Any) -> None:
        if not command.predeclaration_hash:
            raise PromotionRefused("promotion names a confirmatory predeclaration (INV-009)")
        if self.ledger.find(command.predeclaration_hash, "predeclaration") is None:
            raise PredeclarationNotFrozen(
                f"{command.predeclaration_hash} is not in the ledger; promotion refused (INV-009)"
            )
        frozen = uow.predeclarations.get_frozen(command.predeclaration_hash)
        if frozen is None:
            raise PredeclarationNotFrozen(f"{command.predeclaration_hash} is not a frozen predeclaration (INV-009)")
        investigation.promote(frozen, actor=command.actor, at=now, experiment_id=command.experiment_id)

    def _record_refusal(self, command: TriageCommand, reason: str) -> None:
        with self.uow() as uow:
            uow.events.append(
                [
                    ev.InvestigationPromotionRefused(
                        command.investigation_id,
                        self.clock.now(),
                        {"predeclaration_hash": command.predeclaration_hash, "actor": command.actor, "reason": reason},
                    )
                ]
            )
            uow.commit()


@dataclass
class QueryInvestigationsUseCase:
    uow: Callable[[], UnitOfWork]
    store: ArtifactStore

    def sets(self) -> Sequence[InvestigationSet]:
        with self.uow() as uow:
            return uow.investigations.list_sets()

    def set(self, set_id: str) -> InvestigationSet | None:
        with self.uow() as uow:
            return uow.investigations.get_set(set_id)

    def investigations(self, set_id: str) -> Sequence[Investigation]:
        with self.uow() as uow:
            return uow.investigations.for_set(set_id)

    def investigation(self, investigation_id: str) -> Investigation | None:
        with self.uow() as uow:
            return uow.investigations.get(investigation_id)

    def manifest(self, set_id: str) -> dict[str, Any] | None:
        found = self.store.resolve(set_path(set_id, "manifest.json"))
        return decode_artifact(self.store.get(found), JSON) if found else None

    def dossier(self, investigation_id: str) -> dict[str, Any] | None:
        investigation = self.investigation(investigation_id)
        if investigation is None or "dossier_id" not in investigation.links:
            return None
        doc: dict[str, Any] = decode_artifact(self.store.get(ArtifactId(investigation.links["dossier_id"])), JSON)
        return doc

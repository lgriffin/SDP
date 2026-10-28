"""Synthetic fixtures for the v0.4.0 integration seams: descriptors, requirements, envelopes,
plans and a planted-truth association field.

Everything here is synthetic and says so in its ids; real descriptors are read from the VO, and
real envelopes come from recorded products (never hand-written archive responses).
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime

from sdp.kernel.domain.archive import Cone
from sdp.kernel.domain.association import (
    AssociationPolicy,
    CatalogueSource,
    Detection,
    Motion,
    PositionCovariance,
)
from sdp.kernel.domain.integration import (
    AccessStatus,
    AcquisitionBudget,
    AcquisitionPlan,
    AdapterCompatibilityRecord,
    AdapterInfo,
    ConformanceKind,
    ConformanceResult,
    Coverage,
    EvidenceRequirement,
    Operation,
    PayloadExtension,
    PlanCost,
    PlanItem,
    ProductMetadata,
    ProductRelationship,
    ProductType,
    ProtocolEndpoint,
    RelationshipSemantics,
    Rights,
    ServiceLimits,
    SourceCapabilityDescriptor,
    match_requirement,
)
from sdp.kernel.domain.investigation import Field
from sdp.kernel.ports.ledger import LedgerEntry

VERIFIED = date(2026, 10, 28)
SYNTHETIC_FIELD = Field(Cone(53.16, -27.78, 0.05), epoch_jyear=2023.0, label="synthetic-deep-field")
SHA = "sha256:" + "ab" * 32


def _all_pass(*types: ProductType) -> dict[str, bool]:
    return {t.value: True for t in types}


def mast_descriptor(*, admitted: bool = True) -> SourceCapabilityDescriptor:
    types = (ProductType.IMAGE, ProductType.SPECTRUM, ProductType.CUBE, ProductType.TIMESERIES)
    return SourceCapabilityDescriptor(
        source_id="synthetic-mast",
        ivoid="ivo://synthetic/mast/caom",
        title="Synthetic MAST ObsCore",
        publisher="SDP test fixtures",
        origin="registry",
        verified_on=VERIFIED,
        protocols=(ProtocolEndpoint("TAP", "1.1", "https://example.invalid/mast/tap"),),
        data_models=("ObsCore-1.1", "CAOM-2.4"),
        product_types=frozenset(types),
        missions=("JWST", "HST", "TESS"),
        instruments=("NIRCAM", "NIRSPEC", "MIRI", "WFC3"),
        calibration_levels=frozenset({1, 2, 3}),
        coverage=Coverage(all_sky=True),
        operations=frozenset({Operation.CONE, Operation.CRITERIA, Operation.PRODUCT_LISTING, Operation.DOWNLOAD}),
        cutout_support={"image": "per-product"},
        limits=ServiceLimits(max_rows=100000, upload=False, throughput_bytes_per_s=5e6),
        rights=Rights("public domain", "MAST", "Based on observations obtained from MAST at STScI."),
        adapter=AdapterInfo("mast-observations", "1.0.0", VERIFIED, _all_pass(ProductType.IMAGE) if admitted else {}),
    )


def gaia_descriptor() -> SourceCapabilityDescriptor:
    return SourceCapabilityDescriptor(
        source_id="synthetic-gaia",
        ivoid="ivo://synthetic/gaia/tap",
        title="Synthetic Gaia TAP",
        publisher="SDP test fixtures",
        origin="registry",
        verified_on=VERIFIED,
        protocols=(ProtocolEndpoint("TAP", "1.1", "https://example.invalid/gaia/tap"),),
        product_types=frozenset({ProductType.CATALOGUE}),
        missions=("Gaia",),
        coverage=Coverage(all_sky=True, t_min_mjd=56863.0, t_max_mjd=57888.0),
        operations=frozenset({Operation.CONE, Operation.REGION, Operation.DOWNLOAD}),
        limits=ServiceLimits(max_rows=3000000, upload=True),
        rights=Rights("CC-BY-SA-3.0 IGO", "Gaia Collaboration"),
        adapter=AdapterInfo("gaia-tap", "1.0.0", VERIFIED, _all_pass(ProductType.CATALOGUE)),
    )


def dasch_descriptor() -> SourceCapabilityDescriptor:
    return SourceCapabilityDescriptor(
        source_id="synthetic-dasch",
        title="Synthetic DASCH",
        publisher="SDP test fixtures",
        origin="hand-written",
        verified_on=date(2026, 9, 1),
        product_types=frozenset({ProductType.TIMESERIES, ProductType.IMAGE}),
        missions=("DASCH",),
        coverage=Coverage(all_sky=None, t_min_mjd=7000.0, t_max_mjd=48000.0),
        operations=frozenset({Operation.CONE, Operation.DOWNLOAD, Operation.CUTOUT}),
        cutout_support={"image": "native"},
        rights=Rights("CC-BY-4.0", "DASCH"),
        adapter=AdapterInfo("dasch", "1.0.0", None, {}),
    )


def registry_descriptors() -> tuple[SourceCapabilityDescriptor, ...]:
    return (mast_descriptor(), gaia_descriptor(), dasch_descriptor())


def image_requirement() -> EvidenceRequirement:
    return EvidenceRequirement(
        "req-jwst-image", "detect-sources", frozenset({ProductType.IMAGE}), min_calibration_level=2, missions=("JWST",)
    )


def astrometry_requirement() -> EvidenceRequirement:
    return EvidenceRequirement(
        "req-gaia-astrometry",
        "astrometric-coherence",
        frozenset({ProductType.CATALOGUE}),
        operations=frozenset({Operation.CONE}),
    )


def image_metadata(*, checksum: str = SHA, product_type: ProductType = ProductType.IMAGE) -> ProductMetadata:
    return ProductMetadata(
        product_id="synthetic:JWST/product/jw00000-o001_t001_nircam_clear-f200w_i2d.fits",
        product_type=product_type,
        mission="JWST",
        instrument="NIRCAM",
        detector="NRCA1",
        filter_or_grating="F200W",
        observation_id="jw00000-o001_t001_nircam_clear-f200w",
        programme_id="0",
        ra_deg=53.16,
        dec_deg=-27.78,
        frame="ICRS",
        s_region=None,
        time_start_mjd=60000.0,
        time_end_mjd=60000.1,
        time_scale="UTC",
        em_min_m=1.75e-6,
        em_max_m=2.25e-6,
        spatial_resolution_arcsec=0.07,
        field_of_view_deg=0.04,
        calibration_level=3,
        pipeline_version="1.0.0",
        media_type="application/fits",
        dimensions=(2048, 2048),
        units="MJy/sr",
        extensions=(
            PayloadExtension("SCI", "data", "MJy/sr"),
            PayloadExtension("ERR", "error", "MJy/sr"),
            PayloadExtension("DQ", "quality"),
        ),
        has_wcs=True,
        access_status=AccessStatus.PUBLIC,
        exclusive_access_ends=None,
        rights=Rights("public domain", "MAST"),
        source_id="synthetic-mast",
        rendered_query="synthetic",
        checksum=checksum,
        size_bytes=1024,
        relationships=(
            ProductRelationship(RelationshipSemantics.PROGENITOR, "synthetic:JWST/product/a_cal.fits"),
            ProductRelationship(RelationshipSemantics.PROGENITOR, "synthetic:JWST/product/b_cal.fits"),
        ),
    )


def budget(threshold: int = 10_000_000) -> AcquisitionBudget:
    return AcquisitionBudget(
        max_bytes=1_000_000_000,
        max_requests_per_source=50,
        max_wall_seconds=3600.0,
        min_nas_headroom_bytes=0,
        approval_threshold_bytes=threshold,
    )


def sample_plan(*, image_bytes: int = 5_000_000, threshold: int = 10_000_000) -> AcquisitionPlan:
    reqs = (image_requirement(), astrometry_requirement())
    region = SYNTHETIC_FIELD.query_region()
    matches = tuple(match_requirement(d, region, r) for r in reqs for d in registry_descriptors())
    items = (
        PlanItem(
            "item-1",
            "synthetic-mast",
            Operation.DOWNLOAD,
            image_metadata().product_id,
            image_bytes,
            None,
            3,
            AccessStatus.PUBLIC,
            "public domain",
            "req-jwst-image",
            "detect-sources",
        ),
        PlanItem(
            "item-2",
            "synthetic-gaia",
            Operation.CONE,
            "SELECT ... CONTAINS(...)",
            20_000,
            100,
            None,
            AccessStatus.PUBLIC,
            "CC-BY-SA-3.0 IGO",
            "req-gaia-astrometry",
            "astrometric-coherence",
        ),
    )
    total = sum(i.expected_bytes for i in items)
    return AcquisitionPlan(
        field=SYNTHETIC_FIELD.to_document(),
        profile_hash=SHA,
        planner_id="fixture-planner",
        planner_version="1.0.0",
        requirements=reqs,
        matches=matches,
        items=items,
        cost=PlanCost(total, {"synthetic-mast": 1, "synthetic-gaia": 1}, 2.0, None),
        budget=budget(threshold),
    )


def ledger_approval(plan: AcquisitionPlan, signer: str = "leigh", kind: str = "approval") -> LedgerEntry:
    """A plan approval as the ledger returns it once verified: its subject is the plan id."""
    return LedgerEntry(
        entry_id="ledger-42",
        sequence=7,
        kind=kind,  # type: ignore[arg-type]
        subject_id="plan",
        subject_hash=plan.plan_id,
        recorded_at=datetime(2026, 10, 28, tzinfo=UTC),
        signer=signer,
        signature_method="ssh",
        signature="fixture",
        previous_entry_hash=None,
    )


def compatibility(
    adapter: str = "mast-observations",
    *,
    product_type: ProductType = ProductType.IMAGE,
    failing: tuple[ConformanceKind, ...] = (),
    version: str = "1.0.0",
) -> AdapterCompatibilityRecord:
    return AdapterCompatibilityRecord(
        adapter=adapter,
        adapter_version=version,
        product_type=product_type,
        results=tuple(ConformanceResult(k, k not in failing, "ci-run-0") for k in ConformanceKind),
        upstream_versions={"astropy": "7.0.0"},
        live_smoke_on=VERIFIED,
        fixture_digests=(SHA,),
    )


# ---------------------------------------------------------------------------------------------
# Planted-truth association field (v0.4.0 §12 fixtures/association-truth, in code form)

ARCSEC = 1.0 / 3600.0


def association_policy() -> AssociationPolicy:
    return AssociationPolicy("fixture-association", "1.0.0", common_epoch_jyear=2023.0, search_radius_arcsec=3.0)


def planted_field() -> tuple[list[CatalogueSource], list[Detection]]:
    """Two isolated stars, one tight pair a coarse survey blends, and one moving star.

    - ``gaia-a`` and ``jwst-a`` are the same star (0.05 arcsec apart).
    - ``gaia-b1`` and ``gaia-b2`` are 1.0 arcsec apart; ``wise-b`` (6 arcsec resolution) blends both,
      so it must associate one-to-many.
    - ``gaia-c`` moves 500 mas/yr in RA; ``jwst-c`` sees it 3.5 years later, about 1.75 arcsec east.
    - ``gaia-d1`` and ``gaia-d2`` are 0.6 arcsec apart and ``hst-d`` (0.1 arcsec resolution, 0.4 arcsec
      sigma) sits between them: a genuine two-way ambiguity, not a blend.
    """
    cov_g = PositionCovariance(0.02, 0.02)
    cov_j = PositionCovariance(0.03, 0.03)
    cov_w = PositionCovariance(0.5, 0.5)
    ra0, dec0 = 53.16, -27.78
    gaia = [
        CatalogueSource("gaia-a", "gaia", "DR3", "1", ra0, dec0, cov_g, 2016.0, Motion(0.0, 0.0)),
        CatalogueSource("gaia-b1", "gaia", "DR3", "2", ra0 + 20 * ARCSEC, dec0, cov_g, 2016.0, Motion(0.0, 0.0)),
        CatalogueSource(
            "gaia-b2", "gaia", "DR3", "3", ra0 + 20 * ARCSEC, dec0 + 1.0 * ARCSEC, cov_g, 2016.0, Motion(0.0, 0.0)
        ),
        CatalogueSource("gaia-c", "gaia", "DR3", "4", ra0, dec0 + 40 * ARCSEC, cov_g, 2016.0, Motion(500.0, 0.0)),
        CatalogueSource("gaia-d1", "gaia", "DR3", "5", ra0, dec0 - 30 * ARCSEC, cov_g, 2016.0, Motion(0.0, 0.0)),
        CatalogueSource("gaia-d2", "gaia", "DR3", "6", ra0, dec0 - 29.4 * ARCSEC, cov_g, 2016.0, Motion(0.0, 0.0)),
    ]
    cosd = math.cos(math.radians(dec0 + 40 * ARCSEC))
    dets = [
        Detection("jwst-a", "synthetic:JWST/i2d", "SCI", ra0 + 0.05 * ARCSEC, dec0, cov_j, 2019.5, 0.07, "NIRCAM"),
        Detection(
            "wise-b", "synthetic:WISE/w1", "SCI", ra0 + 20 * ARCSEC, dec0 + 0.5 * ARCSEC, cov_w, 2019.5, 6.0, "WISE"
        ),
        Detection(
            "jwst-c",
            "synthetic:JWST/i2d",
            "SCI",
            ra0 + (3.5 * 0.5 * ARCSEC) / cosd,
            dec0 + 40 * ARCSEC,
            cov_j,
            2019.5,
            0.07,
            "NIRCAM",
        ),
        Detection(
            "hst-d",
            "synthetic:HST/drz",
            "SCI",
            ra0,
            dec0 - 29.7 * ARCSEC,
            PositionCovariance(0.4, 0.4),
            2010.0,
            0.1,
            "WFC3",
        ),
    ]
    return gaia, dets


def synthetic_association_field(
    seed: int, *, stars: int = 40, radius_arcsec: float = 120.0, blend_pairs: int = 3
) -> tuple[list[CatalogueSource | Detection], dict[str, frozenset[str]]]:
    """A random field with known truth: Gaia stars with proper motions, a JWST epoch detecting each
    star with noise, and a WISE epoch that blends ``blend_pairs`` close pairs into one detection.

    Truth maps each detection id to the set of anchor ids that produced it.
    """
    import numpy as np

    rng = np.random.default_rng(seed)
    ra0, dec0 = 150.0, 2.0
    cosd = math.cos(math.radians(dec0))
    records: list[CatalogueSource | Detection] = []
    truth: dict[str, frozenset[str]] = {}
    pos: list[tuple[float, float]] = []
    while len(pos) < stars:
        r = radius_arcsec * math.sqrt(rng.uniform())
        t = rng.uniform(0, 2 * math.pi)
        x, y = r * math.cos(t), r * math.sin(t)
        if all(math.hypot(x - a, y - b) > 8.0 for a, b in pos):
            pos.append((x, y))
    motions: list[tuple[float, float]] = []
    for i, (x, y) in enumerate(pos):
        pm = (float(rng.normal(0, 30)), float(rng.normal(0, 30)))
        motions.append(pm)
        sid = f"gaia-{i:03d}"
        records.append(
            CatalogueSource(
                sid,
                "gaia",
                "DR3",
                str(i),
                ra0 + x / 3600 / cosd,
                dec0 + y / 3600,
                PositionCovariance(0.02, 0.02),
                2016.0,
                Motion(pm[0], pm[1], None, 0.05, 0.05),
            )
        )
        dt = 2023.0 - 2016.0
        jx = x + pm[0] * dt / 1000 + rng.normal(0, 0.03)
        jy = y + pm[1] * dt / 1000 + rng.normal(0, 0.03)
        did = f"jwst-{i:03d}"
        records.append(
            Detection(
                did,
                "synthetic:JWST",
                "SCI",
                ra0 + jx / 3600 / cosd,
                dec0 + jy / 3600,
                PositionCovariance(0.03, 0.03),
                2023.0,
                0.07,
                "NIRCAM",
            )
        )
        truth[did] = frozenset({sid})
    for k in range(blend_pairs):
        x, y = pos[k]
        sep = float(rng.uniform(1.0, 2.0))
        j = stars + k
        bx, by = x + sep, y
        records.append(
            CatalogueSource(
                f"gaia-{j:03d}",
                "gaia",
                "DR3",
                str(j),
                ra0 + bx / 3600 / cosd,
                dec0 + by / 3600,
                PositionCovariance(0.02, 0.02),
                2016.0,
                Motion(0.0, 0.0, None, 0.05, 0.05),
            )
        )
        dt = 2019.5 - 2016.0
        ax = x + motions[k][0] * dt / 1000
        ay = y + motions[k][1] * dt / 1000
        wx, wy = (ax + bx) / 2 + rng.normal(0, 0.3), (ay + by) / 2 + rng.normal(0, 0.3)
        wid = f"wise-{k:03d}"
        records.append(
            Detection(
                wid,
                "synthetic:WISE",
                "SCI",
                ra0 + wx / 3600 / cosd,
                dec0 + wy / 3600,
                PositionCovariance(0.5, 0.5),
                2019.5,
                6.0,
                "WISE",
            )
        )
        truth[wid] = frozenset({f"gaia-{k:03d}", f"gaia-{j:03d}"})
    return records, truth

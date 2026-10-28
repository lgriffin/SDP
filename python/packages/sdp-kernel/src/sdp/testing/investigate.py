"""A synthetic Investigate world for T4 tests: a field, a canonical catalogue with a planted
co-moving group, fixture observations, a fixture resolver, a reference test and a profile.

The catalogue is synthetic, not Gaia data; the planted group is what tests expect to recover.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import pyarrow as pa

from sdp.kernel.application.investigate import InvestigatePorts
from sdp.kernel.domain.archive import ColumnDescriptor, Cone, SourceDescriptor
from sdp.kernel.domain.investigation import (
    ArchiveRole,
    ArchiveSelection,
    EvidenceBundle,
    Field,
    IdentifyRoute,
    IdentifySpec,
    NullFieldPlan,
    NullFieldResult,
    ObservationRecord,
    ProductRef,
    RankingPolicy,
    ResolverRecord,
    Subject,
    TestProfile,
    TestResult,
    TestSelection,
)
from sdp.kernel.domain.policy import NumericalPolicy
from sdp.kernel.ports.investigate import (
    CandidateRanker,
    ObjectResolver,
    ProductDialect,
    SourceDetector,
    WcsRefiner,
)
from sdp.kernel.science.investigation import InvestigationTest, InvestigationTestRegistry
from sdp.kernel.science.plan import TaskContext
from sdp.testing.fakes.archives import AstropyCrossMatch, FakeArchive
from sdp.testing.fakes.investigate import FakeObservationArchive, FixtureResolver, PlacedRecord

CANONICAL_COLUMNS = (
    "source_key",
    "ra_deg",
    "dec_deg",
    "epoch_jyear",
    "parallax_mas",
    "parallax_mas_err",
    "pm_ra_cosdec_masyr",
    "pm_dec_masyr",
    "g_mag",
    "bp_rp",
)

SYNTHETIC_GAIA = SourceDescriptor(
    source_id="synthetic-gaia",
    release_label="gaia:DR3",
    table="synthetic.gaia_source",
    columns=tuple(ColumnDescriptor(c, "string" if c == "source_key" else "double") for c in CANONICAL_COLUMNS),
    licence="CC BY-SA 3.0 IGO",
    citation="Synthetic fixture shaped like Gaia DR3 (not real data)",
    max_rows_per_call=40,
    min_seconds_between_calls=0.0,
    dialect_id="canonical",
)

SYNTHETIC_2MASS = SourceDescriptor(
    source_id="synthetic-2mass",
    release_label="twomass:psc",
    table="synthetic.fp_psc",
    columns=(
        ColumnDescriptor("source_key", "string"),
        ColumnDescriptor("ra_deg", "double", "deg"),
        ColumnDescriptor("dec_deg", "double", "deg"),
        ColumnDescriptor("j_mag", "double", "mag"),
        ColumnDescriptor("k_mag", "double", "mag"),
    ),
    licence="2MASS: public, acknowledgement requested",
    citation="Synthetic fixture shaped like 2MASS PSC (not real data)",
    max_rows_per_call=1000,
    min_seconds_between_calls=0.0,
    dialect_id="canonical",
)

FIELD_CENTRE = (120.0, -10.0)
PLANTED = tuple(f"gaia:DR3:{i}" for i in range(5))


def synthetic_field(radius_deg: float = 0.5) -> Field:
    return Field(Cone(FIELD_CENTRE[0], FIELD_CENTRE[1], radius_deg), epoch_jyear=2016.0, label="synthetic field")


def synthetic_catalogue(n: int = 60, seed: int = 7) -> pa.Table:
    """A field of ``n`` stars inside 0.4 deg of the centre; the first five share a large proper
    motion and a parallax (a planted co-moving group)."""
    rng = np.random.default_rng(seed)
    radius = 0.4 * np.sqrt(rng.uniform(0, 1, n))
    theta = rng.uniform(0, 2 * math.pi, n)
    dec = FIELD_CENTRE[1] + radius * np.sin(theta)
    ra = FIELD_CENTRE[0] + radius * np.cos(theta) / math.cos(math.radians(FIELD_CENTRE[1]))
    pmra = rng.normal(-2.0, 1.5, n)
    pmdec = rng.normal(1.0, 1.5, n)
    plx = np.abs(rng.normal(1.0, 0.6, n)) + 0.05
    pmra[:5] = 25.0 + rng.normal(0, 0.2, 5)
    pmdec[:5] = -18.0 + rng.normal(0, 0.2, 5)
    plx[:5] = 4.0 + rng.normal(0, 0.05, 5)
    return pa.table(
        {
            "source_key": [f"gaia:DR3:{i}" for i in range(n)],
            "ra_deg": ra.tolist(),
            "dec_deg": dec.tolist(),
            "epoch_jyear": [2016.0] * n,
            "parallax_mas": plx.tolist(),
            "parallax_mas_err": [0.05] * n,
            "pm_ra_cosdec_masyr": pmra.tolist(),
            "pm_dec_masyr": pmdec.tolist(),
            "g_mag": rng.uniform(10, 18, n).tolist(),
            "bp_rp": rng.uniform(0.3, 2.5, n).tolist(),
        }
    )


def synthetic_twomass(catalogue: pa.Table) -> pa.Table:
    """Infrared counterparts 0.2 arcsec from every other star, J−K = 0.4 (BP−RP) + 0.1 with 0.03 mag
    scatter; star 4 (planted) carries a 0.5 mag infrared excess."""
    rng = np.random.default_rng(11)
    rows = catalogue.to_pylist()[::2]
    jk = [
        0.4 * r["bp_rp"] + 0.1 + rng.normal(0, 0.03) + (0.5 if r["source_key"] == "gaia:DR3:4" else 0.0) for r in rows
    ]
    return pa.table(
        {
            "source_key": [f"twomass:{i}" for i in range(len(rows))],
            "ra_deg": [r["ra_deg"] + 0.2 / 3600 for r in rows],
            "dec_deg": [r["dec_deg"] for r in rows],
            "j_mag": [r["g_mag"] - 1.0 for r in rows],
            "k_mag": [r["g_mag"] - 1.0 - c for r, c in zip(rows, jk, strict=True)],
        }
    )


def gaia_archive(catalogue: pa.Table | None = None) -> FakeArchive:
    return FakeArchive(SYNTHETIC_GAIA, catalogue or synthetic_catalogue(), ra_column="ra_deg", dec_column="dec_deg")


def twomass_archive(catalogue: pa.Table | None = None) -> FakeArchive:
    return FakeArchive(
        SYNTHETIC_2MASS, synthetic_twomass(catalogue or synthetic_catalogue()), ra_column="ra_deg", dec_column="dec_deg"
    )


VARIABLE = "gaia:DR3:0"
"""The planted star whose plate light curve varies (0.8 mag, 6000-day sinusoid); the others are constant."""
ANOMALOUS = "gaia:DR3:2"
"""The planted star whose plate positions disagree with its Gaia proper motion by 3 arcsec."""


def planted_lightcurve(i: int, n: int = 40) -> bytes:
    """Canonical ``mjd,mag,mag_err`` CSV over the plate era, 0.05 mag noise."""
    rng = np.random.default_rng(100 + i)
    mjd = np.linspace(9000.0, 47000.0, n)
    mag = 12.0 + i + rng.normal(0.0, 0.05, n)
    if i == 0:
        mag = mag + 0.8 * np.sin(2 * math.pi * mjd / 6000.0)
    lines = ["mjd,mag,mag_err", *(f"{float(t)!r},{float(m)!r},0.05" for t, m in zip(mjd, mag, strict=True))]
    return ("\n".join(lines) + "\n").encode()


def planted_positions(row: Mapping[str, Any], i: int, n: int = 12) -> bytes:
    """Canonical ``epoch_jyear,ra_deg,dec_deg,pos_err_mas`` CSV from 1890 to 1985 that follows the
    row's Gaia proper motion with 150 mas plate errors; star 2 sits 3 arcsec off."""
    rng = np.random.default_rng(200 + i)
    epochs = np.linspace(1890.0, 1985.0, n)
    dt = epochs - float(row["epoch_jyear"])
    cosd = math.cos(math.radians(float(row["dec_deg"])))
    dec = float(row["dec_deg"]) + float(row["pm_dec_masyr"]) * dt / 3.6e6 + rng.normal(0, 150, n) / 3.6e6
    ra = (
        float(row["ra_deg"])
        + float(row["pm_ra_cosdec_masyr"]) * dt / 3.6e6 / cosd
        + rng.normal(0, 150, n) / 3.6e6 / cosd
    )
    if i == 2:
        dec = dec + 3.0 / 3600
    lines = [
        "epoch_jyear,ra_deg,dec_deg,pos_err_mas",
        *(f"{float(t)!r},{float(a)!r},{float(d)!r},150.0" for t, a, d in zip(epochs, ra, dec, strict=True)),
    ]
    return ("\n".join(lines) + "\n").encode()


def observation_archive(catalogue: pa.Table | None = None) -> FakeObservationArchive:
    """Plate-era light curves (product type ``lightcurve``) for the planted stars, plus a
    preview image (``preview``) for every star; the first planted star has a cloud copy."""
    rows = (catalogue or synthetic_catalogue()).to_pylist()
    records: list[PlacedRecord] = []
    products: dict[str, bytes] = {}
    for i, r in enumerate(rows):
        pid = f"preview-{i}"
        records.append(
            PlacedRecord(
                r["ra_deg"],
                r["dec_deg"],
                ObservationRecord(
                    product=ProductRef("fake-plates", pid, f"https://fake.invalid/{pid}.fits"),
                    mission="DSS",
                    instrument="POSS-I",
                    product_type="preview",
                    epoch_mjd=34000.0 + i,
                    size_bytes=2048,
                    licence="fixture licence",
                    citation="fixture citation",
                ),
            )
        )
        products[pid] = f"preview {i}".encode()
        if r["source_key"] in PLANTED:
            pid = f"lc-{i}"
            cloud = f"s3://fake-bucket/{pid}.csv" if i == 0 else None
            records.append(
                PlacedRecord(
                    r["ra_deg"],
                    r["dec_deg"],
                    ObservationRecord(
                        product=ProductRef("fake-plates", pid, f"https://fake.invalid/{pid}.csv", cloud),
                        mission="DASCH",
                        instrument="plates",
                        product_type="lightcurve",
                        epoch_mjd=9000.0,
                        epoch_end_mjd=47000.0,
                        size_bytes=512,
                        licence="fixture licence",
                        citation="fixture citation",
                    ),
                )
            )
            products[pid] = planted_lightcurve(i)
            pid = f"pos-{i}"
            records.append(
                PlacedRecord(
                    r["ra_deg"],
                    r["dec_deg"],
                    ObservationRecord(
                        product=ProductRef("fake-plates", pid, f"https://fake.invalid/{pid}.csv"),
                        mission="DASCH",
                        instrument="plates",
                        product_type="astrometry",
                        epoch_mjd=9000.0,
                        epoch_end_mjd=47000.0,
                        size_bytes=512,
                        licence="fixture licence",
                        citation="fixture citation",
                    ),
                )
            )
            products[pid] = planted_positions(r, i)
    return FakeObservationArchive("fake-plates", "dasch:DR7", tuple(records), products)


def fixture_resolver(catalogue: pa.Table | None = None) -> FixtureResolver:
    first = (catalogue or synthetic_catalogue()).to_pylist()[1]
    return FixtureResolver(
        (
            ResolverRecord(
                "HD 999999",
                "HighPM*",
                ("2000A&A...000..000X",),
                first["ra_deg"] + 0.3 / 3600,
                first["dec_deg"],
            ),
        )
    )


PM_POLICY = NumericalPolicy(rtol=1e-12)


@dataclass(frozen=True)
class ProperMotionExcessTest:
    """A reference test for the T4 suite: is an object's proper motion an outlier in its field?

    Statistic: distance from the field median proper motion in units of the field's robust spread.
    p-value: the fraction of field objects at least as extreme (empirical, no shape assumed).
    Null model: proper motions shuffled among objects, so each trial is the field with no structure.
    """

    __test__ = False

    name: str = "pm-excess"
    version: str = "1.0.0"
    question: str = "Is the object's proper motion an outlier in its field?"
    policy: NumericalPolicy = PM_POLICY
    null_model: str = "proper motions shuffled among the field's objects"
    inputs: tuple[str, ...] = ("catalogue:synthetic-gaia",)
    needs_products: tuple[str, ...] = ()
    source_id: str = "synthetic-gaia"

    def _pm(self, bundles: Sequence[EvidenceBundle]) -> tuple[list[str], np.ndarray]:
        keys, pm = [], []
        for b in bundles:
            rows = b.rows(self.source_id)
            if rows and rows[0].get("pm_ra_cosdec_masyr") is not None:
                keys.append(b.object_key)
                pm.append((float(rows[0]["pm_ra_cosdec_masyr"]), float(rows[0]["pm_dec_masyr"])))
        return keys, np.asarray(pm, dtype=float).reshape(-1, 2)

    @staticmethod
    def _scores(pm: np.ndarray) -> np.ndarray:
        if len(pm) == 0:
            return np.zeros(0)
        centre = np.median(pm, axis=0)
        spread = np.median(np.abs(pm - centre), axis=0) * 1.4826 + 1e-9
        return np.asarray(np.hypot(*((pm - centre) / spread).T))

    def run(
        self, bundles: Sequence[EvidenceBundle], thresholds: Mapping[str, Any], context: TaskContext
    ) -> Sequence[TestResult]:
        keys, pm = self._pm(bundles)
        scores = self._scores(pm)
        n = len(scores)
        out = []
        for key, s in zip(keys, scores, strict=True):
            p = float(np.sum(scores >= s)) / n
            out.append(
                TestResult(
                    test_name=self.name,
                    test_version=self.version,
                    subject=Subject.of_object(key),
                    statistic=float(s),
                    seed=context.seed,
                    p_value=p,
                    effect_size=float(s),
                    details={"n_field": n},
                )
            )
        return out

    def null(
        self,
        bundles: Sequence[EvidenceBundle],
        thresholds: Mapping[str, Any],
        context: TaskContext,
        trials: int,
        alpha: float,
    ) -> NullFieldResult:
        _, pm = self._pm(bundles)
        rng = np.random.default_rng(context.seed)
        cut = float(thresholds.get("score", 5.0))
        examined = positives = 0
        for _ in range(trials):
            shuffled = np.column_stack([rng.permutation(pm[:, 0]), rng.permutation(pm[:, 1])]) if len(pm) else pm
            scores = self._scores(shuffled)
            examined += len(scores)
            positives += int(np.sum(scores >= cut))
        return NullFieldResult(self.name, self.version, trials, examined, positives, alpha, self.null_model)


def synthetic_profile(
    *,
    route: IdentifyRoute = IdentifyRoute.CATALOGUE,
    threshold: float = 1.0,
    alpha: float = 0.1,
    with_observations: bool = True,
    needs_products: tuple[str, ...] = (),
    version: str = "1.0.0",
) -> TestProfile:
    archives = [
        ArchiveSelection(
            "synthetic-2mass",
            ArchiveRole.CATALOGUE,
            "twomass:psc",
            columns=("source_key", "ra_deg", "dec_deg", "j_mag", "k_mag"),
            match_radius_arcsec=1.0,
        )
    ]
    if with_observations:
        archives.append(ArchiveSelection("fake-plates", ArchiveRole.OBSERVATIONS, "dasch:DR7", match_radius_arcsec=2.0))
    return TestProfile(
        profile_id="synthetic-comoving",
        version=version,
        description="T4 profile over the synthetic field",
        identify=IdentifySpec(
            route=route,
            source_id="synthetic-gaia",
            release_label="gaia:DR3",
            columns=CANONICAL_COLUMNS,
            match_radius_arcsec=1.0,
        ),
        archives=tuple(archives),
        tests=(TestSelection("pm-excess", "1.0.0", {"score": 5.0}, needs_products=needs_products),),
        ranking=RankingPolicy("significance", "1.0.0", threshold=threshold, alpha=alpha),
        null_fields=NullFieldPlan(trials=5),
    )


def investigate_ports(
    *tests: InvestigationTest,
    catalogue: pa.Table | None = None,
    detector: SourceDetector | None = None,
    refiner: WcsRefiner | None = None,
    resolver: ObjectResolver | None = None,
    observations: FakeObservationArchive | None = None,
    resolve: bool = True,
    product_dialects: Mapping[str, ProductDialect] | None = None,
    rankers: Mapping[str, CandidateRanker] | None = None,
) -> InvestigatePorts:
    """InvestigatePorts wired to the synthetic world's fakes."""
    catalogue = catalogue or synthetic_catalogue()
    return InvestigatePorts(
        tests=InvestigationTestRegistry.of(*(tests or (ProperMotionExcessTest(),))),
        cross_match=AstropyCrossMatch(),
        observation_gateways={"fake-plates": observations or observation_archive(catalogue)},
        detector=detector,
        refiner=refiner,
        resolver=(resolver or fixture_resolver(catalogue)) if resolve else None,
        product_dialects=dict(product_dialects or {}),
        rankers=dict(rankers or {}),
    )

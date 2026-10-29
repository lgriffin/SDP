"""The default test profiles in profiles/ (INV-001): each is a valid, versioned profile that names
only real archive columns, registered tests and installed rankers, and each runs end to end.

The end-to-end run maps the profile onto the synthetic world (its sources become the synthetic
Gaia, 2MASS and plate archives, whose rows are already canonical) and keeps every test, threshold,
ranking policy and null-field plan as written, so a threshold a test cannot use fails here first.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from sdp.adapters.archives.dialect import load_dialects
from sdp.adapters.archives.tap import TapArchiveAdapter, load_sources
from sdp.adapters.association import BayesFactorAssociation, LikelihoodRatioAssociation
from sdp.adapters.bootstrap import clusterers
from sdp.adapters.compute.clustering import SklearnHdbscan
from sdp.adapters.imaging.photutils_detector import PhotutilsDetector
from sdp.adapters.imaging.tweakwcs_refiner import TweakwcsRefiner
from sdp.kernel.application.investigate import (
    SIGNIFICANCE_POLICY,
    catalogue_query,
    identify_query,
    load_candidate_rankers,
)
from sdp.kernel.application.media import JSON, decode_artifact
from sdp.kernel.domain.investigation import ArchiveRole, ArchiveSelection, Stage, StageState, TestProfile
from sdp.kernel.ports.investigate import InvestigateFieldCommand
from sdp.kernel.science import products
from sdp.testing.investigate import (
    CANONICAL_COLUMNS,
    gaia_archive,
    investigate_ports,
    synthetic_catalogue,
    synthetic_field,
    twomass_archive,
)
from sdp.testing.jwst import MAST, jwst_catalogue, jwst_cutting_archive, jwst_field, jwst_resolver
from sdp.testing.wiring import build_in_memory_kernel
from sdp_investigate import registered_tests
from sdp_investigate.astrometry import AstrometricCoherence

pytestmark = pytest.mark.tier("T4")

PROFILES = sorted((Path(__file__).resolve().parents[3] / "profiles").glob("*.yaml"))
OBSERVATION_ARCHIVES = {"mast", "dasch"}
"""The ObservationArchiveGateways the production composition root wires (bootstrap.observation_gateways)."""
SYNTHETIC = {"gaia": "synthetic-gaia", "2mass": "synthetic-2mass", "dasch": "fake-plates", "mast": MAST}
SYNTHETIC_RELEASES = {"2mass:psc": "twomass:psc"}


def load(path: Path) -> TestProfile:
    return TestProfile.from_document(yaml.safe_load(path.read_text("utf-8")))


def test_there_are_default_profiles() -> None:
    assert {p.stem for p in PROFILES} >= {"first-look", "moving-group-search", "star-history"}
    # The Start page lists profiles by file name and preselects the first: the quick one to learn on.
    assert PROFILES[0].stem == "first-look"


@pytest.mark.req("INV-001")
@pytest.mark.parametrize("path", PROFILES, ids=lambda p: p.stem)
def test_a_default_profile_names_only_what_exists(path: Path) -> None:
    profile = load(path)
    assert path.stem == profile.profile_id
    assert profile.description.strip(), "the Start page shows the description; say what the profile is for"
    assert TestProfile.from_document(profile.to_document()) == profile

    sources = load_sources()
    dialects = {d.source_id: d for d in load_dialects().values()}
    catalogue_reads = [(profile.identify.source_id, profile.identify.release_label, profile.identify.columns)]
    catalogue_reads += [
        (a.source_id, a.release_label, a.columns) for a in profile.archives if a.role is ArchiveRole.CATALOGUE
    ]
    for source_id, release, columns in catalogue_reads:
        declared = sources[source_id].descriptor
        assert release == declared.release_label
        assert set(columns) <= set(declared.column_names()), f"{source_id}: undeclared columns"
        assert dialects[source_id].native_columns() <= set(columns), f"{source_id}: the dialect could not curate"
    gaia = sources[profile.identify.source_id].descriptor.column_names()
    assert {f.column for f in profile.identify.quality_cuts} <= set(gaia)

    for archive in profile.archives:
        if archive.role is ArchiveRole.OBSERVATIONS:
            assert archive.source_id in OBSERVATION_ARCHIVES
            assert set(archive.product_types) <= {products.LIGHTCURVE, products.ASTROMETRY, "image"}

    registered = {(t.name, t.version): t for t in registered_tests()}
    for selection in profile.tests:
        test = registered[(selection.name, selection.version)]
        assert set(selection.needs_products) == set(test.needs_products), f"{selection.name} needs its products"
        if isinstance(test, AstrometricCoherence):
            test.check_thresholds(selection.thresholds)
            params = [selection.thresholds["clusterer_params"], *selection.thresholds["clusterer_grid"]]
            assert all(set(p) <= {"min_cluster_size", "min_samples"} for p in params), "HDBSCAN's parameters"

    assert profile.ranking.policy_id in {SIGNIFICANCE_POLICY, *load_candidate_rankers()}


@pytest.mark.req("INV-001")
@pytest.mark.parametrize("path", PROFILES, ids=lambda p: p.stem)
def test_the_production_archives_accept_a_default_profiles_queries(path: Path) -> None:
    profile = load(path)
    adapter = TapArchiveAdapter(sources=load_sources(), transport=None)  # type: ignore[arg-type]
    queries = [identify_query(synthetic_field(), profile)]
    queries += [catalogue_query(synthetic_field(), a) for a in profile.archives if a.role is ArchiveRole.CATALOGUE]
    for q in queries:
        adapter._check(q, adapter.sources[q.source_id])


@pytest.mark.req("INV-001")
@pytest.mark.parametrize("path", PROFILES, ids=lambda p: p.stem)
def test_a_default_profiles_group_test_can_reach_its_alpha(path: Path) -> None:
    """A permutation p-value is at least 1/(trials + 1): too few trials and no group is ever significant."""
    profile = load(path)
    for selection in profile.tests:
        if selection.name == "astrometric-coherence":
            assert 1 / (int(selection.thresholds["inner_trials"]) + 1) < profile.ranking.alpha


def on_synthetic_world(profile: TestProfile) -> TestProfile:
    """The same profile reading the synthetic world's archives, whose rows are already canonical."""
    identify = replace(
        profile.identify, source_id=SYNTHETIC["gaia"], columns=CANONICAL_COLUMNS, quality_cuts=(), resolve=False
    )
    archives = tuple(
        replace(
            a,
            source_id=SYNTHETIC[a.source_id],
            release_label=SYNTHETIC_RELEASES.get(a.release_label, a.release_label),
            columns=("source_key", "ra_deg", "dec_deg", "j_mag", "k_mag"),
        )
        if a.role is ArchiveRole.CATALOGUE
        else replace(a, source_id=SYNTHETIC[a.source_id])
        for a in profile.archives
    )
    tests = tuple(
        replace(
            t,
            thresholds={
                k: SYNTHETIC.get(v, v) if k in {"source_id", "ir_source_id"} else v for k, v in t.thresholds.items()
            },
        )
        for t in profile.tests
    )
    return replace(profile, identify=identify, archives=archives, tests=tests)


@pytest.mark.req("INV-001")
@pytest.mark.parametrize("path", PROFILES, ids=lambda p: p.stem)
def test_a_default_profile_runs_end_to_end(path: Path) -> None:
    profile = on_synthetic_world(load(path))
    assert all(isinstance(a, ArchiveSelection) for a in profile.archives)
    # A profile that reads JWST images runs on the synthetic JWST field, with a gateway that cuts.
    images = any(a.source_id == MAST for a in profile.archives)
    catalogue = jwst_catalogue() if images else synthetic_catalogue()
    ports = investigate_ports(
        *registered_tests(),
        catalogue=catalogue,
        rankers=load_candidate_rankers(),
        **({"resolver": jwst_resolver(catalogue)} if images else {}),
    )
    if images:
        ports.observation_gateways = {MAST: jwst_cutting_archive(catalogue)}
    k = build_in_memory_kernel(
        gateways={"synthetic-gaia": gaia_archive(catalogue), "synthetic-2mass": twomass_archive(catalogue)},
        investigate=ports,
    )
    use_case = k.kernel.investigate_field
    assert use_case is not None
    use_case.ports.services = {
        "clusterer": SklearnHdbscan(),
        "clusterers": clusterers(),
        "detector": PhotutilsDetector(),
        "associators": (BayesFactorAssociation(), LikelihoodRatioAssociation()),
        "refiner": TweakwcsRefiner(),
    }
    field_ = jwst_field() if images else synthetic_field()
    run = use_case.execute(InvestigateFieldCommand(field_, profile, seed=1))
    assert all(record.state is StageState.COMPLETED for record in run.investigation_set.stages.values())
    stage = run.investigation_set.stages[Stage.TEST].artifact_id
    assert stage is not None
    results = decode_artifact(k.store.get(stage), JSON)["results"]
    # Every test judged something: no threshold left a test with nothing it could use. The one
    # exception is the structure test, which reports groups only: the synthetic field's planted group
    # has 5 members, below UC-01's smallest min_cluster_size of 10, so it rightly finds none.
    judged = {r["test_name"] for r in results}
    assert judged == {t.name for t in profile.tests} - {"astrometric-coherence"}

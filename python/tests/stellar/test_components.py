"""T1: the UC-01 components on their own: feature policy, quality cuts, membership, null and
injection generators, ranking and the claim lint."""

from __future__ import annotations

import json

import numpy as np
import pyarrow as pa
import pytest

from sdp.adapters.compute.clustering import SklearnHdbscan
from sdp.kernel.domain.errors import InvariantViolation
from sdp.kernel.domain.predeclaration import Predeclaration
from sdp_stellar import dossier, ranking
from sdp_stellar.features import FeatureMatrix, PolicyError, apply_policy, tangent_plane
from sdp_stellar.inject import inject, recovery
from sdp_stellar.membership import estimate
from sdp_stellar.null import null_field
from sdp_stellar.protocol import StellarClusterProtocol, load_predeclaration
from sdp_stellar.quality import QualityCuts, column
from sdp_stellar.search import Configuration, configurations, labelling_document, run

from .conftest import field, targets

pytestmark = pytest.mark.tier("T1")

PLEIADES = next(t for t in targets() if t["target_id"] == "pleiades")
CENTRE = (PLEIADES["spec"]["ra_deg"], PLEIADES["spec"]["dec_deg"])
CUTS = QualityCuts.from_parameters(load_predeclaration()["parameters"]["quality_cuts"])
HDBSCAN = Configuration("sklearn-hdbscan", {"min_cluster_size": 20, "min_samples": 10})


def _kept() -> pa.Table:
    return CUTS.apply(field("pleiades"))[0]


def _labels(table: pa.Table) -> np.ndarray:
    fm = apply_policy("fsp-v1", table, *CENTRE)
    return np.asarray(run(SklearnHdbscan(), fm, HDBSCAN, 1).labels)


def _same_partition(a: np.ndarray, b: np.ndarray) -> bool:
    return bool(np.all((a[:, None] == a[None, :]) == (b[:, None] == b[None, :])))


@pytest.mark.req("STL-001")
@pytest.mark.gate("STL-G-01")
def test_every_predeclared_region_carries_its_reason_and_an_empty_one_is_refused() -> None:
    regions = StellarClusterProtocol().regions()
    assert [r["target_id"] for r in regions] == ["pleiades", "praesepe", "h-chi-persei", "ngp-control"]
    assert all(r["reason"].strip() for r in regions)
    doc = load_predeclaration()
    doc["targets"][0]["reason"] = " "
    with pytest.raises(InvariantViolation, match="reason"):
        Predeclaration.from_document(doc)


@pytest.mark.req("STL-002")
def test_clustering_takes_only_a_feature_matrix_from_a_registered_policy() -> None:
    kept = _kept()
    raw = np.column_stack(
        [column(kept, c) for c in ("ra_deg", "dec_deg", "pm_ra_cosdec_masyr", "pm_dec_masyr", "parallax_mas")]
    )
    with pytest.raises(PolicyError, match="never raw columns"):
        run(SklearnHdbscan(), raw, HDBSCAN, 1)  # type: ignore[arg-type]
    with pytest.raises(PolicyError, match="unknown feature-space policy"):
        apply_policy("raw-euclidean", kept, *CENTRE)
    with pytest.raises(PolicyError, match="unregistered policy"):
        FeatureMatrix(raw, "raw-euclidean", ("a", "b", "c", "d", "e"), (1, 1, 1, 1, 1))


@pytest.mark.req("STL-002")
def test_rescaling_proper_motion_units_leaves_the_feature_matrix_unchanged() -> None:
    kept = _kept()
    as_per_year = kept
    for name in ("pm_ra_cosdec_masyr", "pm_dec_masyr"):
        i = kept.schema.get_field_index(name)
        as_per_year = as_per_year.set_column(i, name, pa.array(column(kept, name) / 1000.0))
    a = apply_policy("fsp-v1", kept, *CENTRE).values
    b = apply_policy("fsp-v1", as_per_year, *CENTRE).values
    assert np.allclose(a, b, rtol=1e-9, atol=1e-12)


@pytest.mark.req("STL-002")
def test_a_small_rigid_rotation_of_the_field_leaves_memberships_unchanged() -> None:
    kept = _kept()
    xi, eta = tangent_plane(column(kept, "ra_deg"), column(kept, "dec_deg"), *CENTRE)
    angle = np.radians(3.0)
    xr, er = np.cos(angle) * xi - np.sin(angle) * eta, np.sin(angle) * xi + np.cos(angle) * eta
    # back from the tangent plane to the sky
    ra0, dec0 = np.radians(CENTRE[0]), np.radians(CENTRE[1])
    x, y = np.radians(xr), np.radians(er)
    rho = np.hypot(x, y)
    c = np.arctan(rho)
    dec = np.arcsin(
        np.cos(c) * np.sin(dec0) + np.where(rho > 0, y * np.sin(c) * np.cos(dec0) / np.where(rho > 0, rho, 1), 0)
    )
    ra = ra0 + np.arctan2(x * np.sin(c), rho * np.cos(dec0) * np.cos(c) - y * np.sin(dec0) * np.sin(c))
    rotated = kept.set_column(kept.schema.get_field_index("ra_deg"), "ra_deg", pa.array(np.degrees(ra) % 360.0))
    rotated = rotated.set_column(rotated.schema.get_field_index("dec_deg"), "dec_deg", pa.array(np.degrees(dec)))
    assert _same_partition(_labels(kept), _labels(rotated))


@pytest.mark.req("STL-006")
def test_row_permutation_leaves_memberships_unchanged_up_to_relabelling() -> None:
    kept = _kept()
    perm = np.random.default_rng(5).permutation(kept.num_rows)
    a = _labels(kept)
    b = _labels(kept.take(pa.array(perm)))
    assert _same_partition(a[perm], b)


@pytest.mark.req("STL-003")
def test_quality_cuts_report_what_each_cut_removed() -> None:
    table = field("pleiades")
    kept, report = CUTS.apply(table)
    assert report["cuts"] == load_predeclaration()["parameters"]["quality_cuts"]
    assert report["input_rows"] == table.num_rows and report["kept_rows"] == kept.num_rows < table.num_rows
    assert set(report["failed_by_cut"]) == {
        "five_parameter_astrometry",
        "ruwe",
        "parallax_over_error",
        "g_mag",
        "astrometric_params_solved",
    }
    assert report["failed_by_cut"]["ruwe"] > 0
    assert sum(report["removed_in_order"].values()) == report["input_rows"] - report["kept_rows"]
    assert all(report["removed_in_order"][k] <= report["failed_by_cut"][k] for k in report["failed_by_cut"])
    assert np.all(column(kept, "ruwe") <= CUTS.ruwe_max)


@pytest.mark.req("STL-003")
@pytest.mark.parametrize("error", [0.0, -0.1, float("nan"), float("inf")])
def test_a_parallax_without_a_usable_uncertainty_fails_the_cut(error: float) -> None:
    table = field("pleiades").slice(0, 5)
    i = table.schema.get_field_index("parallax_mas_err")
    broken = table.set_column(i, "parallax_mas_err", pa.array([error] * 5))
    assert not CUTS.masks(broken)["parallax_over_error"].any()


def test_the_grid_is_expanded_in_predeclared_order() -> None:
    p = load_predeclaration()["parameters"]
    configs = configurations(p["algorithms"], p["hyperparameter_grid"])
    assert len(configs) == 10
    assert configs[0].key == "sklearn-hdbscan|min_cluster_size=10,min_samples=5"
    assert configs[-1].key == "sklearn-dbscan|eps=0.5,min_samples=20"
    with pytest.raises(ValueError, match="empty"):
        configurations([], {})


def test_membership_and_stability_come_from_agreement_across_the_grid() -> None:
    labels = np.array([0] * 20 + [1] * 20 + [-1] * 10)
    probs = np.where(labels >= 0, 1.0, 0.0)
    shifted = labels.copy()
    shifted[:10] = -1  # half of cluster 0 lost in the second labelling
    docs = [
        {"labels": labels, "probabilities": probs},
        {"labels": shifted, "probabilities": np.where(shifted >= 0, 1.0, 0.0)},
    ]
    a, b = estimate("r", docs)
    assert a.candidate_id == "r-c000" and a.stability == pytest.approx(0.75) and a.members.size == 20
    assert b.stability == 1.0 and np.all(b.membership == 1.0)


@pytest.mark.req("STL-006")
def test_null_fields_are_seeded_and_destroy_the_planted_kinematics() -> None:
    kept = _kept()
    a, b, c = null_field(kept, 3), null_field(kept, 3), null_field(kept, 4)
    assert a.equals(b) and not a.equals(c)
    for name in ("ra_deg", "dec_deg", "g_mag", "bp_rp"):
        assert a.column(name).equals(kept.column(name))
    assert sorted(column(a, "pm_ra_cosdec_masyr")) == sorted(column(kept, "pm_ra_cosdec_masyr"))
    planted = np.asarray(["SYN-A" in k for k in kept.column("source_key").to_pylist()])
    near = np.hypot(column(a, "pm_ra_cosdec_masyr") - 20.0, column(a, "pm_dec_masyr") + 45.0) < 1.0
    assert near[planted].mean() < 0.2


@pytest.mark.req("STL-006")
def test_injection_is_seeded_and_scored_by_best_overlap() -> None:
    control = next(t for t in targets() if t["target_id"] == "ngp-control")["spec"]
    kept = CUTS.apply(field("ngp-control"))[0]
    region = {k: control[k] for k in ("ra_deg", "dec_deg", "radius_deg")}
    t1, m1 = inject(kept, region, 50, 1000.0, 0.3, seed=9)
    t2, _ = inject(kept, region, 50, 1000.0, 0.3, seed=9)
    assert t1.equals(t2) and int(m1.sum()) == 50 and t1.num_rows == kept.num_rows + 50
    fm = apply_policy("fsp-v1", t1, region["ra_deg"], region["dec_deg"])
    assert recovery(np.asarray(run(SklearnHdbscan(), fm, HDBSCAN, 1).labels), m1)["recovered"]
    swallowed = np.zeros(m1.size, dtype=np.int64)  # one cluster holding everything
    assert recovery(swallowed, m1) == {"recovered": False, "completeness": 1.0, "purity": pytest.approx(50 / m1.size)}


def test_ranking_is_reproducible_whatever_the_input_order() -> None:
    cands = [
        {"candidate_id": f"r-c{i:03d}", "mean_membership": m, "stability": s, "cmd_coherence": c, "status": st}
        for i, (m, s, c, st) in enumerate(
            [
                (0.9, 0.9, 20.0, "known"),
                (0.9, 0.9, 20.0, "potentially_new"),
                (0.5, 0.2, None, "rejected"),
                (0.9, 0.9, 20.0, "known"),
            ]
        )
    ]
    a = ranking.rank(cands)
    b = ranking.rank(list(reversed(cands)))
    assert [c["candidate_id"] for c in a] == [c["candidate_id"] for c in b] == ["r-c001", "r-c000", "r-c003", "r-c002"]
    assert [c["rank"] for c in a] == [1, 2, 3, 4]


@pytest.mark.gate("STL-G-09")
def test_the_claim_lint_accepts_the_fixed_sentence_and_flags_discovery_language() -> None:
    assert dossier.claim_violations([dossier.statement("pleiades-c004")]) == []
    flagged = dossier.claim_violations(
        ["The cluster passed every check. We discovered a new open cluster in Praesepe."]
    )
    assert flagged == ["We discovered a new open cluster in Praesepe."]
    assert dossier.claim_violations(["praesepe-c001 is potentially new and not a confirmed discovery."]) == []
    assert dossier.claim_violations(["We discovered a new cluster candidate."]) == [
        "We discovered a new cluster candidate."
    ]


def test_labelling_documents_record_the_backend() -> None:
    fm = apply_policy("fsp-v1", _kept(), *CENTRE)
    doc = labelling_document(run(SklearnHdbscan(), fm, HDBSCAN, 1), HDBSCAN, "cpu")
    assert doc["backend"] == "cpu" and doc["config"] == HDBSCAN.key and doc["labels"].shape == (fm.values.shape[0],)


def test_the_packaged_predeclaration_is_the_reviewed_draft() -> None:
    from pathlib import Path

    draft = Path(__file__).parents[3] / "experiments" / "uc01-stellar-clusters" / "predeclaration.json"
    assert json.loads(draft.read_text("utf-8")) == load_predeclaration()

"""The exact records of the UC-01 parts on hand-built inputs: grid keys, refusals, membership and
stability values, recovery scores at their thresholds, null-field binning and injected rows."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from sdp.kernel.ports.compute import ClusterLabels
from sdp.kernel.science.plan import Backend
from sdp_stellar import dossier, synthetic
from sdp_stellar.benchmark import benchmark_from
from sdp_stellar.features import FeatureMatrix, PolicyError
from sdp_stellar.inject import CORE_RADIUS_PC, inject, recovery
from sdp_stellar.membership import MEMBER_THRESHOLD, Candidate, estimate, jaccard
from sdp_stellar.null import BIN_MAG, null_field
from sdp_stellar.protocol import load_predeclaration
from sdp_stellar.quality import column
from sdp_stellar.search import Configuration, configurations, labelling_document, run


class _Echo:
    """A clusterer that records what it was asked and labels every row 0."""

    backend = Backend.CPU

    def __init__(self, algorithm_id: str) -> None:
        self.algorithm_id = algorithm_id
        self.calls: list[tuple[Any, ...]] = []

    def search(self, values: np.ndarray, params: Mapping[str, Any], seed: int) -> ClusterLabels:
        self.calls.append((values, dict(params), seed))
        return ClusterLabels(np.zeros(len(values), dtype=np.int64), np.ones(len(values)), self.algorithm_id, params)


def _features(n: int = 3) -> FeatureMatrix:
    return FeatureMatrix(np.zeros((n, 5)), "fsp-v1", ("xi", "eta", "pm_ra_cosdec", "pm_dec", "parallax"), (1.0,) * 5)


def _digest(table: pa.Table) -> str:
    """A fingerprint of a table's rows, floats to nine decimals, so a seeded fixture cannot drift."""

    def norm(v: Any) -> Any:
        if isinstance(v, float):
            return round(v, 9)
        return [norm(x) for x in v] if isinstance(v, list) else v

    rows = [{k: norm(v) for k, v in r.items()} for r in table.to_pylist()]
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()


# --- search ------------------------------------------------------------------------------------


def test_a_configuration_key_lists_parameters_in_name_order() -> None:
    assert Configuration("a", {"z": 1, "b": 0.5}).key == "a|b=0.5,z=1"
    assert Configuration("a", {}).key == "a|"


def test_the_grid_expands_each_algorithm_in_order_and_an_algorithm_with_no_axes_runs_once() -> None:
    configs = configurations(["x", "y"], {"x": {"n": [1, 2], "a": ["p"]}})
    assert [(c.algorithm_id, dict(c.params)) for c in configs] == [
        ("x", {"a": "p", "n": 1}),
        ("x", {"a": "p", "n": 2}),
        ("y", {}),
    ]
    with pytest.raises(ValueError, match=r"^the hyperparameter grid is empty$"):
        configurations(["x"], {"x": {"n": []}})


def test_a_grid_point_runs_on_its_own_service_or_an_admitted_stand_in_only() -> None:
    features = _features()
    config = Configuration("sk", {"k": 2})
    own = _Echo("sk")
    assert run(own, features, config, 5).algorithm_id == "sk"
    assert own.calls[0][1:] == ({"k": 2}, 5) and own.calls[0][0] is features.values
    assert run(_Echo("cu"), features, config, 5, replaces="sk").algorithm_id == "cu"
    with pytest.raises(ValueError, match=r"^service cu cannot run sk$"):
        run(_Echo("cu"), features, config, 5)
    with pytest.raises(ValueError, match=r"^service cu cannot run sk$"):
        run(_Echo("cu"), features, config, 5, replaces="other")
    with pytest.raises(
        PolicyError, match=r"^clustering takes a FeatureMatrix from a registered policy, never raw columns \(STL-002\)$"
    ):
        run(own, np.zeros((3, 5)), config, 5)  # type: ignore[arg-type]


def test_a_labelling_document_records_both_ids_the_backend_and_typed_arrays() -> None:
    result = ClusterLabels(np.array([0.0, -1.0, 1.0, 1.0]), np.array([1, 0, 1, 1]), "cuml-hdbscan")
    doc = labelling_document(result, Configuration("sklearn-hdbscan", {"m": 5}), "gpu")
    labels, probs = doc.pop("labels"), doc.pop("probabilities")
    assert doc == {
        "config": "sklearn-hdbscan|m=5",
        "algorithm_id": "sklearn-hdbscan",
        "implementation_id": "cuml-hdbscan",
        "params": {"m": 5},
        "backend": "gpu",
        "n_clusters": 2,
    }
    assert labels.dtype == np.int64 and labels.tolist() == [0, -1, 1, 1]
    assert probs.dtype == np.float64 and probs.tolist() == [1.0, 0.0, 1.0, 1.0]


# --- membership --------------------------------------------------------------------------------


def test_jaccard_of_two_index_sets_and_of_two_empty_ones() -> None:
    assert jaccard(np.array([0, 1, 2]), np.array([1, 2, 3, 4])) == pytest.approx(2 / 5)
    assert jaccard(np.array([], dtype=int), np.array([], dtype=int)) == 0.0


def test_membership_averages_matched_probabilities_and_stability_the_best_overlaps() -> None:
    reference = {"labels": np.array([0, 0, 1, 1, -1]), "probabilities": np.array([1.0, 0.8, 0.6, 1.0, 0.0])}
    other = {"labels": np.array([5, 5, 5, -1, -1]), "probabilities": np.array([0.9, 0.9, 0.4, 0.0, 0.0])}
    a, b = estimate("R", [reference, other])
    assert (a.candidate_id, a.members.tolist(), a.reference_size) == ("R-c000", [0, 1], 2)
    assert a.membership.tolist() == pytest.approx([0.95, 0.85])
    assert a.stability == pytest.approx((1 + 2 / 3) / 2)
    assert a.mean_membership == pytest.approx(0.9)
    # Cluster 1 matches 5 with overlap 1/4; rows 2 and 3 sit exactly at the member threshold.
    assert (b.candidate_id, b.members.tolist(), b.reference_size) == ("R-c001", [2, 3], 2)
    assert b.membership.tolist() == pytest.approx([MEMBER_THRESHOLD, MEMBER_THRESHOLD])
    assert b.stability == pytest.approx(0.625)


def test_a_tie_between_equally_overlapping_clusters_goes_to_the_lower_label() -> None:
    reference = {"labels": np.array([0, 0]), "probabilities": np.array([1.0, 1.0])}
    split = {"labels": np.array([3, 4]), "probabilities": np.array([1.0, 0.2])}
    (c,) = estimate("R", [reference, split])
    assert c.stability == 0.75 and c.members.tolist() == [0, 1] and c.membership.tolist() == [1.0, 0.5]


def test_a_labelling_with_no_overlap_adds_nothing_and_no_labellings_give_no_candidates() -> None:
    reference = {"labels": np.array([0, 0, -1]), "probabilities": np.array([1.0, 1.0, 0.0])}
    noise = {"labels": np.array([-1, -1, 3]), "probabilities": np.array([0.0, 0.0, 1.0])}
    (c,) = estimate("R", [reference, noise])
    assert c.stability == 0.5 and c.members.tolist() == [0, 1] and c.membership.tolist() == [0.5, 0.5]
    assert estimate("R", []) == []
    assert Candidate("x", np.array([], dtype=int), np.array([]), 0.0, 0).mean_membership == 0.0


# --- recovery ----------------------------------------------------------------------------------


def test_recovery_takes_the_best_jaccard_match_and_holds_at_the_threshold() -> None:
    labels = np.array([0, 0, 1, 1, 1, -1, 2])
    injected = np.array([False, False, True, True, False, True, True])
    # cluster 1: 2 of 4 injected among 3 members, Jaccard 2/5; cluster 2: 1 of 4, Jaccard 1/4
    assert recovery(labels, injected) == {"recovered": True, "completeness": 0.5, "purity": pytest.approx(2 / 3)}
    assert recovery(labels, injected, threshold=0.6)["recovered"] is False
    assert recovery(labels, injected, threshold=2 / 3) == {
        "recovered": False,
        "completeness": 0.5,
        "purity": pytest.approx(2 / 3),
    }
    assert recovery(np.array([-1, -1]), np.array([True, True])) == {
        "recovered": False,
        "completeness": 0.0,
        "purity": 0.0,
    }


def test_recovery_ranks_by_jaccard_and_keeps_the_lower_label_on_a_tie() -> None:
    injected = np.array([True] * 4 + [False] * 4)
    # label 0: 1 of 4 injected in 1 member (Jaccard 1/4); label 1: 2 of 4 in 6 members (Jaccard 2/8)
    tie = np.array([0, 1, 1, -1, 1, 1, 1, 1])
    assert recovery(tie, injected) == {"recovered": False, "completeness": 0.25, "purity": 1.0}
    # label 0: 2 of 5 injected in 2 members (Jaccard 2/5) beats label 1: 2 of 5 in 6 members (Jaccard 2/9)
    injected = np.array([True] * 5 + [False] * 4)
    labels = np.array([0, 0, 1, 1, -1, 1, 1, 1, 1])
    assert recovery(labels, injected) == {"recovered": False, "completeness": 0.4, "purity": 1.0}


def test_recovery_needs_both_completeness_and_purity() -> None:
    injected = np.array([True, True, False, False, False, False])
    pure_part = np.array([0, -1, -1, -1, -1, -1])  # completeness 1/2, purity 1
    assert recovery(pure_part, injected) == {"recovered": True, "completeness": 0.5, "purity": 1.0}
    diluted = np.array([0, 0, 0, 0, 0, -1])  # completeness 1, purity 2/5
    assert recovery(diluted, injected) == {"recovered": False, "completeness": 1.0, "purity": 0.4}
    half_pure = np.array([0, 0, 0, 0, -1, -1])  # completeness 1, purity exactly 1/2
    assert recovery(half_pure, injected) == {"recovered": True, "completeness": 1.0, "purity": 0.5}
    partial = np.array([0, -1, -1, -1, -1, -1])
    assert recovery(partial, np.array([True, True, True, False, False, False]))["recovered"] is False


# --- null fields -------------------------------------------------------------------------------


def _null_input() -> pa.Table:
    g = [0.1, 0.2, 0.3, 0.4, 0.6, 0.7, 0.8, 0.9, 0.5, 0.45]
    n = len(g)
    base = np.arange(n, dtype=float)
    return pa.table(
        {
            "source_key": [f"s{i}" for i in range(n)],
            "g_mag": g,
            "pm_ra_cosdec_masyr": base,
            "pm_ra_cosdec_masyr_err": base * 10,
            "pm_dec_masyr": base + 100,
            "pm_dec_masyr_err": (base + 100) * 10,
            "parallax_mas": base + 200,
        }
    )


def test_null_fields_permute_within_half_magnitude_bins_and_carry_each_error() -> None:
    table = _null_input()
    assert BIN_MAG == 0.5
    bins = {0: [0, 1, 2, 3, 9], 1: [4, 5, 6, 7, 8]}  # g 0.45 is in bin 0, g 0.5 in bin 1
    shuffled, independent = False, False
    for seed in range(6):
        out = null_field(table, seed)
        assert out.column("source_key").equals(table.column("source_key"))
        assert out.column("g_mag").equals(table.column("g_mag"))
        pmra, pmdec, plx = (column(out, c) for c in ("pm_ra_cosdec_masyr", "pm_dec_masyr", "parallax_mas"))
        assert column(out, "pm_ra_cosdec_masyr_err").tolist() == (pmra * 10).tolist()
        assert column(out, "pm_dec_masyr_err").tolist() == (pmdec * 10).tolist()
        assert "parallax_mas_err" not in out.column_names
        for rows in bins.values():
            assert sorted(pmra[rows].tolist()) == sorted(float(r) for r in rows)
            assert sorted(plx[rows].tolist()) == sorted(float(r) + 200 for r in rows)
        shuffled |= pmra.tolist() != list(range(10))
        independent |= pmra.tolist() != (pmdec - 100).tolist()
    assert shuffled and independent


def test_a_star_with_no_magnitude_is_binned_with_the_faintest() -> None:
    table = pa.table(
        {
            "g_mag": [None, 99.1, 99.2, 5.0],
            "pm_ra_cosdec_masyr": [1.0, 2.0, 3.0, 4.0],
            "pm_dec_masyr": [1.0, 2.0, 3.0, 4.0],
            "parallax_mas": [1.0, 2.0, 3.0, 4.0],
        }
    )
    seen = {tuple(column(null_field(table, seed), "pm_ra_cosdec_masyr").tolist()) for seed in range(20)}
    assert {s[3] for s in seen} == {4.0}
    assert {s[0] for s in seen} == {1.0, 2.0, 3.0}


# --- injection ---------------------------------------------------------------------------------


def _injection_field(n: int, err: float | None) -> pa.Table:
    rng = np.random.default_rng(0)
    return pa.table(
        {
            "source_key": [f"f{i}" for i in range(n)],
            "source_release": ["field"] * n,
            "ra_deg": rng.uniform(149, 151, n),
            "dec_deg": rng.uniform(-1, 1, n),
            "pm_ra_cosdec_masyr": rng.normal(-3, 2, n),
            "pm_dec_masyr": rng.normal(4, 2, n),
            "parallax_mas": rng.normal(1, 0.3, n),
            "parallax_mas_err": pa.array([err] * n, type=pa.float64()),
            "g_mag": rng.uniform(10, 18, n),
        }
    )


def test_injected_members_share_a_parallax_a_motion_and_a_core() -> None:
    table = _injection_field(50, 0.02)
    region = {"ra_deg": 150.0, "dec_deg": 0.0, "radius_deg": 1.0}
    out, mask = inject(table, region, 400, 500.0, 0.25, seed=3)
    assert out.num_rows == 450 and mask.tolist() == [False] * 50 + [True] * 400
    assert out.slice(0, 50).equals(table)
    added = out.slice(50)
    assert added.column("source_key").to_pylist() == [f"synthetic:injected:3:{i}" for i in range(400)]
    assert set(added.column("source_release").to_pylist()) == {"synthetic:injected"}
    plx = column(added, "parallax_mas")
    assert plx.mean() == pytest.approx(2.0, abs=0.01) and plx.std() == pytest.approx(0.02, rel=0.15)
    for name in ("pm_ra_cosdec_masyr", "pm_dec_masyr"):
        values = column(added, name)
        assert values.std() == pytest.approx(0.25, rel=0.15)
        assert np.abs(column(table, name) - values.mean()).min() < 0.05  # the centre is one field star's motion
    core = np.degrees(CORE_RADIUS_PC / 500.0)
    dec, ra = column(added, "dec_deg"), column(added, "ra_deg")
    assert dec.std() == pytest.approx(core, rel=0.15) and ra.std() == pytest.approx(core, rel=0.15)
    assert abs(dec.mean()) < 0.5 and abs(ra.mean() - 150.0) < 0.5


def test_injection_falls_back_to_a_0_05_mas_parallax_error_and_wraps_right_ascension() -> None:
    table = _injection_field(10, None)
    region = {"ra_deg": 0.0, "dec_deg": 0.0, "radius_deg": 0.0}
    out, mask = inject(table, region, 300, 1000.0, 0.1, seed=1)  # more members than field stars: drawn with replacement
    assert int(mask.sum()) == 300
    added = out.slice(10)
    assert column(added, "parallax_mas").std() == pytest.approx(0.05, rel=0.15)
    ra = column(added, "ra_deg")
    assert ((ra >= 0) & (ra < 360)).all() and (ra > 359).any() and (ra < 1).any()


# --- dossier -----------------------------------------------------------------------------------


def test_a_dossier_record_carries_the_fixed_sentence_and_every_check() -> None:
    candidate = {
        "candidate_id": "r-c001",
        "status": "candidate",
        "reasons": ("a", "b"),
        "n_members": 40,
        "mean_membership": 0.8,
        "stability": 0.9,
        "cmd_coherence": 0.7,
        "crossmatch": None,
        "centre": {"ra_deg": 1.0},
    }
    assert dossier.build(candidate) == {
        "candidate_id": "r-c001",
        "statement": "r-c001 is a candidate open cluster, not a confirmed discovery.",
        "status": "candidate",
        "reasons": ["a", "b"],
        "checks": {"members": 40, "mean_membership": 0.8, "stability": 0.9, "cmd_coherence": 0.7, "crossmatch": None},
        "evidence": None,
        "centre": {"ra_deg": 1.0},
        "rank": None,
        "score": None,
    }
    full = dossier.build({**candidate, "evidence": {"e": 1}, "rank": 2, "score": 0.5})
    assert (full["evidence"], full["rank"], full["score"]) == ({"e": 1}, 2, 0.5)


def test_the_claim_lint_splits_sentences_and_names_each_claiming_one() -> None:
    text = (
        "We DISCOVERED a group!  It is a new open cluster? This is a confirmed cluster. "
        "These discoveries matter. A new cluster here.\nr-c001 is a candidate open cluster, not a confirmed discovery. "
        "A candidate discovery. Undiscovered is fine. Rediscovery is fine."
    )
    assert dossier.claim_violations([text, "a new  cluster", "discovery"]) == [
        "We DISCOVERED a group!",
        "It is a new open cluster?",
        "This is a confirmed cluster.",
        "These discoveries matter.",
        "A new cluster here.",
        "A candidate discovery.",
        "discovery",
    ]


# --- synthetic fields --------------------------------------------------------------------------


_TARGET = {"target_id": "t", "spec": {"ra_deg": 359.9, "dec_deg": 10.0, "radius_deg": 0.5}}
_PLANT = synthetic.PlantedCluster("SYN-T", "t", 0.2, -0.1, (5.0, -6.0), 200.0, 60, ("bm",))


def test_a_synthetic_field_plants_its_clusters_and_says_it_is_not_gaia() -> None:
    table = synthetic.make_field(_TARGET, 4, stars=300, planted=[_PLANT])
    assert table.schema.remove_metadata() == synthetic.SCHEMA
    assert table.schema.metadata == {
        b"sdp.synthetic": b"Synthetic field with planted clusters; not Gaia data.",
        b"sdp.seed": b"4",
    }
    keys = table.column("source_key").to_pylist()
    assert keys[:2] == ["synthetic:t:field:0", "synthetic:t:field:1"] and keys[300:302] == [
        "synthetic:t:SYN-T:0",
        "synthetic:t:SYN-T:1",
    ]
    assert table.num_rows == 360
    rows = table.to_pylist()
    assert {r["source_release"] for r in rows} == {"synthetic:fields-v1"}
    assert {r["epoch_jyear"] for r in rows} == {2016.0} and {r["zero_point_corrected"] for r in rows} == {False}
    assert {r["astrometric_corr"] for r in rows} == {None}
    for r in rows:
        assert r["quality_class"] == ("good" if r["ruwe"] < 1.4 else "poor")
        flags = json.loads(r["quality_flags"])
        assert flags["ruwe"] == r["ruwe"] and flags["astrometric_params_solved"] in (31, 95)
        assert r["parallax_mas_err"] == pytest.approx(0.015 + 0.02 * 10 ** (0.2 * (r["g_mag"] - 15.0)))
        assert r["pm_ra_cosdec_masyr_err"] == pytest.approx(1.5 * r["parallax_mas_err"])
        assert 0 <= r["ra_deg"] < 360
    field_rows, members = rows[:300], rows[300:]
    ruwe = np.array([r["ruwe"] for r in field_rows])
    assert (ruwe > 0).all() and len(set(ruwe.tolist())) == 300 and 3 <= int((ruwe > 1.8).sum()) <= 30
    assert all(6.0 <= r["g_mag"] <= 18.5 for r in field_rows)
    assert np.mean([r["pm_ra_cosdec_masyr"] for r in members]) == pytest.approx(5.0, abs=0.2)
    assert np.mean([r["pm_dec_masyr"] for r in members]) == pytest.approx(-6.0, abs=0.2)
    assert np.std([r["pm_dec_masyr"] for r in members]) < 0.6
    assert np.median([r["parallax_mas"] for r in members]) == pytest.approx(5.0, abs=0.05)
    assert np.mean([r["dec_deg"] for r in members]) == pytest.approx(9.9, abs=0.1)
    distance_modulus = 5 * np.log10(20.0)
    assert all(0.5 + distance_modulus <= r["g_mag"] <= 8.0 + distance_modulus for r in members)
    assert synthetic.make_field(_TARGET, 4, stars=300, planted=[_PLANT]).equals(table)
    assert not synthetic.make_field({**_TARGET, "target_id": "u"}, 4, stars=300, planted=[_PLANT]).equals(table)


def test_a_synthetic_field_fills_its_cone_uniformly_in_area() -> None:
    table = synthetic.make_field(_TARGET, 5, stars=2000, planted=[])
    ra, dec = column(table, "ra_deg"), column(table, "dec_deg")
    assert (ra > 359).any() and (ra < 1).any() and ((ra >= 0) & (ra < 360)).all()
    east = ((ra + 180.0) % 360.0) - 180.0 - (359.9 - 360.0)
    x, y = east * np.cos(np.radians(dec)), dec - 10.0
    r = np.hypot(x, y)
    assert r.max() <= 0.5 + 1e-9 and r.max() > 0.49
    assert np.mean(r < 0.25) == pytest.approx(0.25, abs=0.04)  # uniform in area, not in radius
    for qx, qy in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
        assert np.mean((np.sign(x) == qx) & (np.sign(y) == qy)) == pytest.approx(0.25, abs=0.04)


def test_the_seeded_fixtures_are_exactly_reproducible() -> None:
    far = synthetic.PlantedCluster("SYN-F", "t", -0.1, 0.1, (1.0, 2.0), 900.0, 20, ("bm",))
    field = synthetic.make_field(_TARGET, 4, stars=300, planted=[_PLANT, far])
    assert _digest(field) == "1814dcebedf225a20b93ad4862e6bbf905913723cec6531fcb04411cad88c18e"
    bench = synthetic.make_benchmark("bm", [_TARGET], "bm:v1", planted=[_PLANT, far])
    assert _digest(bench) == "47671d748cfc280c230fd926bc141a65dea8047d0d064bb98f6f40ea44a30c2b"
    assert synthetic.make_field(_TARGET, 4, planted=[]).num_rows == 1500
    region = {"ra_deg": 150.0, "dec_deg": 30.0, "radius_deg": 1.0}
    injected, _ = inject(_injection_field(20, 0.02), region, 20, 800.0, 0.2, seed=6)
    assert _digest(injected) == "db5a6241dc8ab34ca6e0cafe8896fbd7a1ad370fa4b5fb2e2e5a5b51d1a39dc9"


def test_a_synthetic_benchmark_lists_the_planted_clusters_it_names() -> None:
    other = synthetic.PlantedCluster("SYN-U", "t", 0.0, 0.0, (1.0, 2.0), 1000.0, 10, ("other",))
    elsewhere = synthetic.PlantedCluster("SYN-V", "nowhere", 0.0, 0.0, (1.0, 2.0), 1000.0, 10, ("bm",))
    table = synthetic.make_benchmark("bm", [_TARGET], "bm:v1", planted=[other, elsewhere, _PLANT])
    (row,) = table.to_pylist()
    assert row == {
        "name": "SYN-T",
        "ra_deg": pytest.approx((359.9 + 0.2 / np.cos(np.radians(9.9))) % 360.0),
        "dec_deg": pytest.approx(9.9),
        "pm_ra_cosdec_masyr": 5.0,
        "pm_dec_masyr": -6.0,
        "parallax_mas": 5.0,
    }
    assert row["ra_deg"] < 1.0
    read = benchmark_from("bm", table)
    assert (read.release, read.licence, read.citation) == (
        "bm:v1",
        "CC0-1.0 (synthetic)",
        "SDP synthetic benchmark bm; not the published catalogue",
    )
    empty = synthetic.make_benchmark("none", [_TARGET], "none:v1", planted=[_PLANT])
    assert empty.num_rows == 0 and [(f.name, str(f.type)) for f in empty.schema] == [
        ("name", "string"),
        ("ra_deg", "double"),
        ("dec_deg", "double"),
        ("pm_ra_cosdec_masyr", "double"),
        ("pm_dec_masyr", "double"),
        ("parallax_mas", "double"),
    ]


def test_the_synthetic_command_writes_a_field_per_region_and_a_benchmark_per_label(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "nested" / "out"
    synthetic.main([str(out), "--seed", "03", "--stars", "040"])
    pre = load_predeclaration()
    expected = {f"field-{t['target_id']}.parquet" for t in pre["targets"]}
    expected |= {f"benchmark-{label.split(':')[0]}.parquet" for label in pre["parameters"]["benchmarks"]}
    assert {p.name for p in out.iterdir()} == expected
    first = pre["targets"][0]
    written = pq.read_table(out / f"field-{first['target_id']}.parquet")
    assert written.equals(synthetic.make_field(first, 3, 40)) and written.schema.metadata[b"sdp.seed"] == b"3"
    for label in pre["parameters"]["benchmarks"]:
        bid = label.split(":")[0]
        assert pq.read_table(out / f"benchmark-{bid}.parquet").equals(
            synthetic.make_benchmark(bid, pre["targets"], label)
        )
    assert capsys.readouterr().out == f"wrote {out}\n"
    synthetic.main([str(out)])
    capsys.readouterr()
    with pytest.raises(SystemExit):
        synthetic.main(["--help"])
    assert capsys.readouterr().out.startswith(
        "usage: python -m sdp_stellar.synthetic [-h] [--seed SEED] [--stars STARS] out"
    )
    assert pq.read_table(out / f"field-{first['target_id']}.parquet").equals(synthetic.make_field(first, 7, 1500))

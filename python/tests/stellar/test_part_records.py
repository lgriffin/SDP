"""The exact records of the UC-01 checks on hand-built inputs: benchmark problems and matches at their
edges, CMD coherence, every quality cut at its boundary, the candidate score and the feature policy."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import numpy as np
import pyarrow as pa
import pytest

from sdp.kernel.domain.investigation import RankingPolicy, Subject, TestResult
from sdp_stellar import cmd, ranking
from sdp_stellar.benchmark import Benchmark, benchmark_from, centre, cross_match, recovered, with_metadata
from sdp_stellar.features import MAD_TO_SIGMA, FeatureMatrix, PolicyError, apply_policy, fsp_v1, tangent_plane
from sdp_stellar.quality import QualityCuts, require_columns

pytestmark = pytest.mark.tier("T1")


# --- benchmarks --------------------------------------------------------------------------------


def _clusters(rows: list[tuple[str, float, float, float, float]]) -> pa.Table:
    names = ("name", "ra_deg", "dec_deg", "pm_ra_cosdec_masyr", "pm_dec_masyr")
    return pa.table({n: [r[i] for r in rows] for i, n in enumerate(names)})


def test_a_benchmark_names_every_missing_record_and_column() -> None:
    bare = Benchmark("b", "", " ", "c", pa.table({"name": ["x"], "ra_deg": [1.0]}))
    assert bare.problems("v1") == [
        "b: no release",
        "b: no licence",
        "b: release (none) is not the predeclared v1",
        "b: table lacks ['dec_deg', 'pm_dec_masyr', 'pm_ra_cosdec_masyr']",
    ]
    good = Benchmark("b", "v1", "CC0", "c", _clusters([]))
    assert good.problems("v1") == [] and good.missing_columns() == []
    assert good.problems("v2") == ["b: release v1 is not the predeclared v2"]


def test_a_benchmark_reads_its_release_licence_and_citation_from_the_schema() -> None:
    table = with_metadata(_clusters([]), "r1", "CC0", "Someone 2023")
    assert table.schema.metadata == {
        b"sdp.benchmark.release": b"r1",
        b"sdp.benchmark.licence": b"CC0",
        b"sdp.benchmark.citation": b"Someone 2023",
    }
    b = benchmark_from("hr23", table)
    assert (b.benchmark_id, b.release, b.licence, b.citation) == ("hr23", "r1", "CC0", "Someone 2023")
    plain = benchmark_from("x", _clusters([]))
    assert (plain.release, plain.licence, plain.citation) == ("", "", "")


def test_a_candidate_centre_is_the_mean_direction_and_median_motion() -> None:
    table = pa.table(
        {
            "ra_deg": [359.0, 1.0, 0.0],
            "dec_deg": [10.0, 10.0, 10.0],
            "pm_ra_cosdec_masyr": [1.0, 2.0, 9.0],
            "pm_dec_masyr": [-1.0, -2.0, -9.0],
            "parallax_mas": [3.0, 4.0, 5.0],
        }
    )
    c = centre(table, np.array([0, 1, 2]))
    assert c["ra_deg"] == pytest.approx(0.0, abs=1e-9) or c["ra_deg"] == pytest.approx(360.0)
    assert c["dec_deg"] == pytest.approx(10.0, abs=1e-3) and c["dec_deg"] > 10.0  # the chord bows toward the pole
    assert (c["pm_ra_cosdec_masyr"], c["pm_dec_masyr"], c["parallax_mas"]) == (2.0, -2.0, 4.0)
    west = centre(table, np.array([0]))
    assert west["ra_deg"] == pytest.approx(359.0) and west["dec_deg"] == pytest.approx(10.0)


def test_a_cross_match_keeps_clusters_inside_both_windows_nearest_then_by_name() -> None:
    bench = Benchmark(
        "b",
        "v1",
        "CC0",
        "c",
        _clusters(
            [
                ("far", 10.0, 0.0, 0.0, 0.0),
                ("zeta", 0.0, 0.1, 0.0, 0.0),
                ("alpha", 0.0, -0.1, 0.0, 0.0),
                ("edge-pm", 0.0, 0.0, 3.0, 4.0),
                ("fast", 0.0, 0.0, 3.0, 4.1),
                ("here", 0.0, 0.0, 0.0, 0.0),
            ]
        ),
    )
    c = {"ra_deg": 0.0, "dec_deg": 0.0, "pm_ra_cosdec_masyr": 0.0, "pm_dec_masyr": 0.0}
    result = cross_match(c, bench, 0.1, 5.0)
    assert (result["benchmark"], result["release"]) == ("b", "v1")
    assert [(m["name"], round(m["separation_deg"], 9), round(m["pm_offset_masyr"], 9)) for m in result["matches"]] == [
        ("edge-pm", 0.0, 5.0),
        ("here", 0.0, 0.0),
        ("alpha", 0.1, 0.0),
        ("zeta", 0.1, 0.0),
    ]
    empty = Benchmark("e", "v2", "CC0", "c", _clusters([]))
    assert cross_match(c, empty, 0.1, 5.0) == {"benchmark": "e", "release": "v2", "matches": []}


def test_recovery_covers_only_benchmark_clusters_inside_the_region() -> None:
    bench = Benchmark(
        "b",
        "v1",
        "CC0",
        "c",
        _clusters([("found", 0.0, 0.0, 0.0, 0.0), ("missed", 0.0, 0.5, 9.0, 9.0), ("outside", 5.0, 0.0, 0.0, 0.0)]),
    )
    region = {"ra_deg": 0.0, "dec_deg": 0.0, "radius_deg": 0.5}
    c = {"ra_deg": 0.0, "dec_deg": 0.0, "pm_ra_cosdec_masyr": 0.0, "pm_dec_masyr": 0.0}
    assert recovered(bench, [c], region, 0.1, 1.0) == {"found": True, "missed": False}
    assert recovered(bench, [], region, 0.1, 1.0) == {"found": False, "missed": False}


# --- CMD coherence -----------------------------------------------------------------------------


def test_absolute_magnitude_is_g_plus_five_log_parallax_over_100_mas() -> None:
    assert cmd.absolute_g(np.array([10.0, 12.0]), np.array([10.0, 100.0])).tolist() == [5.0, 12.0]
    with np.errstate(all="raise"):
        assert np.isneginf(cmd.absolute_g(np.array([10.0]), np.array([0.0])))[0]
        assert np.isnan(cmd.absolute_g(np.array([10.0]), np.array([-1.0])))[0]


def _cmd_field(n_members: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(1)
    colour = rng.uniform(0.3, 2.5, 200)
    mag = 2.0 + 3.0 * colour + rng.normal(0, 1.0, 200)
    members = np.arange(n_members)
    mag[members] = 2.0 + 3.0 * colour[members] + 0.5 * colour[members] ** 2 + rng.normal(0, 0.05, n_members)
    return members, colour, mag


def test_coherence_is_the_median_field_scatter_over_the_member_scatter() -> None:
    members, colour, mag = _cmd_field(20)
    value = cmd.coherence(members, colour, mag, seed=4, draws=7)
    assert value is not None and value > 5
    assert cmd.coherence(members, colour, mag, seed=4, draws=7) == value
    assert cmd.coherence(members, colour, mag, seed=5, draws=7) != value
    # by hand: the same draws, the same fits
    rng = np.random.default_rng(4)
    field = np.arange(20, 200)
    scatter = [cmd._scatter(colour[s], mag[s]) for s in (rng.choice(field, 20, replace=False) for _ in range(7))]
    assert value == pytest.approx(float(np.median(scatter)) / cmd._scatter(colour[members], mag[members]))


def test_coherence_needs_eight_coloured_members_and_a_field_as_large() -> None:
    assert cmd.MIN_WITH_COLOUR == 8
    members, colour, mag = _cmd_field(9)
    assert cmd.coherence(members, colour, mag, seed=1, draws=3) is not None
    colour[0] = np.nan  # eight left with a colour
    assert cmd.coherence(members, colour, mag, seed=1, draws=3) is not None
    mag[1] = np.inf  # seven
    assert cmd.coherence(members, colour, mag, seed=1, draws=3) is None
    small = np.arange(10)
    c = np.linspace(0.3, 2.0, 19)
    m = 1.0 + c
    assert cmd.coherence(small, c, m, seed=1, draws=3) is None  # nine field stars for ten members
    assert cmd.coherence(np.arange(9), c, m, seed=1, draws=3) is not None


def test_a_perfect_sequence_scores_against_a_floor_not_a_zero() -> None:
    c = np.linspace(0.3, 2.0, 40)
    m = 1.0 + 2.0 * c
    m[10:] += np.random.default_rng(0).normal(0, 0.5, 30)
    value = cmd.coherence(np.arange(10), c, m, seed=2, draws=3)
    assert value is not None and value > 1e4
    assert cmd._scatter(c[:10], m[:10]) == pytest.approx(0.0, abs=1e-9)
    line = np.array([0.0, 1.0, 2.0, 3.0, 4.0])
    bumped = np.array([0.0, 0.0, 1.0, 0.0, 0.0])
    # a quadratic fit leaves residuals whose robust scatter is 1.4826 MAD
    coeffs = np.polyfit(line, bumped, 2)
    r = bumped - np.polyval(coeffs, line)
    assert cmd._scatter(line, bumped) == pytest.approx(1.4826 * float(np.median(np.abs(r - np.median(r)))))


# --- quality cuts ------------------------------------------------------------------------------


CUTS = QualityCuts(ruwe_max=1.4, parallax_over_error_min=5.0, g_mag_max=18.0, astrometric_params_solved=(31, 95))


def _stars(**columns: list[Any]) -> pa.Table:
    n = len(next(iter(columns.values())))
    base: dict[str, list[Any]] = {
        "ruwe": [1.0] * n,
        "parallax_mas": [1.0] * n,
        "parallax_mas_err": [0.1] * n,
        "g_mag": [15.0] * n,
        "pm_ra_cosdec_masyr": [1.0] * n,
        "pm_dec_masyr": [1.0] * n,
        "quality_flags": [json.dumps({"astrometric_params_solved": 31})] * n,
    }
    base.update(columns)
    return pa.table(
        {k: pa.array(v, type=pa.string() if k == "quality_flags" else pa.float64()) for k, v in base.items()}
    )


def test_quality_cuts_round_trip_their_parameters() -> None:
    doc = {"ruwe_max": "1.4", "parallax_over_error_min": 5, "g_mag_max": 18, "astrometric_params_solved": ["31", 95]}
    cuts = QualityCuts.from_parameters(doc)
    assert cuts == CUTS
    assert cuts.to_document() == {
        "ruwe_max": 1.4,
        "parallax_over_error_min": 5.0,
        "g_mag_max": 18.0,
        "astrometric_params_solved": [31, 95],
    }


def test_each_cut_keeps_its_boundary_and_drops_a_missing_value() -> None:
    nan = float("nan")
    masks = CUTS.masks(_stars(ruwe=[1.4, 1.41, nan], g_mag=[18.0, 18.01, nan], parallax_mas=[0.5, 0.49, 1.0]))
    assert masks["ruwe"].tolist() == [True, False, False]
    assert masks["g_mag"].tolist() == [True, False, False]
    assert masks["parallax_over_error"].tolist() == [True, False, True]
    assert list(masks) == [
        "five_parameter_astrometry",
        "ruwe",
        "parallax_over_error",
        "g_mag",
        "astrometric_params_solved",
    ]


def test_astrometry_needs_both_motions_and_a_parallax() -> None:
    nan = float("nan")
    masks = CUTS.masks(
        _stars(
            pm_ra_cosdec_masyr=[nan, 1.0, 1.0, 1.0],
            pm_dec_masyr=[1.0, nan, 1.0, 1.0],
            parallax_mas=[1.0, 1.0, nan, 1.0],
        )
    )
    assert masks["five_parameter_astrometry"].tolist() == [False, False, False, True]
    assert masks["parallax_over_error"].tolist() == [True, True, False, True]


@pytest.mark.parametrize(
    ("flags", "kept"),
    [
        (json.dumps({"astrometric_params_solved": 95}), True),
        (json.dumps({"astrometric_params_solved": 3}), False),
        (json.dumps({"ruwe": 1.0}), False),
        ("", False),
        (None, False),
    ],
)
def test_the_solution_type_comes_from_the_quality_flags(flags: str | None, kept: bool) -> None:
    assert CUTS.masks(_stars(quality_flags=[flags]))["astrometric_params_solved"].tolist() == [kept]
    accepts_missing = QualityCuts(1.4, 5.0, 18.0, (-1,))
    assert accepts_missing.masks(_stars(quality_flags=[flags]))["astrometric_params_solved"].tolist() == [
        flags is None or flags == "" or "ruwe" in flags
    ]


def test_applying_the_cuts_reports_failures_and_removals_in_order() -> None:
    table = _stars(ruwe=[2.0, 2.0, 1.0, 1.0], g_mag=[19.0, 15.0, 19.0, 15.0])
    kept, report = CUTS.apply(table)
    assert kept.num_rows == 1 and kept.column("g_mag").to_pylist() == [15.0]
    assert report == {
        "cuts": CUTS.to_document(),
        "input_rows": 4,
        "kept_rows": 1,
        "failed_by_cut": {
            "five_parameter_astrometry": 0,
            "ruwe": 2,
            "parallax_over_error": 0,
            "g_mag": 2,
            "astrometric_params_solved": 0,
        },
        "removed_in_order": {
            "five_parameter_astrometry": 0,
            "ruwe": 2,
            "parallax_over_error": 0,
            "g_mag": 1,
            "astrometric_params_solved": 0,
        },
    }


def test_canonical_astrometry_names_the_columns_it_lacks() -> None:
    table = pa.table({"a": [1]})
    require_columns(table, ["a"])
    with pytest.raises(ValueError, match=r"^canonical astrometry lacks \['b', 'c'\]$"):
        require_columns(table, ["a", "b", "c"])


# --- the candidate score -----------------------------------------------------------------------


def _cand(cid: str, membership: float, stability: float, coherence: float | None, status: str) -> dict[str, Any]:
    return {
        "candidate_id": cid,
        "mean_membership": membership,
        "stability": stability,
        "cmd_coherence": coherence,
        "status": status,
    }


def test_the_candidate_score_weights_each_term_and_caps_coherence_at_four() -> None:
    assert ranking.WEIGHTS == {"membership": 0.35, "coherence": 0.25, "stability": 0.25, "novelty": 0.15}
    assert ranking.score(_cand("a", 0.8, 0.6, 2.0, "known")) == pytest.approx(0.35 * 0.8 + 0.25 * 0.5 + 0.25 * 0.6)
    assert ranking.score(_cand("a", 0.8, 0.6, 9.0, "potentially_new")) == pytest.approx(
        0.35 * 0.8 + 0.25 + 0.25 * 0.6 + 0.15
    )
    assert ranking.score(_cand("a", 1.0, 1.0, None, "known")) == pytest.approx(0.6)
    assert ranking.score(_cand("a", 1.0, 1.0, 4.0, "potentially_new")) == pytest.approx(1.0)


def test_ranking_rounds_scores_and_breaks_ties_by_id() -> None:
    ranked = ranking.rank(
        [_cand("b", 0.5, 0.5, None, "known"), _cand("a", 0.5, 0.5, None, "known"), _cand("c", 1.0, 1.0, 4.0, "known")]
    )
    assert [(c["candidate_id"], c["rank"], c["score"]) for c in ranked] == [
        ("c", 1, 0.85),
        ("a", 2, 0.3),
        ("b", 3, 0.3),
    ]
    assert ranking.rank([]) == []


def _result(subject: Subject, p: float | None, **details: object) -> TestResult:
    return TestResult(ranking.STRUCTURE_TEST, "1.0.0", subject, statistic=3.0, seed=1, p_value=p, details=details)


def _scores(policy: RankingPolicy, results: list[TestResult]) -> dict[str, float]:
    return {c.subject.key: c.score for c in ranking.StellarCandidateRanker().rank(results, policy)}


def test_a_group_scores_zero_without_a_significant_structure_test() -> None:
    policy = RankingPolicy("stellar", "1.0.0", threshold=0.0, alpha=0.05)
    group = Subject(structure_id="g", members=("a", "b"), membership=(1.0, 1.0))
    above_alpha = _scores(policy, [_result(group, 0.0500001, stability=1.0, cmd_coherence=4.0)])
    assert above_alpha == {group.key: 0.0}
    at_alpha = _scores(policy, [_result(group, 0.05, stability=1.0, cmd_coherence=4.0)])
    assert at_alpha[group.key] > 0.0, "p == alpha is significant (p <= alpha keeps the declared size)"
    untested = _scores(policy, [_result(group, None, stability=1.0)])
    assert untested == {group.key: 0.0}
    other = _scores(policy, [replace(_result(group, 0.001, stability=1.0), test_name="parallax-consistency")])
    assert other == {group.key: 0.0}


def test_a_significant_group_scores_the_weighted_mean_of_the_terms_assessed() -> None:
    policy = RankingPolicy("stellar", "1.0.0", threshold=0.0, alpha=0.05)
    group = Subject(structure_id="g", members=("a", "b"), membership=(1.0, 0.5))
    assert _scores(policy, [_result(group, 0.01, stability=0.4)]) == {
        group.key: pytest.approx((0.35 * 0.75 + 0.25 * 0.4) / 0.6)
    }
    assert _scores(policy, [_result(group, 0.01, cmd_coherence=2.0)]) == {
        group.key: pytest.approx((0.35 * 0.75 + 0.25 * 0.5) / 0.6)
    }
    empty = Subject(structure_id="h", members=("a", "b"), membership=())
    assert _scores(policy, [_result(empty, 0.01)]) == {empty.key: 0.0}


# --- the feature policy ------------------------------------------------------------------------


def test_the_tangent_plane_is_the_gnomonic_projection() -> None:
    xi, eta = tangent_plane(np.array([10.0, 11.0, 10.0]), np.array([20.0, 20.0, 21.0]), 10.0, 20.0)
    assert xi[0] == pytest.approx(0.0) and eta[0] == pytest.approx(0.0)
    a, d = np.radians(1.0), np.radians(20.0)
    cos_c = np.sin(d) ** 2 + np.cos(d) ** 2 * np.cos(a)
    assert xi[1] == pytest.approx(np.degrees(np.cos(d) * np.sin(a) / cos_c))
    assert eta[1] == pytest.approx(np.degrees((np.cos(d) * np.sin(d) - np.sin(d) * np.cos(d) * np.cos(a)) / cos_c))
    assert xi[2] == pytest.approx(0.0, abs=1e-12) and eta[2] == pytest.approx(np.degrees(np.tan(np.radians(1.0))))


def _astrometry(n: int = 9) -> pa.Table:
    rng = np.random.default_rng(2)
    return pa.table(
        {
            "ra_deg": 10.0 + rng.normal(0, 0.3, n),
            "dec_deg": 20.0 + rng.normal(0, 0.3, n),
            "pm_ra_cosdec_masyr": rng.normal(5, 2, n),
            "pm_dec_masyr": rng.normal(-3, 2, n),
            "parallax_mas": rng.normal(2, 0.5, n),
        }
    )


def test_fsp_v1_scales_each_pair_by_its_robust_radius_and_parallax_by_its_mad() -> None:
    table = _astrometry()
    fm = apply_policy("fsp-v1", table, 10.0, 20.0)
    assert (fm.policy_id, fm.axes) == ("fsp-v1", ("xi", "eta", "pm_ra_cosdec", "pm_dec", "parallax"))
    xi, eta = tangent_plane(np.asarray(table["ra_deg"]), np.asarray(table["dec_deg"]), 10.0, 20.0)
    half = np.sqrt(2 * np.log(2))
    pos = MAD_TO_SIGMA * float(np.median(np.hypot(xi, eta))) / half
    pmra, pmdec = np.asarray(table["pm_ra_cosdec_masyr"]), np.asarray(table["pm_dec_masyr"])
    pm = MAD_TO_SIGMA * float(np.median(np.hypot(pmra - np.median(pmra), pmdec - np.median(pmdec)))) / half
    plx = np.asarray(table["parallax_mas"])
    p = MAD_TO_SIGMA * float(np.median(np.abs(plx - np.median(plx))))
    assert fm.scales == pytest.approx((pos, pos, pm, pm, p))
    expected = np.column_stack(
        [
            xi / pos,
            eta / pos,
            (pmra - np.median(pmra)) / pm,
            (pmdec - np.median(pmdec)) / pm,
            (plx - np.median(plx)) / p,
        ]
    )
    assert fm.values == pytest.approx(expected)


def test_fsp_v1_refuses_non_finite_or_degenerate_axes() -> None:
    table = _astrometry()
    nan_plx = table.set_column(4, "parallax_mas", pa.array([float("nan")] + [1.0] * 8))
    with pytest.raises(
        PolicyError,
        match=r"^fsp-v1 needs finite positions, proper motions and parallaxes; apply the quality cuts first$",
    ):
        fsp_v1(nan_plx, 10.0, 20.0)
    flat_plx = table.set_column(4, "parallax_mas", pa.array([1.0] * 9))
    with pytest.raises(PolicyError, match=r"^a feature axis has no spread; the region is degenerate$"):
        fsp_v1(flat_plx, 10.0, 20.0)
    flat_pm = table.set_column(2, "pm_ra_cosdec_masyr", pa.array([1.0] * 9)).set_column(
        3, "pm_dec_masyr", pa.array([1.0] * 9)
    )
    with pytest.raises(PolicyError, match=r"^a feature pair has no spread; the region is degenerate$"):
        fsp_v1(flat_pm, 10.0, 20.0)
    at_centre = table.set_column(0, "ra_deg", pa.array([10.0] * 9)).set_column(1, "dec_deg", pa.array([20.0] * 9))
    with pytest.raises(PolicyError, match=r"^positions have no spread$"):
        fsp_v1(at_centre, 10.0, 20.0)


def test_a_feature_matrix_names_a_registered_policy_and_matches_its_axes() -> None:
    with pytest.raises(PolicyError, match=r"^unknown feature-space policy 'v9'; registered: \['fsp-v1'\]$"):
        apply_policy("v9", _astrometry(), 10.0, 20.0)
    with pytest.raises(PolicyError, match=r"^feature matrix from unregistered policy 'v9'$"):
        FeatureMatrix(np.zeros((2, 1)), "v9", ("a",), (1.0,))
    with pytest.raises(PolicyError, match=r"^feature matrix shape does not match its axes$"):
        FeatureMatrix(np.zeros((2, 2)), "fsp-v1", ("a",), (1.0,))
    with pytest.raises(PolicyError, match=r"^feature matrix shape does not match its axes$"):
        FeatureMatrix(np.zeros(2), "fsp-v1", ("a",), (1.0,))

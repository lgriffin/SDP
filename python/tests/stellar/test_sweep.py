"""T1/T8: the exploratory whole-sky sweep (STL-009): the tiling covers the sky once, a tile sees its rim
but keeps only what it owns, and the planted clusters come out as the predeclared run labels them."""

from __future__ import annotations

import math
from functools import cache
from typing import Any

import numpy as np
import pyarrow as pa
import pytest

from sdp.adapters.bootstrap import clusterers
from sdp_stellar import sweep
from sdp_stellar.benchmark import Benchmark, benchmark_from
from sdp_stellar.protocol import load_predeclaration
from sdp_stellar.synthetic import make_benchmark, make_field

pytestmark = pytest.mark.tier("T1")

TILE = 2.0


def targets() -> list[dict[str, Any]]:
    return list(load_predeclaration()["targets"])


@cache
def praesepe_field() -> pa.Table:
    return make_field(next(t for t in targets() if t["target_id"] == "praesepe"), 7)


def benchmarks() -> list[Benchmark]:
    return [
        benchmark_from(b, make_benchmark(b, targets(), label))
        for b, label in (("hr23", "hr23:v1.0"), ("cga20", "cga20:v1"))
    ]


def owner(ra: float, dec: float) -> sweep.Tile:
    band = int(sweep.band_of(np.array([dec]), TILE)[0])
    found = [
        t for t in (sweep.tile(band, i, TILE) for i in range(sweep.tiles_in_band(band, TILE))) if t.contains(ra, dec)
    ]
    assert len(found) == 1
    return found[0]


#: SYN-B, planted in the Praesepe field and in neither benchmark
SYN_B = (130.1 - 0.4 / math.cos(math.radians(19.97)), 19.97)


def tile_rows(t: sweep.Tile, table: pa.Table) -> pa.Table:
    ra, dec = sweep.ra_dec(table)
    return table.filter(pa.array(sweep.neighbourhood(t, 0.5, ra, dec)))


def settings(**changes: Any) -> dict[str, Any]:
    return {**sweep.load_settings(), **changes}


# --- tiling -------------------------------------------------------------------------------------


@pytest.mark.req("STL-009")
def test_every_point_of_the_sky_is_in_exactly_one_tile() -> None:
    rng = np.random.default_rng(1)
    ra = rng.uniform(0, 360, 400)
    dec = np.degrees(np.arcsin(rng.uniform(-1, 1, 400)))
    points = [*zip(ra.tolist(), dec.tolist(), strict=True), (0.0, 90.0), (359.999, -90.0), (0.0, 0.0), (180.0, 18.0)]
    all_tiles = sweep.tiles(TILE)
    for r, d in points:
        assert sum(t.contains(r, d) for t in all_tiles) == 1, (r, d)


def test_tiles_are_about_two_degrees_on_the_sky_and_named_by_band_and_index() -> None:
    assert sweep.bands(TILE) == 90
    assert sweep.tiles_in_band(45, TILE) == 180  # the band just north of the equator
    assert sweep.tiles_in_band(44, TILE) == 180
    assert sweep.tiles_in_band(89, TILE) == math.ceil(360 * math.cos(math.radians(88)) / 2)
    assert sweep.tiles_in_band(0, TILE) == sweep.tiles_in_band(89, TILE)
    t = sweep.tile(45, 3, TILE)
    assert (t.tile_id, t.ra_min, t.ra_max, t.dec_min, t.dec_max) == ("b045-t003", 6.0, 8.0, 0.0, 2.0)
    assert (t.ra_deg, t.dec_deg) == (7.0, 1.0)
    assert len(sweep.tiles(TILE)) == sum(sweep.tiles_in_band(b, TILE) for b in range(90))


def test_tile_settings_that_do_not_divide_the_sky_are_refused() -> None:
    with pytest.raises(ValueError, match=r"^tile_deg 7.0 does not divide 180 degrees of declination$"):
        sweep.bands(7.0)
    with pytest.raises(ValueError, match=r"^band 45 has tiles 0..179, not 180$"):
        sweep.tile(45, 180, TILE)
    with pytest.raises(ValueError):
        sweep.tile(45, -1, TILE)


def test_band_of_clips_the_poles_into_the_end_bands() -> None:
    assert sweep.band_of(np.array([-90.0, -88.0, -0.001, 0.0, 89.999, 90.0]), TILE).tolist() == [0, 1, 44, 45, 89, 89]


def test_a_tile_is_half_open_so_its_edges_belong_to_one_neighbour() -> None:
    t = sweep.tile(45, 3, TILE)
    assert t.contains(6.0, 0.0) and not t.contains(8.0, 0.0) and not t.contains(7.0, 2.0)
    assert t.contains(366.5, 1.0)  # RA is taken modulo 360
    assert sweep.tile(89, 0, TILE).contains(10.0, 90.0)
    assert not sweep.tile(44, 3, TILE).contains(7.0, 0.0)


@pytest.mark.req("STL-009")
def test_the_neighbourhood_holds_the_rim_and_wraps_through_ra_zero() -> None:
    t = sweep.tile(45, 0, TILE)  # RA 0..2, Dec 0..2
    ra = np.array([1.0, 359.6, 2.4, 1.0, 1.0, 359.0, 3.0])
    dec = np.array([1.0, 1.0, 1.0, -0.4, 2.6, 1.0, 1.0])
    assert sweep.neighbourhood(t, 0.5, ra, dec).tolist() == [True, True, True, True, False, False, False]


def test_near_a_pole_the_neighbourhood_takes_every_ra() -> None:
    top = sweep.tile(89, 0, TILE)
    assert sweep.neighbourhood(top, 0.5, np.array([0.0, 180.0, 90.0]), np.array([89.0, 89.5, 87.6])).tolist() == [
        True,
        True,
        True,
    ]
    wide = sweep.tile(88, 0, TILE)  # 86..88 and RA 0..27.7: the rim reaches 88.5, not the pole, and widens in RA
    assert sweep.neighbourhood(wide, 0.5, np.array([37.0, 200.0]), np.array([87.0, 87.0])).tolist() == [True, False]


def test_a_tile_seed_depends_only_on_the_tile_and_the_sweep_seed() -> None:
    assert sweep.tile_seed("b045-t003", 2026) == sweep.tile_seed("b045-t003", 2026)
    assert sweep.tile_seed("b045-t003", 2026) != sweep.tile_seed("b045-t004", 2026)
    assert sweep.tile_seed("b045-t003", 2026) != sweep.tile_seed("b045-t003", 2027)
    assert 0 <= sweep.tile_seed("b045-t003", 2026) < 2**32


def test_settings_share_the_predeclared_cuts_and_pass_marks() -> None:
    s = sweep.load_settings()
    declared = load_predeclaration()["parameters"]
    for key in (
        "quality_cuts",
        "feature_space_policy",
        "candidate_checks",
        "crossmatch_radius_deg",
        "pm_window_masyr",
        "benchmarks",
    ):
        assert s[key] == declared[key]
    assert (s["tile_deg"], s["margin_deg"], s["null_fields_per_tile"], s["seed"]) == (2.0, 0.5, 1, 2026)
    assert s["algorithms"] == ["sklearn-hdbscan"]


# --- one tile ------------------------------------------------------------------------------------


@cache
def syn_b_result() -> dict[str, Any]:
    t = owner(*SYN_B)
    return sweep.sweep_tile(tile_rows(t, praesepe_field()), t, settings(), clusterers(), benchmarks(), 2026)


@pytest.mark.req("STL-009")
@pytest.mark.tier("T8")
def test_a_planted_cluster_in_no_benchmark_comes_out_potentially_new_and_labelled_exploratory() -> None:
    result = syn_b_result()
    assert (result["schema"], result["label"], result["skipped"]) == ("sdp-stellar/sweep-tile@1", "EXPLORATORY", None)
    assert result["tile"]["tile_id"] == owner(*SYN_B).tile_id
    assert result["benchmarks"] == ["hr23", "cga20"]
    assert result["null_fields"] == 1 and result["null_fields_with_survivors"] == 0
    new = [c for c in result["candidates"] if c["status"] == "potentially_new"]
    assert len(new) == 1 and new[0]["label"] == "EXPLORATORY"
    assert new[0]["reasons"] == ["passed every check and matches no cluster in hr23 or cga20"]
    assert abs(new[0]["centre"]["pm_ra_cosdec_masyr"] - -36.0) < 0.5
    assert new[0]["candidate_id"].startswith(result["tile"]["tile_id"] + "-c")


@pytest.mark.tier("T8")
def test_the_field_itself_is_rejected_as_most_of_the_stars() -> None:
    field = [c for c in syn_b_result()["candidates"] if c["status"] == "rejected"]
    assert field and any(r.endswith("stars searched: the field itself, not a cluster") for r in field[0]["reasons"])


@pytest.mark.tier("T8")
def test_a_tile_keeps_only_candidates_centred_inside_it() -> None:
    t = owner(*SYN_B)
    neighbour = sweep.tile(t.band, t.index + 1, TILE)  # SYN-B sits in its rim
    result = sweep.sweep_tile(
        tile_rows(neighbour, praesepe_field()), neighbour, settings(), clusterers(), benchmarks(), 2026
    )
    assert all(neighbour.contains(c["centre"]["ra_deg"], c["centre"]["dec_deg"]) for c in result["candidates"])
    assert not any(c["status"] == "potentially_new" for c in result["candidates"])


@pytest.mark.tier("T8")
def test_without_two_benchmarks_nothing_is_potentially_new() -> None:
    t = owner(*SYN_B)
    rows = tile_rows(t, praesepe_field())
    one = sweep.sweep_tile(rows, t, settings(), clusterers(), benchmarks()[:1], 2026)
    broken = [benchmark_from("cga20", benchmarks()[1].table.drop_columns(["pm_dec_masyr"]))]
    unusable = sweep.sweep_tile(rows, t, settings(), clusterers(), benchmarks()[:1] + broken, 2026)
    for result in (one, unusable):
        assert result["benchmarks"] == ["hr23"]
        assert not [c for c in result["candidates"] if c["status"] == "potentially_new"]
        assert any(
            "cross-matched against fewer than two benchmarks (STL-005)" in c["reasons"] for c in result["candidates"]
        )


@pytest.mark.tier("T8")
def test_without_a_null_field_a_new_looking_candidate_is_only_inconclusive() -> None:
    t = owner(*SYN_B)
    result = sweep.sweep_tile(
        tile_rows(t, praesepe_field()), t, settings(null_fields_per_tile=0), clusterers(), benchmarks(), 2026
    )
    assert result["null_fields"] == 0
    flagged = [c for c in result["candidates"] if "no null field was run for this tile" in c["reasons"]]
    assert len(flagged) == 1 and flagged[0]["status"] == "inconclusive"


@pytest.mark.tier("T8")
def test_a_null_field_survivor_makes_new_looking_candidates_inconclusive() -> None:
    t = owner(*SYN_B)
    lax = settings(candidate_checks={**sweep.load_settings()["candidate_checks"], "max_field_fraction": 1.0})
    result = sweep.sweep_tile(tile_rows(t, praesepe_field()), t, lax, clusterers(), benchmarks(), 2026)
    assert result["null_fields_with_survivors"] == 1
    syn_b = [
        c for c in result["candidates"] if "passed every check and matches no cluster in hr23 or cga20" in c["reasons"]
    ]
    assert syn_b[0]["status"] == "inconclusive"
    assert syn_b[0]["reasons"][-1] == "1 of 1 null fields of this tile produced surviving candidates"


def test_a_tile_with_too_few_stars_is_skipped_with_the_reason() -> None:
    t = owner(*SYN_B)
    rows = tile_rows(t, praesepe_field()).slice(0, 15)
    result = sweep.sweep_tile(rows, t, settings(), clusterers(), benchmarks(), 2026)
    kept = result["cuts"]["kept_rows"]
    assert kept < 20 and result["skipped"] == f"{kept} stars pass the cuts, fewer than 20"
    assert result["candidates"] == [] and result["null_fields"] == 0


def test_a_degenerate_tile_is_skipped_not_crashed_on() -> None:
    t = owner(*SYN_B)
    rows = tile_rows(t, praesepe_field())
    same = rows.set_column(
        rows.schema.get_field_index("parallax_mas"), "parallax_mas", pa.array([5.0] * rows.num_rows, pa.float64())
    )
    result = sweep.sweep_tile(same, t, settings(), clusterers(), benchmarks(), 2026)
    assert result["skipped"] == "a feature axis has no spread; the region is degenerate"


# --- across tiles --------------------------------------------------------------------------------


def _result(statuses: list[str], nulls: int, survivors: int, kept: int, skipped: str | None = None) -> dict[str, Any]:
    return {
        "candidates": [
            {
                "candidate_id": f"x-c{i:03d}",
                "status": s,
                "mean_membership": 0.9,
                "cmd_coherence": 4.0,
                "stability": 1.0 - i / 10,
                "n_members": 30,
                "centre": {
                    "ra_deg": 1.0,
                    "dec_deg": 2.0,
                    "pm_ra_cosdec_masyr": 3.0,
                    "pm_dec_masyr": 4.0,
                    "parallax_mas": 5.0,
                },
                "crossmatch": [{"benchmark": "hr23", "matches": [{"name": "Known_1"}]}] if s == "known" else [],
                "reasons": ["r1", "r2"],
            }
            for i, s in enumerate(statuses)
        ],
        "null_fields": nulls,
        "null_fields_with_survivors": survivors,
        "cuts": {"kept_rows": kept},
        "skipped": skipped,
    }


@pytest.mark.req("STL-009")
def test_the_summary_counts_candidates_and_the_null_false_positive_rate_per_null_field() -> None:
    results = [
        _result(["potentially_new", "rejected"], 1, 0, 100),
        _result(["known", "rejected", "rejected"], 2, 1, 50),
        _result([], 0, 0, 7, skipped="too few"),
    ]
    assert sweep.summarise(results) == {
        "label": "EXPLORATORY",
        "tiles": 3,
        "tiles_skipped": 1,
        "stars_searched": 150,
        "candidates": {"known": 1, "potentially_new": 1, "rejected": 3},
        "null_fields": 3,
        "null_false_positive_rate": 1 / 3,
    }
    assert sweep.summarise([_result([], 0, 0, 5)])["null_false_positive_rate"] is None


def test_candidate_rows_put_potentially_new_first_then_score() -> None:
    rows = sweep.candidate_rows(
        [_result(["rejected", "known", "potentially_new", "inconclusive", "potentially_new"], 1, 0, 10)]
    )
    assert [(r["status"], r["candidate_id"]) for r in rows] == [
        ("potentially_new", "x-c002"),
        ("potentially_new", "x-c004"),
        ("inconclusive", "x-c003"),
        ("known", "x-c001"),
        ("rejected", "x-c000"),
    ]
    known = rows[3]
    assert known["matches"] == "Known_1" and known["reasons"] == "r1; r2"
    assert (known["ra_deg"], known["dec_deg"], known["parallax_mas"], known["members"]) == (1.0, 2.0, 5.0, 30)
    assert rows[0]["score"] > rows[1]["score"]

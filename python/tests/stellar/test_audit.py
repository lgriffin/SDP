"""T1/T8: the claim audit (STL-024): which catalogues list each object, which claims are audited, and
whether a claim survives the sweep's own tests on a field centred on it. The fields are synthetic
with planted clusters; they are not Gaia data."""

from __future__ import annotations

import math
from collections.abc import Sequence
from functools import cache
from typing import Any

import numpy as np
import pyarrow as pa
import pytest

from sdp.adapters.bootstrap import clusterers
from sdp_stellar import audit
from sdp_stellar.benchmark import Benchmark, with_metadata
from sdp_stellar.protocol import load_predeclaration
from sdp_stellar.synthetic import SCHEMA, _rows, make_field

pytestmark = pytest.mark.tier("T1")

#: SYN-B, planted in the Praesepe field at 185 pc
SYN_B = (130.1 - 0.4 / math.cos(math.radians(19.97)), 19.97, -36.0, -13.0, 1000.0 / 185.0)


def catalogue(benchmark_id: str, rows: Sequence[tuple[str, float, float, float, float, float | None]]) -> Benchmark:
    names = ["name", "ra_deg", "dec_deg", "pm_ra_cosdec_masyr", "pm_dec_masyr", "parallax_mas"]
    table = pa.table(
        {k: [r[i] for r in rows] for i, k in enumerate(names)},
        schema=pa.schema([("name", pa.string())] + [(k, pa.float64()) for k in names[1:]]),
    )
    table = with_metadata(table, f"{benchmark_id}:test", "CC0-1.0", "test catalogue")
    return Benchmark(benchmark_id, f"{benchmark_id}:test", "CC0-1.0", "test catalogue", table)


def doc(**changes: Any) -> dict[str, Any]:
    return {**audit.load_settings()["audit"], **changes}


def claim(
    name: str, ra: float, dec: float, pm: tuple[float, float], plx: float | None, by: tuple[str, ...] = ("x",)
) -> audit.Claim:
    return audit.Claim(by[0], name, ra, dec, pm[0], pm[1], plx, by)


# --- which catalogues list an object --------------------------------------------------------------


@pytest.mark.req("STL-024")
def test_an_object_is_listed_by_every_catalogue_with_a_cluster_at_its_place_and_motion() -> None:
    a = catalogue(
        "a", [("A1", 10.0, 5.0, 1.0, 2.0, 0.5), ("A2", 50.0, -20.0, 3.0, 3.0, 0.4), ("A3", 80, 0, 0, 0, None)]
    )
    b = catalogue("b", [("B1", 10.05, 5.0, 1.5, 2.0, 0.5), ("B2", 50.0, -20.0, 6.0, 3.0, 0.4)])
    c = catalogue("c", [("C1", 10.0, 5.02, 1.0, 1.6, 0.5), ("C9", 200.0, 40.0, 0.0, 0.0, 1.0)])
    by_id = {x.claim_id: x for x in audit.claims([a, b, c], doc())}
    assert by_id["a:A1"].claimed_by == ("a", "b", "c") and by_id["b:B1"].agreement == 3
    # the same place, 3 mas/yr apart in proper motion: two different objects
    assert by_id["a:A2"].claimed_by == ("a",) and by_id["b:B2"].claimed_by == ("b",)
    assert by_id["c:C9"].claimed_by == ("c",)
    assert by_id["a:A3"].parallax_mas is None and by_id["a:A3"].agreement == 1


@pytest.mark.req("STL-024")
def test_a_nearby_clusters_centres_may_differ_by_what_five_parsecs_subtend() -> None:
    near = 10.0  # mas: 100 pc, where 5 pc is 2.9 degrees
    a = catalogue("a", [("A", 60.0, 20.0, 5.0, -5.0, near), ("F", 60.0, -40.0, 5.0, -5.0, 0.2)])
    b = catalogue("b", [("B", 61.5, 20.0, 5.5, -5.0, near), ("G", 60.3, -40.0, 5.0, -5.0, 0.2)])
    by_id = {x.claim_id: x for x in audit.claims([a, b], doc())}
    assert by_id["a:A"].agreement == 2 and by_id["b:B"].agreement == 2
    # 0.23 degrees apart at 5 kpc is farther than both 0.1 degrees and 5 pc
    assert by_id["a:F"].agreement == 1 and by_id["b:G"].agreement == 1
    assert audit.match_radius_deg(near, doc()) == pytest.approx(math.degrees(5.0 / 100.0))
    assert audit.match_radius_deg(0.2, doc()) == 0.1 and audit.match_radius_deg(None, doc()) == 0.1


@pytest.mark.req("STL-024")
def test_two_catalogues_agree_whichever_side_the_match_is_seen_from() -> None:
    # one row has no parallax (0.1 deg), the other is 100 pc away (2.9 deg): 1 degree apart is a match both ways
    a = catalogue("a", [("A", 60.0, 20.0, 5.0, -5.0, None)])
    b = catalogue("b", [("B", 61.06, 20.0, 5.0, -5.0, 10.0)])
    by_id = {x.claim_id: x for x in audit.claims([a, b], doc())}
    assert by_id["a:A"].claimed_by == by_id["b:B"].claimed_by == ("a", "b")
    assert by_id["a:A"].partners == ("b:B",) and by_id["b:B"].partners == ("a:A",)


@pytest.mark.req("STL-024")
def test_a_row_without_a_position_or_proper_motion_is_kept_and_cannot_be_assessed() -> None:
    a = catalogue("a", [("A", 60.0, 20.0, math.nan, -5.0, 1.0), ("B", 61.0, 20.0, 5.0, -5.0, 1.0)])
    by_id = {x.claim_id: x for x in audit.claims([a, catalogue("b", [])], doc())}
    assert set(by_id) == {"a:A", "a:B"} and by_id["a:A"].claimed_by == ("a",)
    assert not audit.placed(by_id["a:A"]) and audit.scale_for(by_id["a:A"], audit.load_settings()) is None
    r = audit.audit_claim(pa.table({}), by_id["a:A"], "single", audit.load_settings(), {}, 1)
    assert r["verdict"] == "unassessable" and "no position or proper motion" in r["reasons"][0]


@pytest.mark.req("STL-024")
def test_two_rows_of_one_catalogue_matching_the_same_object_enter_the_control_once() -> None:
    a = catalogue("a", [("A1", 10.0, 0.0, 0.0, 0.0, 1.0), ("A2", 10.02, 0.0, 0.2, 0.0, 1.0)])
    b = catalogue("b", [("B", 10.01, 0.0, 0.1, 0.0, 1.0)])
    every = audit.claims([a, b], doc())
    assert all(c.agreement == 2 for c in every)
    for seed in range(5):
        control = [c for c, g in audit.chosen(every, doc(control_sample=10, control_from=["a", "b"]), seed)]
        assert len(control) == 1 and control[0].catalogue == "a"


@pytest.mark.req("STL-024")
def test_every_single_claim_and_a_seeded_control_sample_with_no_object_twice_are_audited() -> None:
    rows = [(f"K{i}", float(i), 0.0, 0.0, 0.0, 1.0) for i in range(40)]
    a = catalogue("a", [*rows, ("SOLO", 300.0, 60.0, 1.0, 1.0, 1.0)])
    b = catalogue("b", rows)
    every = audit.claims([a, b], doc())
    picked = audit.chosen(every, doc(control_sample=10, control_from=["a", "b"]), 2026)
    assert [(c.claim_id, g) for c, g in picked if g == "single"] == [("a:SOLO", "single")]
    control = [c for c, g in picked if g == "control"]
    assert len(control) == 10 and all(c.catalogue == "a" for c in control)  # each object once, as a names it
    again = audit.chosen(every, doc(control_sample=10, control_from=["a", "b"]), 2026)
    assert [c.claim_id for c, _ in again] == [c.claim_id for c, _ in picked]
    other = audit.chosen(every, doc(control_sample=10, control_from=["a", "b"]), 7)
    assert {c.claim_id for c, _ in other} != {c.claim_id for c, _ in picked}
    assert len(audit.chosen(every, doc(control_sample=500, control_from=["a", "b"]), 1)) == 41


# --- one claim ------------------------------------------------------------------------------------


def near_field(seed: int = 11, stars: int = 4000) -> pa.Table:
    """Field stars 110-400 pc away around Praesepe, so the near shell has a field to find SYN-B against."""
    rng = np.random.default_rng(seed)
    dec = rng.uniform(10, 30, stars)
    ra = (130 + rng.uniform(-11, 11, stars) / np.cos(np.radians(dec))) % 360
    pm = (rng.normal(0, 25, stars), rng.normal(0, 25, stars))
    plx, g = rng.uniform(2.5, 9, stars), rng.uniform(8, 18, stars)
    rows = _rows(rng, "synthetic:near", ra, dec, *pm, plx, g, rng.uniform(0.4, 2.6, stars))
    return pa.Table.from_pylist(rows, schema=SCHEMA)


@cache
def stars() -> pa.Table:
    praesepe = next(t for t in load_predeclaration()["targets"] if t["target_id"] == "praesepe")
    return pa.concat_tables([make_field(praesepe, 7), near_field()])


@cache
def audited(name: str) -> dict[str, Any]:
    ra, dec, pmra, pmdec, plx = SYN_B
    claims = {
        "SYN-B": claim("SYN-B", ra, dec, (pmra, pmdec), plx),
        # nothing at this place and motion
        "EMPTY": claim("EMPTY", 131.5, 21.0, (10.0, 10.0), 5.0),
        "NO-PLX": claim("NO-PLX", ra, dec, (pmra, pmdec), None),
    }
    return audit.audit_claim(stars(), claims[name], "single", audit.load_settings(), clusterers(), 2026)


@pytest.mark.req("STL-024")
@pytest.mark.tier("T8")
def test_a_real_cluster_one_catalogue_claims_survives_with_its_null_fields_run() -> None:
    r = audited("SYN-B")
    assert (r["schema"], r["label"], r["verdict"]) == ("sdp-stellar/audit-claim@1", "EXPLORATORY", "survives")
    assert r["scale"] == "near" and r["field_radius_deg"] == 9.0
    assert r["null_fields"] == audit.load_settings()["audit"]["null_fields_per_claim"] == 3
    assert r["match"]["significance_sigma"] > 3.0
    assert abs(r["match"]["centre"]["pm_ra_cosdec_masyr"] - -36.0) < 0.5
    assert r["reasons"][0].startswith("recovered at ")


@pytest.mark.req("STL-024")
@pytest.mark.tier("T8")
def test_a_claim_with_nothing_at_its_place_and_motion_is_not_recovered() -> None:
    r = audited("EMPTY")
    assert r["verdict"] == "not_recovered" and r["match"] is None and r["stars"] > 100


@pytest.mark.req("STL-024")
def test_a_claim_without_a_parallax_cannot_be_assessed() -> None:
    assert audit.audit_claim(
        pa.table({}), claim("E", 1.0, 2.0, (0.0, 0.0), 1.0), "single", audit.load_settings(), {}, 1
    )["reasons"] == ["no prepared stars around the claim"]
    r = audited("NO-PLX")
    assert r["verdict"] == "unassessable" and r["scale"] is None
    assert "no parallax" in r["reasons"][0]


@pytest.mark.req("STL-024")
def test_a_field_with_too_few_stars_cannot_be_assessed() -> None:
    ra, dec, pmra, pmdec, plx = SYN_B
    r = audit.audit_claim(
        stars().slice(0, 15), claim("S", ra, dec, (pmra, pmdec), plx), "single", audit.load_settings(), clusterers(), 1
    )
    assert r["verdict"] == "unassessable" and "fewer than" in r["reasons"][0]


def test_the_audit_refuses_settings_without_the_significance_test() -> None:
    ra, dec, pmra, pmdec, plx = SYN_B
    with pytest.raises(ValueError, match="significance test"):
        audit.audit_claim(
            stars(),
            claim("S", ra, dec, (pmra, pmdec), plx),
            "single",
            {**audit.load_settings(), "significance": None},
            {},
            1,
        )


# --- the verdict -----------------------------------------------------------------------------------


def candidate(status: str = "inconclusive", sigma: float | None = 8.0, coh: float | None = 2.0) -> dict[str, Any]:
    return {
        "candidate_id": "c",
        "status": status,
        "reasons": ["unstable across the grid"] if status == "rejected" else [],
        "n_members": 60,
        "significance_sigma": sigma,
        "cmd_coherence": coh,
    }


@pytest.mark.req("STL-024")
def test_a_claim_survives_only_above_every_false_candidate_of_its_field() -> None:
    assert audit.judge(None, [], 3)[0] == "not_recovered"
    assert audit.judge(candidate("rejected"), [], 3) == ("fails_checks", ["unstable across the grid"])
    assert audit.judge(candidate(sigma=None), [], 3)[0] == "untestable"
    assert audit.judge(candidate(coh=None), [], 3)[0] == "untestable"
    assert audit.judge(candidate(), [], 0)[0] == "untestable"
    assert audit.judge(candidate(), [4.0, 8.0], 3)[0] == "below_null"
    assert audit.judge(candidate(), [None], 3)[0] == "below_null"
    verdict, reasons = audit.judge(candidate(), [4.0, 7.9], 3)
    assert verdict == "survives" and "beats every null survivor (best 7.9 sigma)" in reasons[0]
    assert audit.judge(candidate(), [], 3)[1] == ["recovered at 8.0 sigma, passed every check, no null survivor"]


def test_the_best_match_is_one_that_passed_then_the_most_significant() -> None:
    passed, louder, failed = candidate(sigma=5.0), candidate(sigma=9.0), candidate("rejected", sigma=20.0)
    assert audit._best([failed, passed, louder]) is louder
    assert audit._best([failed]) is failed and audit._best([]) is None


# --- the summary -----------------------------------------------------------------------------------


def result(group: str, cat: str, verdict: str, i: int) -> dict[str, Any]:
    return {
        "group": group,
        "claim": {
            "claim_id": f"{cat}:{i}",
            "catalogue": cat,
            "claimed_by": [cat],
            "ra_deg": 1.0,
            "dec_deg": 2.0,
            "parallax_mas": 0.5,
        },
        "verdict": verdict,
        "scale": "far",
        "stars": 100,
        "match": None,
        "null_survivor_sigmas": [3.5, None],
        "reasons": ["r"],
    }


@pytest.mark.req("STL-024")
def test_the_summary_gives_survival_per_group_and_catalogue_of_the_claims_assessed() -> None:
    rows = (
        [result("single", "cwnu23", "survives", i) for i in range(3)]
        + [result("single", "cwnu23", "not_recovered", i) for i in range(3, 9)]
        + [result("single", "hr23", "below_null", 9), result("single", "hr23", "unassessable", 10)]
        + [result("control", "hr23", "survives", i) for i in range(11, 19)]
        + [result("control", "hr23", "fails_checks", i) for i in range(19, 21)]
    )
    s = audit.summarise(rows)
    assert s["label"] == "EXPLORATORY"
    single = s["groups"]["single"]
    assert single["cwnu23"]["survival"] == pytest.approx(3 / 9) and single["cwnu23"]["recovered"] == pytest.approx(
        3 / 9
    )
    assert single["hr23"]["assessed"] == 1 and single["hr23"]["verdicts"]["unassessable"] == 1
    assert single["all"]["survival"] == pytest.approx(3 / 10)
    assert s["groups"]["control"]["all"]["survival"] == pytest.approx(0.8)
    assert s["single_vs_control"] == pytest.approx(0.3 / 0.8)
    low, high = single["all"]["survival_95"]
    assert low < 0.3 < high
    table = audit.claim_rows(rows)
    assert table[0]["group"] == "single" and table[0]["verdict"] == "survives"
    assert table[-1]["group"] == "control" and table[0]["worst_null_sigma"] == 3.5


def test_the_wilson_interval_stays_inside_zero_and_one() -> None:
    assert audit.wilson(0, 0) is None
    none = audit.wilson(0, 10)
    assert none is not None
    low, high = none
    assert low == 0.0 and 0.2 < high < 0.35
    every = audit.wilson(10, 10)
    assert every is not None
    low, high = every
    assert high == pytest.approx(1.0) and 0.65 < low < 0.8

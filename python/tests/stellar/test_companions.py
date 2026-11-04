"""Hidden companions of cluster members: orbit to mass, Gaia's dark-partner rule, the field null and
RUWE-blind membership, on invented stars (STL-020 to STL-022). Not Gaia data."""

from __future__ import annotations

import copy
import math
from typing import Any

import numpy as np
import pytest

from sdp_stellar import companions as c

pytestmark = pytest.mark.tier("T1")
SETTINGS = c.load_settings()


def orbit_rows(
    m1: list[float],
    m2: list[float],
    parallax: list[float],
    period_days: list[float],
    dark: list[bool],
    source_id: list[int] | None = None,
    m2_lower: list[float] | None = None,
) -> dict[str, Any]:
    """Face-on circular orbits (A = G = a0, B = F = 0) of the photocentre of a dark partner of mass m2."""
    m1a, m2a, plx, p = (np.asarray(x, dtype=float) for x in (m1, m2, parallax, period_days))
    a_au = np.cbrt((m1a + m2a) * (p / c.DAYS_PER_YEAR) ** 2)
    a0 = a_au * m2a / (m1a + m2a) * plx
    n = len(m1a)
    return {
        "source_id": np.asarray(source_id if source_id is not None else range(1, n + 1), dtype=np.int64),
        "nss_solution_type": np.asarray(["Orbital"] * n, dtype=object),
        "combination_method": np.asarray(["Orbital+M1"] * n, dtype=object),
        "period": p,
        "period_error": p * 0.01,
        "a_thiele_innes": a0,
        "b_thiele_innes": np.zeros(n),
        "f_thiele_innes": np.zeros(n),
        "g_thiele_innes": a0.copy(),
        "parallax": plx,
        "parallax_error": plx * 0.01,
        "significance": np.full(n, 30.0),
        "phot_g_mean_mag": np.full(n, 13.0),
        "m1": m1a,
        "m1_lower": m1a * 0.95,
        "m1_upper": m1a * 1.05,
        "m2_lower": np.asarray(m2_lower if m2_lower is not None else m2a * 0.95, dtype=float),
        "fluxratio_upper": np.where(np.asarray(dark), 0.0, 0.5),
        "flag": np.asarray([""] * n, dtype=object),
    }


# --- orbits to masses -----------------------------------------------------------------------------


@pytest.mark.req("STL-020")
def test_the_thiele_innes_elements_give_back_the_orbits_size_whatever_its_orientation() -> None:
    a0, inc, node, peri = 2.5, np.radians(50.0), np.radians(30.0), np.radians(110.0)
    # the Thiele-Innes elements of an orbit of semi-major axis a0 (Binnendijk's definitions)
    a = a0 * (math.cos(peri) * math.cos(node) - math.sin(peri) * math.sin(node) * math.cos(inc))
    b = a0 * (math.cos(peri) * math.sin(node) + math.sin(peri) * math.cos(node) * math.cos(inc))
    f = a0 * (-math.sin(peri) * math.cos(node) - math.cos(peri) * math.sin(node) * math.cos(inc))
    g = a0 * (-math.sin(peri) * math.sin(node) + math.cos(peri) * math.cos(node) * math.cos(inc))
    assert c.photocentre_semimajor_mas(np.array([a]), np.array([b]), np.array([f]), np.array([g]))[0] == pytest.approx(
        a0
    )


@pytest.mark.req("STL-020")
def test_keplers_law_gives_back_the_partners_mass() -> None:
    o = orbit_rows([0.9, 1.2, 0.5], [12.0, 0.3, 1.0], [2.0, 8.0, 5.0], [186.0, 900.0, 40.0], [True, False, True])
    masses = c.companion_masses(o, SETTINGS)
    assert masses["m2_msun"] == pytest.approx([12.0, 0.3, 1.0], rel=1e-9)
    f = masses["mass_function_msun"]
    assert f == pytest.approx(masses["m2_msun"] ** 3 / (o["m1"] + masses["m2_msun"]) ** 2, rel=1e-9)


@pytest.mark.req("STL-020")
def test_the_mass_solver_is_exact_from_tiny_to_huge_mass_functions_and_refuses_bad_input() -> None:
    m1 = np.array([0.1, 1.0, 1.0, 3.0, 1.0, 1.0, np.nan])
    f = np.array([1e-9, 1e-3, 1.0, 50.0, 1e4, -1.0, 1.0])
    m2 = c.companion_mass(f, m1)
    good = np.isfinite(m2)
    assert good.tolist() == [True, True, True, True, True, False, False]
    assert m2[good] ** 3 / (m1[good] + m2[good]) ** 2 == pytest.approx(f[good], rel=1e-9)


@pytest.mark.req("STL-020")
def test_only_a_dark_partner_above_the_chandrasekhar_mass_is_a_neutron_star_or_black_hole() -> None:
    o = orbit_rows(
        [1.0, 1.0, 1.0, 1.0],
        [3.0, 1.0, 3.0, 0.5],
        [5.0, 5.0, 5.0, 5.0],
        [500.0] * 4,
        [True, True, False, True],
        m2_lower=[2.5, 1.2, 2.5, float("nan")],
    )
    o["flag"][3] = "AMRFClassIII"
    _, verdict = c.judge(o, SETTINGS)
    assert verdict.tolist() == [c.COMPACT, c.DARK, c.ORDINARY, c.NO_MASS]


@pytest.mark.req("STL-020")
def test_an_orbit_of_another_kind_or_too_weak_is_not_used() -> None:
    o = orbit_rows([1.0] * 4, [3.0] * 4, [5.0] * 4, [500.0] * 4, [True] * 4)
    o["nss_solution_type"][0] = "OrbitalAlternative"
    o["combination_method"][1] = "SB1+M1"
    o["significance"][2] = 2.0
    _, verdict = c.judge(o, SETTINGS)
    assert verdict.tolist() == [c.UNUSABLE, c.UNUSABLE, c.UNUSABLE, c.COMPACT]


@pytest.mark.req("STL-020")
def test_a_cluster_distance_moves_gaias_lower_bound_and_nearer_means_lighter() -> None:
    o = orbit_rows([1.0], [3.0], [5.0], [500.0], [True], m2_lower=[2.8])
    same = c.companion_masses(o, SETTINGS, np.array([5.0]), np.array([0.0]))
    assert same["m2_lower_msun"][0] == pytest.approx(2.8, rel=1e-9)
    nearer = c.companion_masses(o, SETTINGS, np.array([5.5]), np.array([0.0]))
    with_error = c.companion_masses(o, SETTINGS, np.array([5.0]), np.array([0.25]))
    assert nearer["m2_lower_msun"][0] < 2.8
    assert with_error["m2_lower_msun"][0] == pytest.approx(nearer["m2_lower_msun"][0], rel=1e-9)


# --- the field ------------------------------------------------------------------------------------


def field_stars(n: int, rng: np.random.Generator) -> dict[str, np.ndarray]:
    return {
        "source_id": np.arange(10_000_000, 10_000_000 + n, dtype=np.int64),
        "ra": rng.uniform(40.0, 80.0, n),
        "dec": rng.uniform(0.0, 40.0, n),
        "parallax": rng.uniform(1.0, 10.0, n),
        "parallax_error": np.full(n, 0.05),
        "pmra": rng.normal(0.0, 30.0, n),
        "pmra_error": np.full(n, 0.05),
        "pmdec": rng.normal(0.0, 30.0, n),
        "pmdec_error": np.full(n, 0.05),
        "phot_g_mean_mag": rng.uniform(10.0, 18.0, n),
        "ruwe": 10 ** rng.normal(0.0, 0.03, n),
    }


@pytest.mark.req("STL-021")
def test_the_wobble_baseline_is_the_fields_own_median_and_spread_at_each_brightness() -> None:
    rng = np.random.default_rng(3)
    stars = field_stars(50_000, rng)
    stars["ruwe"] = np.where(stars["phot_g_mean_mag"] > 14.0, stars["ruwe"] * 10**0.1, stars["ruwe"])
    field = c.FieldCounts(SETTINGS)
    field.add(stars["phot_g_mean_mag"], stars["parallax"], stars["ruwe"])
    med, spread = field.baseline()
    g_edges = np.asarray(SETTINGS["wobble"]["g_bins"])
    bright = (g_edges[:-1] >= 10.0) & (g_edges[1:] <= 14.0)
    faint = (g_edges[:-1] >= 14.0) & (g_edges[1:] <= 18.0)
    assert med[bright] == pytest.approx(0.0, abs=0.006)
    assert med[faint] == pytest.approx(0.1, abs=0.006)
    assert spread[bright | faint] == pytest.approx(0.03, abs=0.006)
    z = field.z(np.array([12.0, 16.0]), np.array([10**0.15, 10**0.25]))
    assert z == pytest.approx([5.0, 5.0], abs=0.6)


@pytest.mark.req("STL-021")
def test_the_field_share_of_wobblers_is_counted_from_the_same_histogram() -> None:
    rng = np.random.default_rng(5)
    stars = field_stars(40_000, rng)
    wobbly = rng.random(40_000) < 0.05
    stars["ruwe"] = np.where(wobbly, 3.0, stars["ruwe"])
    field = c.FieldCounts(SETTINGS)
    field.add(stars["phot_g_mean_mag"], stars["parallax"], stars["ruwe"])
    share, total = field.wobbler_share(), field.stars()
    assert (share * total).sum() / total.sum() == pytest.approx(0.05 + 0.00135, abs=0.004)
    assert share[total > 500] == pytest.approx(0.05, abs=0.035)


@pytest.mark.req("STL-021")
def test_the_poisson_tail_says_how_often_the_field_gives_as_many() -> None:
    assert c.poisson_tail(0, 3.0) == 1.0
    assert c.poisson_tail(1, 0.0) == 0.0
    assert c.poisson_tail(5, 1.0) == pytest.approx(1 - sum(math.exp(-1) / math.factorial(k) for k in range(5)))


# --- clusters -------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def sky() -> dict[str, Any]:
    """A field, one cluster of 220 members (170 of them low-RUWE seeds as the sweep would keep, 50 not,
    12 of those wobbling) and one dark 3.2 solar-mass partner of a non-seed member."""
    rng = np.random.default_rng(11)
    field = field_stars(60_000, rng)
    n = 220
    cluster = {
        "source_id": np.arange(1, n + 1, dtype=np.int64),
        "ra": 60.0 + rng.normal(0.0, 0.4, n),
        "dec": 20.0 + rng.normal(0.0, 0.4, n),
        "parallax": rng.normal(5.0, 0.03, n),
        "parallax_error": np.full(n, 0.03),
        "pmra": rng.normal(10.0, 0.3, n),
        "pmra_error": np.full(n, 0.03),
        "pmdec": rng.normal(-5.0, 0.3, n),
        "pmdec_error": np.full(n, 0.03),
        "phot_g_mean_mag": rng.uniform(10.0, 18.0, n),
        "ruwe": 10 ** rng.normal(0.0, 0.03, n),
    }
    cluster["ruwe"][170:182] = 3.0
    stars = {k: np.concatenate([field[k], cluster[k]]) for k in field}
    counts = c.FieldCounts(SETTINGS)
    counts.add(stars["phot_g_mean_mag"], stars["parallax"], stars["ruwe"])
    orbits = orbit_rows(
        [1.0, 1.0, 0.8],
        [3.2, 0.3, 2.0],
        [5.0, float(field["parallax"][7]), float(field["parallax"][9])],
        [600.0, 300.0, 400.0],
        [True, False, True],
        source_id=[175, int(field["source_id"][7]), int(field["source_id"][9])],
    )
    clusters = [c.Cluster("near-b057-t010-c0", "potentially_new", "", frozenset(range(1, 171)))]
    return {"stars": stars, "orbits": orbits, "field": counts, "clusters": clusters}


@pytest.mark.req("STL-022")
def test_members_are_found_again_blind_to_ruwe(sky: dict[str, Any]) -> None:
    stars = sky["stars"]
    seeds = np.flatnonzero(np.isin(stars["source_id"], list(sky["clusters"][0].seed_ids)))
    fp = c.footprint(stars, seeds, SETTINGS)
    assert fp is not None and fp.seeds_found == 170
    found = set(stars["source_id"][c.members(stars, fp, np.arange(len(stars["source_id"])), SETTINGS)].tolist())
    assert set(range(171, 183)) <= found  # every wobbler the sweep's RUWE cut left out
    assert len(found - set(range(1, 221))) <= 2  # field interlopers are rare


@pytest.mark.req("STL-022")
def test_the_second_scan_keeps_only_stars_inside_a_footprint(sky: dict[str, Any]) -> None:
    stars = sky["stars"]
    seeds = np.flatnonzero(np.isin(stars["source_id"], list(sky["clusters"][0].seed_ids)))
    fp = c.footprint(stars, seeds, SETTINGS)
    assert fp is not None
    near = c.near_any([fp], stars["ra"], stars["dec"])
    assert near[np.isin(stars["source_id"], np.arange(1, 221))].mean() > 0.97
    assert near.mean() < 0.02
    assert not c.near_any([], stars["ra"], stars["dec"]).any()


@pytest.mark.req("STL-022")
def test_a_dark_partner_of_a_member_is_found_and_the_cluster_is_judged_against_the_field(sky: dict[str, Any]) -> None:
    result = c.screen(sky["clusters"], sky["stars"], sky["orbits"], sky["field"], SETTINGS)
    (row,) = result["candidates"]
    assert row["source_id"] == 175 and not row["seed_member"]
    assert row["verdict"] == c.COMPACT
    assert row["m2_dark_msun"] == pytest.approx(3.2, rel=0.01)
    (cluster,) = result["clusters"]
    assert cluster["wobblers"] >= 12
    assert cluster["wobblers_field_p"] < 1e-6
    assert cluster["flagged_orbits"] == 1 and cluster["flagged_field_p"] < 0.05
    assert result["field_orbit_verdicts"][c.COMPACT] == 2  # the member and the field's own dark partner


@pytest.mark.req("STL-022")
def test_a_cluster_with_too_few_seeds_in_the_scan_is_reported_not_screened(sky: dict[str, Any]) -> None:
    lost = [c.Cluster("gone", "known", "Fixture", frozenset({999_999_999, 888_888_888}))]
    (cluster,) = c.screen(lost, sky["stars"], sky["orbits"], sky["field"], SETTINGS)["clusters"]
    assert cluster["members"] == 0 and "fewer than 3" in cluster["note"]


@pytest.mark.req("STL-022")
def test_clusters_come_from_sweep_tiles_once_each_or_from_a_member_list() -> None:
    cand = {
        "candidate_id": "near-b001-t001-c0",
        "status": "known",
        "n_members": 3,
        "mean_parallax_mas": 5.0,
        "member_keys": ["gaia:DR3:11", "gaia:DR3:12", "gaia:DR3:13"],
        "crossmatch": [{"matches": [{"name": "Melotte_22"}]}],
    }
    copy_ = copy.deepcopy(cand) | {"candidate_id": "mid-b001-t001-c0", "mean_parallax_mas": 3.9}
    tiles = [{"tile": {"scale": "near"}, "candidates": [cand]}, {"tile": {"scale": "mid"}, "candidates": [copy_]}]
    (one,) = c.clusters_from_tiles(tiles)
    assert one.seed_ids == frozenset({11, 12, 13}) and one.matches == "Melotte_22"
    assert c.clusters_from_tiles(tiles, statuses=["potentially_new"]) == []
    listed = c.clusters_from_rows([{"cluster": "A", "source_id": "5"}, {"cluster": "A", "source_id": "6"}])
    assert listed[0].seed_ids == frozenset({5, 6})


@pytest.mark.req("STL-020")
def test_the_settings_are_hashed_for_every_report() -> None:
    assert len(c.settings_sha256()) == 64
    assert SETTINGS["orbits"]["combination_methods"] == ["Orbital+M1"]

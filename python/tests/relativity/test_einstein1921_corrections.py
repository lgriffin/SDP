"""UC-03b: exploratory corrections to the Einstein 1921 rerun (REL-017, REL-018)."""

from __future__ import annotations

import copy
import math
import re
from typing import Any

import numpy as np
import pytest

from sdp_relativity import einstein1921 as e
from sdp_relativity import einstein1921_corrections as c

pytestmark = pytest.mark.tier("T1")
PUBLISHED_PC = 7420.0


def planted_cluster(mass_to_light: float, seed: int, parallax_bias: float = 0.0) -> dict[str, np.ndarray]:
    cols = e.synthetic_cluster(
        mass=3e5,
        a_pc=3.0,
        distance_pc=PUBLISHED_PC,
        n_cluster=12000,
        n_field=10000,
        mass_to_light=mass_to_light,
        seed=seed,
    )
    cols["parallax"] = cols["parallax"] + parallax_bias
    return cols


def small_choices(mass_to_light_v: float) -> dict[str, Any]:
    """The committed choices on a two-scenario grid, with the published light set to the planted cluster's."""
    choices = copy.deepcopy(c.load_choices())
    choices["scenarios"] = {
        "r_in_arcmin": [3.0],
        "g_max": [19.0],
        "error_scale": [1.0],
        "distance": ["published", "alternative"],
    }
    choices["bootstrap"]["resamples"] = 3
    choices["published"]["light"] = {
        "mass_solar": 3e5,
        "mass_to_light_v": mass_to_light_v,
        "at_distance_pc": PUBLISHED_PC,
    }
    return choices


@pytest.fixture(scope="module")
def biased() -> dict[str, np.ndarray]:
    # Gaia's parallax 0.05 mas too large: 1/parallax would put the cluster at about 5.4 kpc
    return planted_cluster(2.0, seed=17, parallax_bias=0.05)


# --- REL-017: the published distance and light ------------------------------------------------


@pytest.mark.req("REL-017")
def test_the_distances_are_the_published_ones_from_the_choices_file() -> None:
    assert c.distance_pc("published") == 7420.0
    assert c.distance_pc("published_minus_1sigma") == 7340.0
    assert c.distance_pc("published_plus_1sigma") == 7500.0
    assert c.distance_pc("alternative") == 7100.0
    with pytest.raises(ValueError, match="unknown distance 'parallax'"):
        c.distance_pc("parallax")
    sources = c.load_choices()["published"]
    assert "Baumgardt & Vasiliev 2021" in sources["distance"]["source"]
    assert sources["distance"]["url"].startswith("https://people.smp.uq.edu.au/HolgerBaumgardt/")
    assert "Harris 1996" in sources["distance_alternative"]["source"]


@pytest.mark.req("REL-017")
def test_the_published_light_is_mass_over_mass_to_light_scaled_as_distance_squared() -> None:
    assert c.published_light_v(7420.0) == pytest.approx(4.84e5 / 2.05)
    assert c.published_light_v(2 * 7420.0) == pytest.approx(4 * 4.84e5 / 2.05)


@pytest.mark.req("REL-017")
def test_a_biased_parallax_no_longer_sets_the_mass(biased: dict[str, np.ndarray]) -> None:
    stars, _ = e.select(biased)
    centre = e.find_centre(stars)
    corr = c.Correction(3.0, 19.0, 1.0, "published")
    f = e.fit(stars, centre, corr.fit_scenario(0.6))
    uc03 = e.physical(stars, f, e.Scenario(3.0, 0.6, 1.0, 0.0, 0.0))
    assert uc03.distance_pc < 0.8 * PUBLISHED_PC  # what UC-03 would have used
    fixed = c.corrected(stars, f, corr, 0.6, small_choices(2.0))
    assert fixed.distance_pc == PUBLISHED_PC
    assert fixed.a_pc == pytest.approx(3.0, rel=0.1)
    assert fixed.mass_solar == pytest.approx(3e5, rel=0.15)
    assert fixed.mass_to_light_v == pytest.approx(2.0, rel=0.15)
    assert fixed.mass_to_light_g == pytest.approx(2.0, rel=0.15)
    assert fixed.v_rms_kms == pytest.approx(math.sqrt(e.mean_square_speed(fixed.mass_solar, fixed.a_pc)))


# --- REL-018: the exploratory reading ----------------------------------------------------------


@pytest.mark.req("REL-018")
def test_the_grid_has_108_corrections_and_contains_the_baseline() -> None:
    grid = c.corrections()
    assert len(grid) == 3 * 3 * 3 * 4
    assert c.baseline() in grid
    assert c.baseline() == c.Correction(3.0, 19.0, 1.0, "published")


@pytest.mark.req("REL-018")
def test_the_reading_needs_the_band_and_every_correction_on_one_side() -> None:
    low = {"p16": 2.0, "p50": 3.0, "p84": 4.0}
    high = {"p16": 12.0, "p50": 13.0, "p84": 14.0}
    assert c.reading(low, {"a": 3.0, "b": 9.9}, 10.0, True)["status"] == "consistent_with_H1_after_corrections"
    assert c.reading(high, {"a": 10.0, "b": 30.0}, 10.0, True)["status"] == "consistent_with_H0_after_corrections"
    assert c.reading(low, {"a": 3.0, "b": 10.0}, 10.0, True)["status"] == "mixed"
    assert c.reading(low, {"a": 3.0, "b": None}, 10.0, True)["status"] == "mixed"
    assert c.reading(low, {"a": 3.0}, 10.0, False)["status"] == "mixed"
    assert c.reading(None, {"a": 3.0}, 10.0, True)["status"] == "mixed"
    assert c.reading(low, {"a": 3.0}, 10.0, True)["exploratory"] is True


@pytest.mark.req("REL-018")
@pytest.mark.parametrize(
    ("mass_to_light", "status"),
    [(2.0, "consistent_with_H1_after_corrections"), (30.0, "consistent_with_H0_after_corrections")],
)
def test_the_reading_follows_the_planted_ratio_whatever_the_parallax(mass_to_light: float, status: str) -> None:
    # the planted G light is irrelevant here: the published light carries the ratio
    cols = planted_cluster(2.0, seed=23, parallax_bias=0.05)
    choices = small_choices(mass_to_light)
    report = c.run(cols, choices=choices)
    assert report["reading"]["status"] == status
    assert report["baseline"]["bootstrap"]["succeeded"] == 3
    quick = c.run(cols, resamples=2, choices=choices)  # fewer refits than declared: no reading
    assert quick["reading"]["status"] == "mixed"
    assert quick["baseline"]["corrected"] == report["baseline"]["corrected"]
    assert report["choices_match_committed"] is False
    assert report["baseline"]["corrected"]["mass_to_light_v"] == pytest.approx(mass_to_light, rel=0.15)


@pytest.mark.req("REL-018")
def test_a_run_records_its_choices_the_crowding_table_and_the_comparisons() -> None:
    cols = planted_cluster(2.0, seed=29)
    choices = small_choices(2.0)
    choices["scenarios"]["r_in_arcmin"] = [3.0, 4.5]
    report = c.run(cols, resamples=0, choices=choices)
    assert report["protocol_id"] == "uc03b-einstein-1921-corrections"
    assert report["parent_protocol"] == "uc03-einstein-1921"
    assert report["status_note"].startswith("exploratory")
    assert report["committed_choices_sha256"] == c.choices_sha256()
    assert report["reading"]["status"] == "mixed"  # no bootstrap, no reading
    assert len(report["corrections"]) == 4
    assert [(row["r_in_arcmin"], row["g_max"]) for row in report["crowding"]] == [(3.0, 19.0), (4.5, 19.0)]
    assert report["comparison"]["mass_solar"]["baumgardt_mass_solar"] == 4.84e5
    assert report["comparison"]["sigma0_kms"]["harris_sigma_v_kms"] == 7.1
    assert report["baseline"]["fit"]["degenerate"] is False
    assert all(row["degenerate"] is False for row in report["crowding"])


@pytest.mark.req("REL-018")
def test_a_magnitude_limit_fainter_than_uc03s_selection_is_refused() -> None:
    choices = small_choices(2.0)
    choices["scenarios"]["g_max"] = [20.0]
    with pytest.raises(ValueError, match=re.escape("G < 20.0 is fainter than UC-03's selection limit 19.0")):
        c.run(planted_cluster(2.0, seed=31), resamples=0, choices=choices)


@pytest.mark.req("REL-018")
def test_a_scale_far_inside_the_core_cut_is_degenerate() -> None:
    # the first UC-03b run: a slid to 0.06 pc (0.028 arcmin at 7.42 kpc) behind a 6 arcmin cut
    assert c.degenerate(0.028 / 60, 6.0)
    assert c.degenerate(0.199 * 6.0 / 60, 6.0)
    assert not c.degenerate(0.2 * 6.0 / 60, 6.0)
    assert not c.degenerate(2.57 / 60, 3.0)  # the corrected baseline
    assert c.load_choices()["degenerate_a_over_r_in"] == 0.2


@pytest.mark.req("REL-018")
def test_degenerate_corrections_are_listed_and_left_out_of_the_reading() -> None:
    low = {"p16": 2.0, "p50": 3.0, "p84": 4.0}
    r = c.reading(low, {"a": 3.0, "flat": 30.0}, 10.0, True, degenerate_scenarios=["flat"])
    assert r["status"] == "consistent_with_H1_after_corrections"
    assert r["scenarios_degenerate"] == ["flat"]
    assert r["scenarios_at_or_above"] == []
    assert c.reading(low, {"a": 3.0}, 10.0, True, baseline_degenerate=True)["status"] == "mixed"
    assert c.reading(low, {"flat": 3.0}, 10.0, True, degenerate_scenarios=["flat"])["status"] == "mixed"

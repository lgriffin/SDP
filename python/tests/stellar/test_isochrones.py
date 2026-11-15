"""MIST isochrones: reading the published tables, fitting a cluster, and masses on and off a cluster
(STL-033 to STL-036), on invented isochrones in MIST's layout (tests/stellar/mist_toy.py). Not MIST
models and not Gaia data."""

from __future__ import annotations

import copy
import math
from typing import Any

import numpy as np
import pytest
from tests.stellar.mist_toy import cluster, toy_cmd_text, toy_isochrone, turnoff

from sdp_stellar import companions as comp
from sdp_stellar import isochrones as iso

pytestmark = pytest.mark.tier("T1")
AGES = tuple(round(7.5 + 0.05 * i, 2) for i in range(46))  # 7.5 .. 9.75
FEHS = (-0.25, 0.0)


ORIGINAL = iso.load_settings


def settings(**fit: Any) -> dict[str, Any]:
    s = copy.deepcopy(ORIGINAL())
    s["fit"].update({"feh_min": -0.25, "av_max": 1.0, **fit})
    return s


def grid(fehs: tuple[float, ...] = FEHS, ages: tuple[float, ...] = AGES) -> iso.Grid:
    s = iso.load_settings()
    return iso.grid_from_files([iso.parse_cmd(toy_cmd_text(f, ages)) for f in fehs], s)


# --- reading MIST files (STL-033) -------------------------------------------------------------------


@pytest.mark.req("STL-033")
def test_a_mist_file_is_read_with_its_header_values_and_every_table() -> None:
    f = iso.parse_cmd(toy_cmd_text(0.25, (8.0, 9.0)))
    assert f.meta["mist_version"] == "1.2" and f.meta["mesa_revision"] == "7503"
    assert f.meta["photometric_system"].startswith("UBV(RI)c")
    assert f.meta["photometric_system"] == "UBV(RI)c + 2MASS + Kepler + Hipparcos + Gaia (DR2/MAW)"
    assert (f.meta["feh"], f.meta["afe"], f.meta["vvcrit"], f.meta["av"]) == (0.25, 0.0, 0.4, 0.0)
    assert f.meta["zinit"] == pytest.approx(0.0142857)
    assert len(f.blocks) == 2
    first = f.blocks[0]
    assert set(first) >= {"EEP", "Gaia_G_DR2Rev", "Gaia_BP_DR2Rev", "phase"}
    np.testing.assert_allclose(first["Gaia_G_DR2Rev"], toy_isochrone(0.25, 8.0)["Gaia_G_DR2Rev"], rtol=1e-9)
    assert f.blocks[1]["log10_isochrone_age_yr"][0] == 9.0
    # the other header layout: the system on the line after its label, Av and Rv labels over their values
    other = (
        toy_cmd_text(0.25, (8.0,))
        .replace("# photometric system   = UBV", "# photometric system\n# ----\n# UBV")
        .replace("# Av = 0.0000", "#     Av       Rv\n#  0.0300   3.1000")
    )
    g = iso.parse_cmd(other)
    assert g.meta["photometric_system"].startswith("UBV(RI)c") and (g.meta["av"], g.meta["rv"]) == (0.03, 3.1)


@pytest.mark.req("STL-033")
def test_a_file_that_breaks_the_layout_is_refused_with_the_line() -> None:
    good = toy_cmd_text(0.0, (8.0,))
    lines = good.splitlines()
    head = next(i for i, ln in enumerate(lines) if ln.startswith("# number of EEPs"))
    short = "\n".join(lines[: head + 3] + lines[head + 10 :])
    with pytest.raises(iso.MistFormatError, match="fewer than"):
        iso.parse_cmd(short)
    wrong = good.replace("# number of EEPs, cols =    200    14", "# number of EEPs, cols =    200    15")
    with pytest.raises(iso.MistFormatError, match="15"):
        iso.parse_cmd(wrong)
    with pytest.raises(iso.MistFormatError, match="no isochrone tables"):
        iso.parse_cmd("\n".join(lines[:head]))
    no_av = "\n".join(ln for ln in lines if not ln.startswith("# Av"))
    with pytest.raises(iso.MistFormatError, match="'av'"):
        iso.parse_cmd(no_av)
    stray = good.replace("# number of isochrones", "1 2 3\n# number of isochrones")
    with pytest.raises(iso.MistFormatError, match="outside any table"):
        iso.parse_cmd(stray)
    bad_value = good.replace("#  0.2703  1.42857E-02", "#  0.2703  x.4E-02")
    with pytest.raises(iso.MistFormatError, match="not a number"):
        iso.parse_cmd(bad_value)


@pytest.mark.req("STL-033")
def test_the_grid_keeps_the_settings_columns_in_the_age_range_and_refuses_reddened_or_mixed_files() -> None:
    s = copy.deepcopy(iso.load_settings())
    s["grid"].update({"log_age_min": 8.0, "log_age_max": 8.1})
    g = iso.grid_from_files([iso.parse_cmd(toy_cmd_text(f, (7.95, 8.0, 8.05, 8.1, 8.15))) for f in (0.0, -0.5)], s)
    assert g.fehs() == [-0.5, 0.0] and g.ages(0.0) == [8.0, 8.05, 8.1]
    one = g.isochrone(-0.5, 8.05)
    assert np.all(np.diff(one["eep"]) > 0)
    np.testing.assert_allclose(one["g"], toy_isochrone(-0.5, 8.05)["Gaia_G_DR2Rev"])
    assert g.meta["mist_version"] == "1.2" and g.meta["vvcrit"] == 0.4
    with pytest.raises(KeyError):
        g.isochrone(0.0, 9.0)
    with pytest.raises(iso.MistFormatError, match="A_V"):
        iso.grid_from_files([iso.parse_cmd(toy_cmd_text(0.0, (8.0,), av=0.5))], s)
    with pytest.raises(iso.MistFormatError, match="two files"):
        iso.grid_from_files([iso.parse_cmd(toy_cmd_text(0.0, (8.0,)))] * 2, s)
    newer = iso.parse_cmd(toy_cmd_text(-0.5, (8.0,)).replace("=     7503", "=    10398"))
    with pytest.raises(iso.MistFormatError, match="mesa_revision"):
        iso.grid_from_files([iso.parse_cmd(toy_cmd_text(0.0, (8.0,))), newer], s)
    other = iso.parse_cmd(toy_cmd_text(-0.5, (8.0,)).replace("Gaia (DR2/MAW)", "SDSS"))
    with pytest.raises(iso.MistFormatError, match="photometric_system"):
        iso.grid_from_files([iso.parse_cmd(toy_cmd_text(0.0, (8.0,))), other], s)
    renamed = iso.parse_cmd(toy_cmd_text(0.0, (8.0,)).replace("Gaia_BP_DR2Rev", "Gaia_BP_EDR3"))
    with pytest.raises(iso.MistFormatError, match="Gaia_BP_DR2Rev"):
        iso.grid_from_files([renamed], s)
    with pytest.raises(iso.MistFormatError, match="age range"):
        iso.grid_from_files([iso.parse_cmd(toy_cmd_text(0.0, (7.0,)))], s)


@pytest.mark.req("STL-033")
def test_the_grid_survives_a_round_trip_through_its_stored_table() -> None:
    g = grid((0.0, -0.25), (8.0, 8.05))
    g.meta["source_sha256"] = "ab" * 32
    back = iso.Grid.from_table(g.to_table())
    assert back.fehs() == [-0.25, 0.0] and back.ages(0.0) == [8.0, 8.05]
    assert back.meta["source_sha256"] == "ab" * 32 and back.meta["mist_version"] == "1.2"
    np.testing.assert_allclose(
        back.isochrone(0.0, 8.05)["initial_mass"], g.isochrone(0.0, 8.05)["initial_mass"], rtol=1e-6
    )
    with pytest.raises(iso.MistFormatError, match="lacks"):
        iso.Grid.from_table(g.to_table().drop_columns(["bp"]))


# --- fitting a cluster (STL-034) ---------------------------------------------------------------------


@pytest.mark.req("STL-034")
def test_extinction_errors_and_the_parallax_prior_follow_the_settings() -> None:
    s = iso.load_settings()
    a_g, e = iso.extinction(1.0, s)
    assert float(a_g) == pytest.approx(0.789) and float(e) == pytest.approx(1.002 - 0.589)
    ce, ge = iso.photometric_errors(np.asarray([15.0, 20.0]), s)
    assert ce[0] == pytest.approx(math.hypot(0.03, 0.005)) and ce[1] == pytest.approx(math.hypot(0.03, 0.05))
    assert ge[0] == pytest.approx(math.hypot(0.08, 0.001))
    mu, err = iso.modulus_prior(2.483, 0.0, s)  # 2.483 + 0.017 = 2.5 mas
    assert mu == pytest.approx(5 * math.log10(40.0)) and err == pytest.approx(5 / math.log(10) * 0.011 / 2.5)
    with pytest.raises(ValueError, match="no distance"):
        iso.modulus_prior(-0.5, 0.1, s)


@pytest.mark.req("STL-034")
def test_a_synthetic_cluster_gets_back_its_age_extinction_and_distance() -> None:
    s = settings()
    g = grid()
    colour, mag, _ = cluster(0.0, 8.8, 0.4, 8.0, 160, seed=3, settings=s, binaries=16)
    fit = iso.fit_cluster(colour, mag, 2.5 - 0.017, 0.02, g, s)
    assert fit is not None
    assert abs(fit["log_age"] - 8.8) <= 0.05
    assert fit["av"] == pytest.approx(0.4, abs=0.1)
    assert fit["distance_modulus"] == pytest.approx(8.0, abs=0.06)
    assert fit["on_isochrone_share"] >= 0.85  # the 16 binaries may sit off the line; the rest on it
    lo, hi = fit["ranges"]["log_age"]
    assert lo <= fit["log_age"] <= hi
    assert fit["heaviest_living_msun"] == pytest.approx(turnoff(fit["log_age"]), rel=0.03)
    assert {"feh", "log_age", "cost"} <= set(fit["surface"][0])
    assert fit["schema"] == iso.SCHEMA


@pytest.mark.req("STL-034")
def test_an_old_cluster_is_told_from_a_young_one_by_its_turnoff() -> None:
    s = settings()
    g = grid()
    young = iso.fit_cluster(*cluster(0.0, 8.0, 0.1, 7.0, 120, seed=5, settings=s)[:2], 4.0 - 0.017, 0.02, g, s)
    old = iso.fit_cluster(*cluster(0.0, 9.5, 0.1, 7.0, 120, seed=5, settings=s)[:2], 4.0 - 0.017, 0.02, g, s)
    assert young is not None and old is not None
    assert abs(young["log_age"] - 8.0) <= 0.1 and abs(old["log_age"] - 9.5) <= 0.1
    assert young["turnoff_initial_msun"] > old["turnoff_initial_msun"]


@pytest.mark.req("STL-034")
def test_too_few_members_or_no_metallicity_in_range_give_no_fit() -> None:
    s = settings()
    g = grid()
    colour, mag, _ = cluster(0.0, 8.8, 0.4, 8.0, 20, seed=1, settings=s)
    colour[8:] = np.nan
    assert iso.fit_cluster(colour, mag, 2.5, 0.02, g, s) is None
    with pytest.raises(ValueError, match="no \\[Fe/H\\]"):
        iso.fit_cluster(
            *cluster(0.0, 8.8, 0.4, 8.0, 40, seed=1, settings=s)[:2], 2.5, 0.02, g, settings(feh_min=0.3, feh_max=0.4)
        )


@pytest.mark.req("STL-034")
def test_a_profile_range_holds_every_value_within_delta_of_the_best() -> None:
    trials = np.asarray(
        [
            [0.0, 8.0, 0.1, 7.0, 10.0],
            [0.0, 8.1, 0.1, 7.0, 10.6],
            [0.0, 8.2, 0.2, 7.1, 12.0],
            [0.25, 8.0, 0.3, 7.0, 10.9],
        ]
    )
    r = iso.profile_ranges(trials, 1.0)
    assert r == {"feh": [0.0, 0.25], "log_age": [8.0, 8.1], "av": [0.1, 0.3], "distance_modulus": [7.0, 7.0]}
    cells = {(x["feh"], x["log_age"]): x["cost"] for x in iso.surface(trials)}
    assert cells == {(0.0, 8.0): 10.0, (0.0, 8.1): 10.6, (0.0, 8.2): 12.0, (0.25, 8.0): 10.9}


@pytest.mark.req("STL-034")
def test_a_known_cluster_is_checked_against_its_window_by_any_of_its_names() -> None:
    s = iso.load_settings()
    hit = iso.known_answer(["near-b009-t010-c0", "hr23:Melotte_22"], {"log_age": 8.05}, s)
    assert hit is not None and hit["cluster"] == "Melotte 22" and hit["inside"]
    assert hit["verified_window"] is False
    miss = iso.known_answer(["Pleiades"], {"log_age": 9.2}, s)
    assert miss is not None and not miss["inside"]
    assert iso.known_answer(["Pleiades"], None, s)["inside"] is False  # type: ignore[index]
    assert iso.known_answer(["NGC 9999"], {"log_age": 8.0}, s) is None


# --- masses (STL-035) ------------------------------------------------------------------------------


@pytest.mark.req("STL-035")
def test_members_on_the_isochrone_get_their_masses_back() -> None:
    s = settings()
    g = grid()
    fit = {"feh": 0.0, "log_age": 9.0, "av": 0.3, "distance_modulus": 9.0}
    one = g.isochrone(0.0, 9.0)
    rows = np.asarray([10, 60, 120, 150])
    a_g, e = iso.extinction(0.3, s)
    colour = one["bp"][rows] - one["rp"][rows] + e
    mag = one["g"][rows] + 9.0 + a_g
    out = iso.member_masses(np.append(colour, [np.nan, 0.5]), np.append(mag, [12.0, 2.0]), fit, g, s)
    np.testing.assert_allclose(out["initial_mass"][:4], one["initial_mass"][rows], rtol=0.02)
    assert np.all(out["phase"][:4] == 0) and np.all(out["on_isochrone"][:4] == 1)
    assert math.isnan(out["initial_mass"][4]) and out["on_isochrone"][4] == 0
    assert out["on_isochrone"][5] == 0 and out["distance"][5] > 3  # far above the line: not on it
    assert math.isnan(out["initial_mass"][5]) and math.isnan(out["star_mass"][5])  # so no mass
    assert out["phase"][5] in (0.0, 2.0)


@pytest.mark.req("STL-034")
def test_the_nearest_point_is_exact_in_each_stars_own_errors() -> None:
    rng = np.random.default_rng(8)
    one = grid().isochrone(0.0, 9.0)
    ln = iso.line(one, [-1, 0, 2, 3, 4], 0.02)
    matcher = iso._Matcher(ln, (0.03, 0.08), 4)
    n = 400
    colour = rng.uniform(-0.3, 2.2, n)
    mag = rng.uniform(-2.0, 11.0, n)
    sc = 0.03 * np.exp(rng.uniform(0.0, 3.0, n))  # errors never below the floors, often far above
    sm = 0.08 * np.exp(rng.uniform(0.0, 1.0, n))
    d2, _ = matcher.nearest(colour, mag, sc, sm)
    brute = ((colour[:, None] - ln.colour[None, :]) / sc[:, None]) ** 2 + (
        (mag[:, None] - ln.mag[None, :]) / sm[:, None]
    ) ** 2
    np.testing.assert_allclose(d2, brute.min(axis=1), rtol=1e-12)
    capped, _ = matcher.nearest(colour, mag, sc, sm, limit=3.0)
    exact = brute.min(axis=1)
    near = np.sqrt(exact) < 3.0
    np.testing.assert_allclose(capped[near], exact[near], rtol=1e-12)
    assert np.all(capped[~near] >= 9.0 - 1e-9)  # beyond the limit: still beyond it


@pytest.mark.req("STL-035")
def test_the_heaviest_living_star_bounds_any_normal_partner() -> None:
    one = toy_isochrone(0.0, 9.0)
    data = {"phase": one["phase"], "initial_mass": one["initial_mass"], "star_mass": one["star_mass"]}
    assert iso.turnoff_mass(data) == pytest.approx(turnoff(9.0), rel=1e-4)
    assert iso.heaviest_living(data) == pytest.approx(one["star_mass"].max(), rel=1e-4)
    assert iso.turnoff_mass({**data, "phase": np.full(data["phase"].size, 2.0)}) is None


@pytest.mark.req("STL-035")
def test_the_imf_is_continuous_at_the_break_and_falls_with_mass() -> None:
    s = iso.load_settings()
    w = iso.imf_weight(np.asarray([0.1, 0.4999999, 0.5, 1.0, 2.0]), s)
    assert w[1] == pytest.approx(w[2], rel=1e-5) and w[2] == 1.0
    assert w[0] == pytest.approx(5.0**1.3) and w[3] == pytest.approx(2.0**-2.3)
    assert np.all(np.diff(w) < 0)


@pytest.mark.req("STL-035")
def test_a_field_stars_mass_is_recovered_and_a_star_far_from_every_model_is_off_grid() -> None:
    s = iso.load_settings()
    g = grid()
    one = g.isochrone(0.0, 8.5)
    rows = np.asarray([40, 100])
    colour = np.concatenate([one["bp"][rows] - one["rp"][rows], [-0.4, np.nan]])
    mag = np.concatenate([one["g"][rows], [11.0, 5.0]])
    est = iso.field_star_estimates(mag, colour, g, s, feh=0.1)
    for e, truth in zip(est[:2], one["initial_mass"][rows], strict=True):
        lo, mid, hi = e["initial_mass_msun"]
        assert lo <= mid <= hi and mid == pytest.approx(truth, rel=0.1)
        assert e["feh"] == 0.0 and e["phase"] == 0 and not e["off_grid"]
        assert e["log_age"][0] <= e["log_age"][2]
    assert est[2]["off_grid"]  # a white dwarf's colour and magnitude
    assert est[3] == {"feh": 0.0, "off_grid": True}
    # a faint star with a poor parallax: its errors grow with its apparent G and its modulus's error
    sharp = iso.field_star_estimates(mag[:1], colour[:1], g, s, feh=0.0)[0]["initial_mass_msun"]
    blurred = iso.field_star_estimates(
        mag[:1], colour[:1], g, s, feh=0.0, apparent_g=mag[:1] + 14.0, modulus_err=np.asarray([0.5])
    )[0]["initial_mass_msun"]
    assert blurred[2] - blurred[0] > 1.5 * (sharp[2] - sharp[0])


# --- the companion screen with an isochrone (STL-036) -----------------------------------------------


def companion_sky(s: dict[str, Any]) -> dict[str, Any]:
    rng = np.random.default_rng(11)
    colour, mag, _ = cluster(0.0, 9.0, 0.2, 8.0, 200, seed=4, settings=s)
    n = colour.size
    stars = {
        "source_id": np.arange(1, n + 1, dtype=np.int64),
        "ra": 60.0 + rng.normal(0.0, 0.3, n),
        "dec": 20.0 + rng.normal(0.0, 0.3, n),
        "parallax": rng.normal(2.5, 0.02, n),
        "parallax_error": np.full(n, 0.02),
        "pmra": rng.normal(10.0, 0.2, n),
        "pmra_error": np.full(n, 0.02),
        "pmdec": rng.normal(-5.0, 0.2, n),
        "pmdec_error": np.full(n, 0.02),
        "phot_g_mean_mag": mag,
        "bp_rp": colour,
        "ruwe": np.full(n, 1.0),
    }
    a0 = [20.0, 0.3]
    orbits = {
        "source_id": np.asarray([5, 6], dtype=np.int64),
        "nss_solution_type": np.asarray(["Orbital"] * 2, dtype=object),
        "combination_method": np.asarray(["Orbital+M1"] * 2, dtype=object),
        "period": np.asarray([900.0, 300.0]),
        "period_error": np.asarray([9.0, 3.0]),
        "phot_g_mean_mag": mag[4:6],
        "a_thiele_innes": np.asarray(a0),
        "b_thiele_innes": np.zeros(2),
        "f_thiele_innes": np.zeros(2),
        "g_thiele_innes": np.asarray(a0),
        "parallax": np.asarray([2.5, 2.5]),
        "parallax_error": np.asarray([0.02, 0.02]),
        "significance": np.full(2, 30.0),
        "m1": np.asarray([1.0, 1.0]),
        "m2_lower": np.asarray([20.0, 0.2]),
        "fluxratio_upper": np.asarray([0.0, 0.0]),
        "flag": np.asarray(["", ""], dtype=object),
    }
    field = comp.FieldCounts(comp.load_settings())
    field.add(stars["phot_g_mean_mag"], stars["parallax"], stars["ruwe"])
    clusters = [comp.Cluster("mid-b001-t001-c0", "potentially_new", "", frozenset(range(1, n + 1)))]
    return {"stars": stars, "orbits": orbits, "field": field, "clusters": clusters}


@pytest.mark.req("STL-036")
def test_a_screen_with_a_grid_adds_isochrone_masses_and_flags_a_partner_heavier_than_any_living_star(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    s = settings()
    monkeypatch.setattr(iso, "load_settings", lambda: s)
    sky = companion_sky(s)
    g = grid()
    result = comp.screen(sky["clusters"], sky["stars"], sky["orbits"], sky["field"], comp.load_settings(), g)
    (row,) = result["clusters"]
    assert abs(row["iso_log_age"] - 9.0) <= 0.1 and row["iso_heaviest_living_msun"] > 1.5
    by_id = {r["source_id"]: r for r in result["candidates"]}
    heavy, light = by_id[5], by_id[6]
    assert heavy["heavier_than_living"] and not light["heavier_than_living"]
    assert heavy["m1_mist_msun"] == pytest.approx(
        iso.member_masses(sky["stars"]["bp_rp"][4:5], sky["stars"]["phot_g_mean_mag"][4:5], _fit(row), g, s)[
            "star_mass"
        ][0],
        rel=1e-6,
    )
    # the same mass function: a heavier visible star means a heavier dark partner
    assert (heavy["m2_dark_mist_msun"] > heavy["m2_dark_msun"]) == (heavy["m1_mist_msun"] > 1.0)
    assert heavy["verdict"] == comp.COMPACT  # Gaia's rule decides the verdict, the isochrone sits beside it
    far = {**sky["stars"], "parallax": np.full(sky["stars"]["parallax"].size, 2.5)}
    monkeypatch.setattr(iso, "load_settings", lambda: settings(feh_min=0.4, feh_max=0.5))
    (none,) = comp.screen(sky["clusters"], far, sky["orbits"], sky["field"], comp.load_settings(), g)["clusters"]
    assert "no isochrone fit" in none["iso_note"] and none["members"] > 0  # one cluster's failure, not the run's
    plain = comp.screen(sky["clusters"], sky["stars"], sky["orbits"], sky["field"], comp.load_settings())
    assert "iso_log_age" not in plain["clusters"][0] and "m1_mist_msun" not in plain["candidates"][0]


def _fit(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "feh": row["iso_feh"],
        "log_age": row["iso_log_age"],
        "av": row["iso_av"],
        "distance_modulus": row["iso_distance_modulus"],
    }

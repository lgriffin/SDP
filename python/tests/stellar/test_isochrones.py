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
    assert f.meta["photometric_system"] == "UBV(RI)c, 2MASS, Kepler, Hipparcos, Gaia (Vega)"
    assert (f.meta["feh"], f.meta["afe"], f.meta["vvcrit"], f.meta["av"]) == (0.25, 0.0, 0.4, 0.0)
    assert f.meta["zinit"] == pytest.approx(0.0142857)
    assert len(f.blocks) == 2
    first = f.blocks[0]
    assert set(first) >= {"EEP", "Gaia_G_EDR3", "Gaia_BP_EDR3", "phase"}
    np.testing.assert_allclose(first["Gaia_G_EDR3"], toy_isochrone(0.25, 8.0)["Gaia_G_EDR3"], rtol=1e-9)
    assert f.blocks[1]["log10_isochrone_age_yr"][0] == 9.0
    # the other header layout: the system on the line after its label, Av and Rv labels over their values
    other = (
        toy_cmd_text(0.25, (8.0,))
        .replace("# photometric system   = UBV", "# photometric system\n# ----\n# UBV")
        .replace("# CCM89 extinction: Av =  0.000", "#     Av       Rv\n#  0.0300   3.1000")
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
    no_av = "\n".join(ln for ln in lines if "Av =" not in ln)
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
    np.testing.assert_allclose(one["g"], toy_isochrone(-0.5, 8.05)["Gaia_G_EDR3"])
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
    other = iso.parse_cmd(toy_cmd_text(-0.5, (8.0,)).replace("Gaia (Vega)", "SDSS"))
    with pytest.raises(iso.MistFormatError, match="photometric_system"):
        iso.grid_from_files([iso.parse_cmd(toy_cmd_text(0.0, (8.0,))), other], s)
    renamed = iso.parse_cmd(toy_cmd_text(0.0, (8.0,)).replace("Gaia_BP_EDR3", "Gaia_BP_DR2Rev"))
    with pytest.raises(iso.MistFormatError, match="Gaia_BP_EDR3"):
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
    # the Pleiades window was checked against Babusiaux et al. 2018 (log age 8.04); the globulars' not yet
    assert hit["verified_window"] is True and "Babusiaux" in hit["window_source"]
    globular = iso.known_answer(["M3"], {"log_age": 10.1, "feh": -1.5}, s)
    assert globular is not None and globular["verified_window"] is False
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


#: The first lines of MIST_v1.2_feh_m0.25_afe_p0.0_vvcrit0.4_UBVRIplus.iso.cmd from the real
#: MIST_v1.2_vvcrit0.4_UBVRIplus.txz, copied character for character on 2026-11-15 (the table cut to its
#: first three rows, so its row count reads 3 instead of 266).
REAL_HEAD = (
    "# MIST version number  = 1.2     \n"
    "# MESA revision number =     7503\n"
    "# photometric system   = UBV(RI)c, 2MASS, Kepler, Hipparcos, Gaia (Vega)                 \n"
    "# --------------------------------------------------------------------------------------\n"
    "#  Yinit        Zinit   [Fe/H]   [a/Fe]  v/vcrit                                        \n"
    "# 0.2610  8.03343E-03    -0.25     0.00     0.40\n"
    "# --------------------------------------------------------------------------------------\n"
    "# number of isochrones =   107\n"
    "# CCM89 extinction: Av =  0.000\n"
    "# --------------------------------------------------------------------------------------\n"
    "# number of EEPs, cols =     3   34\n"
    "#   1                               2                               3                               4                               5                               6                               7                               8                   9                  10                  11                  12                  13                  14                  15                  16                  17                  18                  19                  20                  21                  22                  23                  24                  25                  26                  27                  28                  29                  30                  31                  32                  33                  34\n"
    "# EEP          log10_isochrone_age_yr                    initial_mass                       star_mass                        log_Teff                           log_g                           log_L                     [Fe/H]_init              [Fe/H]           Bessell_U           Bessell_B           Bessell_V           Bessell_R           Bessell_I             2MASS_J             2MASS_H            2MASS_Ks           Kepler_Kp          Kepler_D51        Hipparcos_Hp             Tycho_B             Tycho_V       Gaia_G_DR2Rev      Gaia_BP_DR2Rev      Gaia_RP_DR2Rev          Gaia_G_MAW        Gaia_BP_MAWb        Gaia_BP_MAWf         Gaia_RP_MAW                TESS         Gaia_G_EDR3        Gaia_BP_EDR3        Gaia_RP_EDR3               phase\n"
    "   14         5.0000000000000000E+000         1.0000000000000001E-001         9.9999987465850482E-002         3.4862207608164812E+000         3.1313420078437875E+000        -7.9366710581785394E-001        -2.5000000000000000E-001           -0.217200           11.576773           10.914362            9.781591            8.393749            6.590892            4.781341            4.071001            3.865110            8.209890           10.191441            9.569141           11.018705           10.004640            8.105919           10.045743            6.877904            8.119210           10.003132           10.036953            6.852643            6.732532            8.137337           10.024206            6.851886           -1.000000\n"
    "   15         5.0000000000000000E+000         1.0264483521052409E-001         1.0264482205216272E-001         3.4873618400144251E+000         3.1268080177764190E+000        -7.7525415297838363E-001        -2.5000000000000000E-001           -0.217200           11.515804           10.847707            9.713316            8.334562            6.538465            4.738173            4.022935            3.819549            8.154368           10.124570            9.506421           10.953330            9.935961            8.052544            9.977938            6.826506            8.065170            9.935299            9.969279            6.801315            6.682570            8.083336            9.956364            6.800464           -1.000000\n"
    "   16         5.0000000000000000E+000         1.0703921552827966E-001         1.0703920129064491E-001         3.4892434780670603E+000         3.1193671044823663E+000        -7.4481045034640248E-001        -2.5000000000000000E-001           -0.217200           11.415371           10.737701            9.600525            8.236778            6.451846            4.666836            3.943412            3.744168            8.062635           10.014151            9.402812           10.845475            9.822510            7.964358            9.865941            6.741587            7.975888            9.823252            9.857505            6.716511            6.600021            7.994116            9.844304            6.715503           -1.000000\n"
)


@pytest.mark.req("STL-033")
def test_the_real_mist_v12_header_and_its_gaia_edr3_columns_are_read() -> None:
    f = iso.parse_cmd(REAL_HEAD)
    assert f.meta["mist_version"] == "1.2" and f.meta["mesa_revision"] == "7503"
    assert f.meta["photometric_system"] == "UBV(RI)c, 2MASS, Kepler, Hipparcos, Gaia (Vega)"
    assert (f.meta["feh"], f.meta["av"], f.meta["vvcrit"]) == (-0.25, 0.0, 0.4)
    (block,) = f.blocks
    assert len(block) == 34 and block["EEP"].tolist() == [14.0, 15.0, 16.0]
    s = copy.deepcopy(ORIGINAL())
    s["grid"]["log_age_min"] = 5.0
    g = iso.grid_from_files([f], s)
    one = g.isochrone(-0.25, 5.0)
    np.testing.assert_allclose(one["g"], [8.137337, 8.083336, 7.994116])  # Gaia_G_EDR3, not the DR2 or MAW G
    np.testing.assert_allclose(one["bp"], [10.024206, 9.956364, 9.844304])
    np.testing.assert_allclose(one["rp"], [6.851886, 6.800464, 6.715503])
    np.testing.assert_allclose(one["initial_mass"], [0.1, 0.10264483521052409, 0.10703921552827966])
    assert one["phase"].tolist() == [-1.0, -1.0, -1.0]


# --- speed and the edges of the search (STL-037) ---------------------------------------------------


@pytest.mark.req("STL-037")
def test_every_extinction_matched_in_one_query_costs_the_same_as_one_at_a_time() -> None:
    s = settings()
    g = grid()
    colour, mag, _ = cluster(0.0, 8.8, 0.4, 8.0, 120, seed=7, settings=s, binaries=12)
    stars = iso.Stars.from_photometry(colour, mag, s)
    ln = iso.line(g.isochrone(0.0, 8.8), s["phases"]["fit"], float(s["fit"]["densify_step"]))
    m = iso._Matcher(ln, (s["photometry"]["colour_floor"], s["photometry"]["g_floor"]), int(s["fit"]["neighbours"]))
    avs, mods = np.round(np.arange(0.0, 1.0, 0.1), 6), np.round(np.arange(7.8, 8.2, 0.05), 6)
    together = iso._costs(m, stars, avs, mods, (8.0, 0.05), s, chunk=1_000)  # small chunks: several queries
    alone = np.vstack([iso._costs(m, stars, np.asarray([av]), mods, (8.0, 0.05), s) for av in avs])
    np.testing.assert_allclose(together, alone, rtol=1e-12)


@pytest.mark.req("STL-038")
def test_a_fit_that_ends_on_the_edge_of_its_search_says_so() -> None:
    g = grid((-0.25, 0.0, 0.25))
    s = settings()
    colour, mag, _ = cluster(0.0, 8.8, 0.4, 8.0, 160, seed=3, settings=s, binaries=16)
    inside = iso.fit_cluster(colour, mag, 2.5 - 0.017, 0.02, g, s)
    assert inside is not None and inside["at_grid_edge"] == []
    # the oldest cluster the grid holds: its age can only be at the grid's last one
    old = iso.fit_cluster(*cluster(0.0, AGES[-1], 0.1, 7.0, 120, seed=5, settings=s)[:2], 4.0 - 0.017, 0.02, g, s)
    assert old is not None and old["log_age"] == AGES[-1] and "log_age" in old["at_grid_edge"]
    # dust beyond the search's largest A_V
    dusty = iso.fit_cluster(*cluster(0.0, 8.8, 1.6, 8.0, 160, seed=3, settings=s)[:2], 2.5 - 0.017, 0.02, g, s)
    assert dusty is not None and dusty["av"] == pytest.approx(1.0) and "av" in dusty["at_grid_edge"]


@pytest.mark.req("STL-039")
def test_a_globular_is_checked_against_its_age_and_metallicity_windows() -> None:
    s = iso.load_settings()
    assert s["fit"]["feh_min"] <= -2.5  # the search reaches metal-poor globulars such as M30
    good = iso.known_answer(["far-b046-t114-c002", "HSC_46, NGC_5904", "NGC_5904"], {"log_age": 10.1, "feh": -1.25}, s)
    assert good is not None and good["cluster"] == "NGC 5904" and good["inside"]
    assert good["window_feh"] == [-1.6, -0.9] and good["fitted_feh"] == -1.25
    metal_rich = iso.known_answer(["NGC_7099"], {"log_age": 10.1, "feh": -0.75}, s)
    assert metal_rich is not None and not metal_rich["inside"]  # the right age, the wrong [Fe/H]
    young = iso.known_answer(["M3"], {"log_age": 9.1, "feh": -1.5}, s)
    assert young is not None and not young["inside"]
    pleiades = iso.known_answer(["Pleiades"], {"log_age": 8.05, "feh": 0.5}, s)
    assert pleiades is not None and pleiades["inside"] and "window_feh" not in pleiades  # an age window only


@pytest.mark.req("STL-040")
def test_a_star_above_the_line_has_a_negative_g_offset() -> None:
    s = settings()
    g = grid()
    fit = {"feh": 0.0, "log_age": 9.0, "av": 0.3, "distance_modulus": 9.0}
    one = g.isochrone(0.0, 9.0)
    a_g, e = iso.extinction(0.3, s)
    colour = np.repeat(one["bp"][60] - one["rp"][60] + e, 2)
    mag = one["g"][60] + 9.0 + a_g + np.asarray([0.0, -0.75])  # on the line, and an equal-mass pair
    out = iso.member_masses(colour, mag, fit, g, s)
    assert abs(out["g_offset"][0]) < 0.01
    assert out["g_offset"][1] < -0.3 and math.isnan(out["star_mass"][1])


@pytest.mark.req("STL-041")
def test_a_screen_given_an_earlier_fit_uses_it_instead_of_fitting_again(monkeypatch: pytest.MonkeyPatch) -> None:
    s = settings()
    monkeypatch.setattr(iso, "load_settings", lambda: s)
    sky = companion_sky(s)
    g = grid()
    first = comp.screen(sky["clusters"], sky["stars"], sky["orbits"], sky["field"], comp.load_settings(), g)
    (row,) = first["clusters"]
    earlier = {
        **_fit(row),
        "a_g": 0.0,
        "on_isochrone_share": row["iso_on_isochrone_share"],
        "heaviest_living_msun": row["iso_heaviest_living_msun"],
        "turnoff_initial_msun": row["iso_turnoff_initial_msun"],
        "feh_prior": {"mean": 0.0, "sigma": 0.2, "source": "test"},
    }

    def no_fit(*_: Any, **__: Any) -> None:
        raise AssertionError("fitted again")

    monkeypatch.setattr(iso, "fit_cluster", no_fit)
    again = comp.screen(
        sky["clusters"],
        sky["stars"],
        sky["orbits"],
        sky["field"],
        comp.load_settings(),
        g,
        {row["cluster_id"]: earlier},
    )
    (reused,) = again["clusters"]
    assert reused["iso_from"] == "the sweep fit of the seed members" and reused["iso_log_age"] == row["iso_log_age"]
    assert "disc gradient" in row["iso_feh_prior"] and reused["iso_feh_prior"] == "0.0 ± 0.2 (test)"  # both record it
    assert reused["iso_on_isochrone_share"] == pytest.approx(row["iso_on_isochrone_share"], abs=0.2)  # measured here
    assert [r["heavier_than_living"] for r in again["candidates"]] == [
        r["heavier_than_living"] for r in first["candidates"]
    ]
    assert all("m1_mist_g_offset_mag" in r for r in again["candidates"])


# --- a prior on [Fe/H] and the models' red dwarfs (STL-042, STL-043) ----------------------------------


@pytest.mark.req("STL-042")
def test_a_metallicity_prior_comes_from_a_known_name_the_disc_or_the_halo() -> None:
    s = iso.load_settings()
    known = iso.metallicity_prior(["far-b046-t114-c002", "HSC_46, NGC_5904"], None, None, None, s)
    assert known is not None and known["source"] == "known: NGC 5904"
    assert known["mean"] == pytest.approx(-1.29 + math.log10(0.638 * 10**0.3 + 0.362), abs=1e-3)  # Salaris 1993
    pleiades = iso.metallicity_prior(["x"], 56.75, 24.12, 136.0, s)  # in the plane, near the Sun
    assert pleiades is not None and abs(pleiades["mean"]) < 0.05 and pleiades["source"].startswith("disc")
    inner = iso.metallicity_prior(["x"], 266.4, -29.0, 3000.0, s)  # towards the Galactic centre: richer
    assert inner is not None and inner["mean"] > 0.1
    halo = iso.metallicity_prior(["x"], 229.64, 2.08, 7500.0, s)  # M5's place, unnamed: far above the plane
    assert halo is not None and halo["source"].startswith("halo") and halo["mean"] < -1.0
    across = iso.metallicity_prior(["x"], [359.9, 0.1], [24.0, 24.2], 136.0, s)  # members either side of RA 0
    near0 = iso.metallicity_prior(["x"], 0.0, 24.1, 136.0, s)
    assert across is not None and near0 is not None and across["mean"] == pytest.approx(near0["mean"], abs=0.01)
    assert iso.metallicity_prior(["x"], None, None, 136.0, s) is None
    assert iso.metallicity_prior(["x"], [float("nan")], [1.0], 136.0, s) is None


@pytest.mark.req("STL-042")
def test_a_metallicity_prior_pulls_the_fit_and_is_recorded() -> None:
    s = settings()
    g = grid((-0.25, 0.0, 0.25))
    colour, mag, _ = cluster(0.0, 8.8, 0.4, 8.0, 160, seed=3, settings=s, binaries=16)
    free = iso.fit_cluster(colour, mag, 2.5 - 0.017, 0.02, g, s)
    tight = iso.fit_cluster(colour, mag, 2.5 - 0.017, 0.02, g, s, {"mean": 0.25, "sigma": 0.01, "source": "test"})
    assert free is not None and tight is not None
    assert free["feh"] == 0.0 and free["feh_prior"] is None
    assert tight["feh"] == 0.25 and tight["feh_prior"]["source"] == "test"


@pytest.mark.req("STL-043")
def test_red_dwarfs_carry_the_models_extra_colour_error() -> None:
    s = iso.load_settings()
    g = np.asarray([12.0, 16.0])
    plain_c, plain_g = iso.photometric_errors(g, s)
    ce, ge = iso.photometric_errors(g, s, abs_g=np.asarray([5.0, 9.0]))  # a G dwarf and an M dwarf
    assert ce[0] == plain_c[0] and np.all(ge == plain_g)
    assert ce[1] == pytest.approx(math.hypot(plain_c[1], s["photometry"]["red_dwarf_colour_error"]))


@pytest.mark.req("STL-043")
def test_every_trial_of_a_fit_sees_the_same_red_dwarfs() -> None:
    s = iso.load_settings()
    cut = s["photometry"]["red_dwarf_abs_g"]
    stars = iso.Stars.from_photometry(np.asarray([1.0, 1.0]), np.asarray([15.5, 18.0]), s, 15.5 - cut - 0.5)
    plain, _ = iso.photometric_errors(stars.g, s)
    assert list(stars.red) == [True, True] and stars.colour_err[0] > plain[0]
    dusty = stars.judged(s, 15.5 - cut + 0.5)  # with dust the first star is brighter than the cutoff
    assert list(dusty.red) == [False, True] and dusty.colour_err[0] == pytest.approx(plain[0])
    seen: list[np.ndarray] = []

    class Matcher:
        def nearest(
            self, colour: np.ndarray, mag: np.ndarray, ce: np.ndarray, ge: np.ndarray, cap: float
        ) -> tuple[np.ndarray, np.ndarray]:
            seen.append(np.asarray(ce).reshape(-1, 2))
            return np.zeros(colour.size), np.zeros(colour.size, dtype=np.int64)

    iso._costs(Matcher(), stars, np.asarray([0.0, 2.0]), np.asarray([8.0, 9.0]), (8.5, 0.1), s)  # type: ignore[arg-type]
    rows = np.concatenate(seen)
    assert np.all(rows == rows[0])  # no trial gets larger errors, and so a smaller cost, for nothing


@pytest.mark.req("STL-043")
def test_a_dusty_clusters_red_dwarfs_are_judged_after_its_dust() -> None:
    s = settings(av_max=2.0)
    g = grid((0.0,))
    colour, mag, _ = cluster(0.0, 8.8, 1.5, 8.0, 160, seed=3, settings=s)
    fit = iso.fit_cluster(colour, mag, 2.5 - 0.017, 0.02, g, s)
    assert fit is not None
    true_abs_g = mag - float(iso.extinction(1.5, s)[0]) - 8.0
    without_dust = int(np.sum(mag - 8.0 > s["photometry"]["red_dwarf_abs_g"]))
    expected = int(np.sum(true_abs_g > s["photometry"]["red_dwarf_abs_g"]))
    assert expected < without_dust  # the dust would have made turnoff stars look like red dwarfs
    assert abs(fit["red_dwarfs"] - expected) <= 3


@pytest.mark.req("STL-043")
def test_member_flags_use_the_red_dwarfs_the_fit_judged() -> None:
    s = settings(av_max=2.0)
    g = grid((0.0,))
    colour, mag, _ = cluster(0.0, 8.8, 1.5, 8.0, 160, seed=3, settings=s)
    fit = iso.fit_cluster(colour, mag, 2.5 - 0.017, 0.02, g, s)
    assert fit is not None and fit["red_dwarf_offset"] > 8.5  # judged after the dust, not at the parallax distance
    same = iso.member_masses(colour, mag, fit, g, s)
    other = iso.member_masses(colour, mag, {**fit, "red_dwarf_offset": fit["red_dwarf_offset"] - 3.0}, g, s)
    assert not np.allclose(same["distance"], other["distance"], equal_nan=True)  # the flags follow the fit's


@pytest.mark.req("STL-033")
def test_each_checked_open_cluster_window_holds_the_published_gaia_age_it_cites() -> None:
    """Babusiaux et al. 2018, A&A 616, A10, Table 2 (log years), read on 2026-11-25."""
    published = {"Melotte 22": 8.04, "Melotte 25": 8.90, "NGC 2632": 8.85, "NGC 2682": 9.54}
    for entry in iso.load_settings()["known_answers"]["clusters"]:
        if entry["names"][0] in published:
            lo, hi = entry["log_age"]
            assert entry["verified"] is True and f"{published[entry['names'][0]]:.2f}" in entry["source"]
            assert lo <= published[entry["names"][0]] <= hi

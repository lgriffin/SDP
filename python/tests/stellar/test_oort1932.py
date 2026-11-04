"""UC-05: Oort's 1932 vertical force, his arithmetic and formula (7) on synthetic columns (STL-025 to STL-028)."""

from __future__ import annotations

import copy
import dataclasses
import math
from typing import Any

import numpy as np
import pytest

from sdp_stellar import oort1932 as o

pytestmark = pytest.mark.tier("T1")

K_INF = 3.8e-9  # cm/s^2, Oort's plateau, used only as a plausible plant
Z0 = 150.0
K1_SLAB = K_INF / Z0 / o.PC_CM  # s^-2


def r300_choices() -> dict[str, Any]:
    """The committed choices on the 300 pc cylinder for every tracer together: 8 scenarios."""
    choices = copy.deepcopy(o.load_choices())
    choices["scenarios"]["group"] = ["all"]
    choices["scenarios"]["cylinder_radius_pc"] = [300.0]
    return choices


@pytest.fixture(scope="module")
def sheet() -> dict[str, Any]:
    return o.synthetic_column(n=300_000, potential=o.sheet_potential(K_INF, Z0), rv_error_km_s=5.0, seed=5)


@pytest.fixture(scope="module")
def slab() -> dict[str, Any]:
    return o.synthetic_column(n=300_000, potential=o.slab_potential(K1_SLAB), rv_error_km_s=5.0, seed=6)


@pytest.fixture(scope="module")
def sheet_baseline(sheet: dict[str, Any]) -> o.Estimate:
    c = o.load_choices()
    zp = o.baseline(c).parallax_zero_point_mas
    return o.estimate(o.place(sheet, zp), o.place(sheet, zp), o.baseline(c), c)


def planted_line(estimate: o.Estimate) -> float:
    """Oort's straight line through the origin, fitted to the planted sheet force at the same heights."""
    z = estimate.edges_pc
    k = K_INF * np.tanh(z / Z0)
    inner = z <= o.load_choices()["fit"]["inner_max_pc"]
    return float((z[inner] * k[inner]).sum() / (z[inner] ** 2).sum() / o.PC_CM)


# --- Part A ---------------------------------------------------------------------------------------


@pytest.mark.req("STL-025")
def test_oorts_arithmetic_is_recomputed_beside_his_printed_figures() -> None:
    a = o.arithmetic()
    assert a["eq9_constant"]["computed"] == pytest.approx(7.48e-9, rel=0.005)
    assert a["z0_constant"]["computed"] == pytest.approx(6.17e8, rel=0.001)
    assert a["z0_example"]["computed_Z0_km_s"] == pytest.approx(30.0, abs=0.5)
    for row in a["shape_factor_x"]:
        assert row["computed"] == pytest.approx(row["printed"], abs=0.001)
    slopes = a["dK_dz_s2"]["computed_through_origin"]
    assert slopes["z<=200"] == pytest.approx(5.56e-30, rel=0.01)
    assert slopes["z<=150"] < slopes["z<=200"] < a["dK_dz_s2"]["printed"]
    assert a["density_flat_layer_msun_pc3"]["computed"] == pytest.approx(0.099, abs=0.001)
    assert a["plateau_K_1e9"]["computed"] == pytest.approx(3.79, abs=0.01)
    assert a["unit_msun_pc3_in_g_cm3"]["computed"] == pytest.approx(6.77e-23, rel=0.002)
    assert [r["printed"] for r in a["table14_integral"]][-1] == 1884.0
    assert a["table14_integral"][0]["computed_trapezium"] == pytest.approx(19.25)


@pytest.mark.req("STL-025")
def test_the_density_from_a_slope_takes_off_the_rotation_term() -> None:
    flat = o.density_from_slope(5.62e-30)
    assert flat == pytest.approx(0.099, abs=0.001)
    with_rotation = o.density_from_slope(5.62e-30, 15.22, -13.24)
    assert with_rotation < flat
    assert (flat - with_rotation) / flat == pytest.approx(0.021, abs=0.002)
    assert o.shape_factor(None) == 1.0


# --- the column -----------------------------------------------------------------------------------


@pytest.mark.req("STL-026")
def test_the_column_volume_is_a_cone_then_a_cylinder_and_odd_in_z() -> None:
    r, b = 300.0, 20.0
    zc = r * math.tan(math.radians(b))
    cone = float(o.slab_volume(0.0, zc, r, b))
    assert cone == pytest.approx(math.pi * r**2 * zc / 3.0, rel=1e-9)
    assert float(o.slab_volume(zc, zc + 100.0, r, b)) == pytest.approx(math.pi * r**2 * 100.0)
    assert float(o.slab_volume(-50.0, 0.0, r, b)) == pytest.approx(float(o.slab_volume(0.0, 50.0, r, b)))
    rng = np.random.default_rng(0)
    pts = rng.uniform([-r, -r, -200.0], [r, r, 200.0], size=(400_000, 3))
    rxy = np.hypot(pts[:, 0], pts[:, 1])
    inside = (rxy <= r) & (np.degrees(np.arctan2(np.abs(pts[:, 2]), rxy)) >= b) & (pts[:, 2] > -30) & (pts[:, 2] < 170)
    box = (2 * r) ** 2 * 400.0
    assert float(o.slab_volume(-30.0, 170.0, r, b)) == pytest.approx(inside.mean() * box, rel=0.02)


@pytest.mark.req("STL-026")
def test_every_star_a_scenario_uses_survives_the_per_file_reduction(sheet: dict[str, Any]) -> None:
    c = o.load_choices()
    kept = o.column_mask(sheet, c)
    for s in o.scenarios(c)[::7]:
        stars = o.place(sheet, s.parallax_zero_point_mas)
        used = o.scenario_mask(stars, s, c) & (np.abs(stars.z_helio) <= c["selection"]["z_table_max_pc"])
        assert not (used & ~kept).any(), s.label
    plx, g = (np.asarray(sheet[k]) for k in ("parallax", "phot_g_mean_mag"))
    for name, op, value in o.prefilters(c):
        col = {"parallax": plx, "ruwe": np.asarray(sheet["ruwe"]), "phot_g_mean_mag": g}[name]
        assert op in ("<", ">")
        assert ((col > value) if op == ">" else (col < value))[kept].all()


@pytest.mark.req("STL-026")
def test_the_shared_cuts_keep_a_star_without_a_radial_velocity_in_the_density(sheet: dict[str, Any]) -> None:
    cols = {k: np.asarray(v)[:1000].copy() for k, v in sheet.items()}
    cols["radial_velocity"][:10] = np.nan
    cols["ruwe"][10:20] = 2.0
    density = o.quality_mask(cols)
    velocity = o.quality_mask(cols, velocities=True)
    assert density[:10].all() and not velocity[:10].any()
    assert not density[10:20].any()
    assert density.sum() == 990 and velocity.sum() == 980


@pytest.mark.req("STL-026")
def test_the_suns_height_is_found_from_the_two_hemispheres(sheet_baseline: o.Estimate) -> None:
    assert sheet_baseline.z_sun_pc == pytest.approx(20.0, abs=5.0)


# --- formula (7) ----------------------------------------------------------------------------------


@pytest.mark.req("STL-027")
def test_a_self_gravitating_sheet_gives_its_slope_and_flattens(sheet_baseline: o.Estimate) -> None:
    e, c = sheet_baseline, o.load_choices()
    expected = planted_line(e)
    assert expected < K_INF / Z0 / o.PC_CM  # tanh already bends inside 200 pc, as the protocol warns
    assert e.k1 == pytest.approx(expected, rel=0.10)
    assert e.k1 >= 5 * e.k1_error
    rot = c["rotation"]
    assert e.rho0 == pytest.approx(o.density_from_slope(expected, rot["A_km_s_kpc"], rot["B_km_s_kpc"]), rel=0.10)
    assert e.plateau == pytest.approx(K_INF * np.tanh(350.0 / Z0), rel=0.15)
    assert o.shape(e, c) == "flattens"


@pytest.mark.req("STL-027")
def test_a_uniform_slab_gives_its_slope_and_does_not_flatten(slab: dict[str, Any]) -> None:
    c = o.load_choices()
    zp = o.baseline(c).parallax_zero_point_mas
    e = o.estimate(o.place(slab, zp), o.place(slab, zp), o.baseline(c), c)
    assert e.k1 == pytest.approx(K1_SLAB, rel=0.10)
    assert e.flattening == pytest.approx(1.0, abs=0.25)
    assert o.shape(e, c) == "does_not_flatten"


@pytest.mark.req("STL-027")
def test_the_radial_velocity_error_is_taken_off_the_mean_square(sheet_baseline: o.Estimate) -> None:
    b = sheet_baseline.bins
    good = b.n_velocity >= 1000
    assert good.sum() >= 5
    # the plant is 20 km/s at every height, with 5 km/s radial-velocity errors that would add up to ~4 km/s in quadrature
    assert np.sqrt(b.sigma2[good]) == pytest.approx(20.0, rel=0.03)


@pytest.mark.req("STL-027")
def test_too_few_stars_is_a_named_failure(sheet: dict[str, Any]) -> None:
    c = o.load_choices()
    few = {k: np.asarray(v)[:400] for k, v in sheet.items()}
    reasons = o.run(few, few, baseline_only=True)["baseline"]
    assert "fewer than the declared 3 and 3" in reasons["failed"]
    empty = {k: np.asarray(v)[:0] for k, v in sheet.items()}
    with pytest.raises(ValueError, match="0 density"):
        o.estimate(o.place(empty, 0.0), o.place(empty, 0.0), o.baseline(c), c)


@pytest.mark.req("STL-027")
def test_clipping_drops_a_runaway_from_its_bin_only_when_on(sheet: dict[str, Any]) -> None:
    c = o.load_choices()
    s_on = o.baseline(c)
    s_off = dataclasses.replace(s_on, clip=False)
    stars = o.place(sheet, s_on.parallax_zero_point_mas)
    assert stars.v_z is not None
    v_z = stars.v_z.copy()
    v_z[np.argmin(np.abs(np.abs(stars.z_helio + 20.0) - 425.0))] = 900.0
    wild = dataclasses.replace(stars, v_z=v_z)
    z_sun = 20.0
    on = o.bin_column(wild, wild, z_sun, s_on, c)
    off = o.bin_column(wild, wild, z_sun, s_off, c)
    i = 8  # the 400-450 pc bin
    assert off.sigma2[i] - on.sigma2[i] > 0.5 * 900.0**2 / off.n_velocity[i]
    assert off.n_velocity[i] > on.n_velocity[i]


# --- verdict --------------------------------------------------------------------------------------


@pytest.mark.req("STL-028")
@pytest.mark.parametrize(("which", "status"), [("sheet", "supports_UC05_H1"), ("slab", "supports_UC05_H0")])
def test_the_verdict_follows_the_planted_force(which: str, status: str, request: pytest.FixtureRequest) -> None:
    cols = request.getfixturevalue(which)
    report = o.run(cols, cols, choices=r300_choices())
    assert report["verdict"]["status"] == status
    assert len(report["scenarios"]) == 8
    assert report["choices_match_committed"] is False


@pytest.mark.req("STL-028")
def test_oorts_density_is_compared_with_the_local_density(sheet_baseline: o.Estimate) -> None:
    c = o.load_choices()
    near = dataclasses.replace(sheet_baseline, rho0=0.095, rho0_error=0.005)
    far = dataclasses.replace(sheet_baseline, rho0=0.25, rho0_error=0.005)
    sigma = 0.2 * 0.092 / 0.6745
    assert o.comparison({"a": near}, c)["status"] == "agrees_with_oort"
    assert o.comparison({"a": far}, c)["status"] == "disagrees_with_oort"
    assert o.comparison({"a": near, "b": far}, c)["status"] == "mixed"
    assert o.comparison({"a": near, "b": "failed"}, c)["status"] == "mixed"
    assert o.comparison({"a": far}, c)["z"]["a"] == pytest.approx((0.25 - 0.092) / math.hypot(0.005, sigma))


@pytest.mark.req("STL-028")
def test_a_failed_scenario_or_a_quick_look_decides_nothing(sheet: dict[str, Any], sheet_baseline: o.Estimate) -> None:
    c = o.load_choices()
    assert o.detection({"a": sheet_baseline}, c)["status"] == "supports_UC05_H1"
    assert o.detection({"a": sheet_baseline, "b": "too few"}, c)["status"] == "inconclusive"
    assert o.detection({}, c)["status"] == "inconclusive"
    weak = dataclasses.replace(sheet_baseline, k1_error=sheet_baseline.k1)
    assert o.shape(weak, c) == "undecided"
    quick = o.run(sheet, sheet, baseline_only=True)
    assert quick["verdict"]["status"] == "inconclusive" and quick["verdict"]["quick_look"]
    assert quick["choices_match_committed"] is True
    assert quick["committed_choices_sha256"] == o.choices_sha256()


@pytest.mark.req("STL-028")
def test_the_report_sets_gaia_beside_table_14(sheet_baseline: o.Estimate) -> None:
    side = o.beside_oort(sheet_baseline, o.load_choices())
    rows = {r["z_pc"]: r for r in side["table14"]}
    assert rows[200]["gaia_K_1e9"] == pytest.approx(float(sheet_baseline.k[3]) * 1e9)
    assert rows[200]["oort_mean_corrected_1e9"] == 3.52
    assert side["rho0_less_oorts_visible_stars"]["value"] == pytest.approx(sheet_baseline.rho0 - 0.038)
    assert side["z_sun_pc"]["oort_tentative"] == 30.0
    doc = sheet_baseline.to_document(with_bins=True)
    assert doc["bins"][0]["z_lo_pc"] == 0.0 and doc["rho0_g_cm3"]["value"] > 0

"""T8: Einstein's 1921 calculation on real Gaia DR3 stars in M13, checked against published values (REL-021).

The recorded cone is the 11,552 stars that pass UC-03's cuts, exported by Leigh from the bulk Gaia copy with
``sdp einstein1921 export`` (tests/cassettes/gaia-m13/README.md). The expected numbers are the ones Leigh's
full runs gave on 2026-10-31; the published ones come from einstein1921_corrections.json.
"""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq
import pytest

from sdp_relativity import einstein1921 as e
from sdp_relativity import einstein1921_corrections as c

pytestmark = [pytest.mark.tier("T8"), pytest.mark.scientific]

CONE = Path(__file__).resolve().parents[1] / "cassettes" / "gaia-m13" / "m13-cone.parquet"
CONE_SHA256 = "43e7a3d4db03eb6433134ea1add1b95c99702b3d8fd63b883a9fe72d7e852282"
R_MAX = 0.6


@pytest.fixture(scope="module")
def cone() -> dict[str, Any]:
    table = pq.read_table(CONE)
    return {name: table.column(name).to_numpy(zero_copy_only=False) for name in table.column_names}


@pytest.fixture(scope="module")
def stars(cone: dict[str, Any]) -> tuple[e.Stars, tuple[float, float]]:
    selected, _ = e.select(cone)
    return selected, e.find_centre(selected)


@pytest.fixture(scope="module")
def baseline_fit(stars: tuple[e.Stars, tuple[float, float]]) -> e.Fit:
    selected, centre = stars
    base = c.baseline()
    return e.fit(selected.take(selected.g < base.g_max), centre, base.fit_scenario(R_MAX))


def corrected(
    stars: tuple[e.Stars, tuple[float, float]], correction: c.Correction, start: e.Fit | None = None
) -> tuple[e.Fit, c.Corrected]:
    """One UC-03b fit, started as the run starts it: from the corrected baseline's optimum."""
    selected, centre = stars
    limited = selected.take(selected.g < correction.g_max)
    f = e.fit(limited, centre, correction.fit_scenario(R_MAX), start=None if start is None else start.theta)
    assert f.converged
    return f, c.corrected(limited, f, correction, R_MAX)


@pytest.mark.req("REL-021")
def test_the_recorded_cone_is_the_one_committed_and_every_star_passes_the_cuts(cone: dict[str, Any]) -> None:
    assert hashlib.sha256(CONE.read_bytes()).hexdigest() == CONE_SHA256
    assert np.asarray(cone["ra"]).size == 11_552
    assert e.keep_mask(cone).all()


@pytest.mark.req("REL-021")
def test_uc03_on_m13_straddles_the_threshold_so_its_verdict_is_inconclusive(cone: dict[str, Any]) -> None:
    # the whole protocol, all 48 scenarios; only the bootstrap is declared shorter (5 refits, not 100) to keep it fast
    choices = copy.deepcopy(e.load_choices())
    choices["bootstrap"]["resamples"] = 5
    report = e.run(cone, choices=choices)
    p = report["baseline"]["physical"]
    assert p["distance_pc"] == pytest.approx(8231, rel=0.005)
    assert p["mass_to_light"] == pytest.approx(11.11, rel=0.02)
    v = report["verdict"]
    assert v["bootstrap_complete"] is True
    assert v["status"] == "inconclusive"
    assert (len(v["scenarios_below"]), len(v["scenarios_at_or_above"]), v["scenarios_failed"]) == (11, 37, [])
    assert all("r_in=3.0'" in label for label in v["scenarios_below"])  # Leigh's run: every one below 10 cut at 3'
    low = e.Scenario(3.0, 0.6, 1.1, -0.034, 0.1).label
    assert report["scenarios"][low]["physical"]["mass_to_light"] == pytest.approx(7.91, rel=0.02)


@pytest.mark.req("REL-021")
def test_the_bright_stars_recover_the_published_mass_of_m13(
    stars: tuple[e.Stars, tuple[float, float]], baseline_fit: e.Fit
) -> None:
    published = c.load_choices()["published"]["comparison_only"]["baumgardt_mass_solar"]
    _, bright = corrected(stars, c.Correction(3.0, 17.0, 1.0, "published"), start=baseline_fit)
    assert bright.mass_solar == pytest.approx(published, rel=0.20)
    assert bright.sigma0_kms == pytest.approx(8.37, rel=0.02)


@pytest.mark.req("REL-021")
def test_with_published_distance_and_light_m13_bears_out_einsteins_conclusion(
    stars: tuple[e.Stars, tuple[float, float]],
) -> None:
    f, base = corrected(stars, c.baseline())
    assert not c.degenerate(f.a_deg, c.baseline().r_in_arcmin)
    assert base.distance_pc == 7420.0
    assert base.mass_to_light_v == pytest.approx(3.84, rel=0.02)
    assert base.mass_to_light_v < c.load_choices()["hypothesis_threshold_mass_to_light"]
    assert base.v_rms_kms < 26  # Einstein's borrowed speed is too high
    assert base.two_a_ly == pytest.approx(36.2, rel=0.02)
    # the known weakness: all stars to G = 19 give nearly twice the published mass
    assert base.mass_solar > 1.5 * c.load_choices()["published"]["comparison_only"]["baumgardt_mass_solar"]


@pytest.mark.req("REL-021")
def test_a_six_arcminute_core_cut_on_m13_is_flagged_degenerate(
    stars: tuple[e.Stars, tuple[float, float]], baseline_fit: e.Fit
) -> None:
    f, v = corrected(stars, c.Correction(6.0, 19.0, 1.0, "published"), start=baseline_fit)
    assert c.degenerate(f.a_deg, 6.0)
    assert v.mass_solar == pytest.approx(5.83e5, rel=0.05)  # the mass survives; a and sigma_0 do not

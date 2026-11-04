"""T8: Oort's 1927 rotation constants on real Gaia DR3 stars, checked against Leigh's full run (STL-019).

The recorded sample is a fixed source_id cut of the stars UC-04 reads, exported by Leigh from the bulk Gaia
copy with ``sdp oort1927 export`` (tests/cassettes/gaia-oort/README.md). ``report.json`` beside it is
Leigh's full run on the whole copy on 2026-11-01; the sample must reproduce its baseline within the
sample's own errors, which are about 25 times the full run's.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import pytest

from sdp_stellar import oort1927 as o

pytestmark = [pytest.mark.tier("T8"), pytest.mark.scientific]

HERE = Path(__file__).resolve().parents[1] / "cassettes" / "gaia-oort"
SAMPLE = HERE / "oort-sample.parquet"
SAMPLE_SHA256 = "7a90abd9bd8a62fe969dccb738a28e4ded3f75e1dc055cc2d50473bc7ec8cf3b"


@pytest.fixture(scope="module")
def full() -> dict[str, Any]:
    report: dict[str, Any] = json.loads((HERE / "report.json").read_text("utf-8"))
    return report


@pytest.fixture(scope="module")
def sample() -> dict[str, Any]:
    table = pq.read_table(SAMPLE)
    return {name: table.column(name).to_numpy(zero_copy_only=False) for name in table.column_names}


@pytest.fixture(scope="module")
def baseline_fit(sample: dict[str, Any]) -> o.Fit:
    stars = o.observe(sample, o.baseline().parallax_zero_point_mas)
    return o.fit(stars.take(o.scenario_mask(stars, o.baseline())))


@pytest.mark.req("STL-019")
def test_the_recorded_sample_is_the_one_committed_and_every_star_passes_the_shared_cuts(
    sample: dict[str, Any],
) -> None:
    assert hashlib.sha256(SAMPLE.read_bytes()).hexdigest() == SAMPLE_SHA256
    assert sample["source_id"].size == 26_357
    assert (sample["source_id"] % 10_000 == 0).all()
    assert o.quality_mask(sample).all()


@pytest.mark.req("STL-019")
def test_the_full_run_read_the_committed_choices_and_detected_rotation_in_every_scenario(
    full: dict[str, Any],
) -> None:
    assert full["committed_choices_sha256"] == o.choices_sha256()
    assert full["choices_match_committed"] is True and full["quick_look"] is False
    v = full["verdict"]
    assert v["status"] == "supports_UC04_H1"
    assert (len(v["scenarios_detected"]), len(v["scenarios_null"]), v["scenarios_failed"]) == (48, 0, [])
    for method in ("proper_motions", "radial_velocities"):
        assert full[method]["reading"]["status"] == "supports_UC04_H1"
        assert full[method]["comparison"]["status"] == "disagrees_with_oort"
        assert all(z < -3.5 for z in full[method]["comparison"]["z"].values())
    assert full["arithmetic"] == json.loads(json.dumps(o.arithmetic()))  # part A as recomputed today


@pytest.mark.req("STL-019")
@pytest.mark.parametrize("p", o.PARAMETERS)
def test_the_sample_reproduces_the_full_baseline_within_its_own_errors(
    full: dict[str, Any], baseline_fit: o.Fit, p: str
) -> None:
    whole = full["proper_motions"]["baseline"][p]["value"]
    assert abs(baseline_fit.value(p) - whole) < 3 * baseline_fit.error(p)


@pytest.mark.req("STL-019")
def test_gaias_stars_show_oorts_rotation_at_half_his_value(full: dict[str, Any], baseline_fit: o.Fit) -> None:
    f = baseline_fit
    assert f.value("A") > 5 * f.error("A") and f.value("B") < -5 * f.error("B")
    d = f.derived()
    whole = full["proper_motions"]["baseline"]["derived"]
    assert d["l0_deg"] == pytest.approx(whole["l0_deg"], abs=3 * (d["l0_error_deg"] or 0))
    assert d["omega"] == pytest.approx(whole["omega"], abs=3 * (d["omega_error"] or 0))
    oort = 1000.0 * o.load_choices()["oort"]["total_A_km_s_pc"]
    assert d["amplitude"] is not None and d["amplitude"] < 0.6 * oort
    # the radial-velocity method, Oort's own, gave the same A on the full rvs subset
    rv = full["radial_velocities"]["baseline"]["A"]
    assert rv["value"] == pytest.approx(full["proper_motions"]["baseline"]["A"]["value"], abs=0.2)

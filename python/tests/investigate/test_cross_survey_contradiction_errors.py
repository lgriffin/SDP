"""T4: cross-survey-contradiction starts each budget from the archive's own errors, 2MASS ``*_msigcom``
and its error ellipse, Pan-STARRS PSF magnitude and mean position errors, and the dialects keep those
columns only when a fetch asks for them (INV-027).
"""

from __future__ import annotations

import json
import math
from typing import Any

import numpy as np
import pyarrow as pa
import pytest
from tests.investigate.test_cross_survey_contradiction import (
    KS_EXCESS,
    MOVED,
    PLANTED,
    THRESHOLDS,
    comparisons,
    context,
    field,
    sigma_mag,
)

from sdp.adapters.archives.dialect import curate_columnar, load_dialects, load_golden
from sdp_investigate.contradiction import (
    MAX_MAG_ERROR,
    SURVEYS,
    CrossSurveyContradiction,
    _band,
    _Builder,
    _error,
    _position,
    position_error,
)

pytestmark = [pytest.mark.tier("T4"), pytest.mark.req("INV-027")]

DIALECTS = load_dialects()
OPTIONAL = {
    "2mass-psc": {"j_msigcom", "h_msigcom", "k_msigcom", "err_maj", "err_min"},
    "ps1-dr2": {
        "zMeanPSFMag",
        "yMeanPSFMag",
        "gMeanPSFMagErr",
        "rMeanPSFMagErr",
        "iMeanPSFMagErr",
        "zMeanPSFMagErr",
        "yMeanPSFMagErr",
        "raMeanErr",
        "decMeanErr",
    },
}


@pytest.mark.parametrize("dialect_id", sorted(OPTIONAL))
def test_the_error_columns_are_optional_and_kept_when_fetched(dialect_id: str) -> None:
    dialect = DIALECTS[dialect_id]
    assert set(dialect.optional_quality_flags) == OPTIONAL[dialect_id]
    assert not OPTIONAL[dialect_id] & dialect.native_columns(), "an older profile must still curate"
    golden = load_golden(dialect_id)
    for native, canonical in zip(golden["native"], golden["canonical"], strict=True):
        assert OPTIONAL[dialect_id] <= set(canonical["quality_flags"])
        assert all(canonical["quality_flags"][f] == native[f] for f in OPTIONAL[dialect_id])


@pytest.mark.parametrize("dialect_id", sorted(OPTIONAL))
def test_a_fetch_without_them_curates_to_the_rows_it_always_did(dialect_id: str) -> None:
    dialect = DIALECTS[dialect_id]
    native = [
        {k: v for k, v in row.items() if k not in OPTIONAL[dialect_id]} for row in load_golden(dialect_id)["native"]
    ]
    rows = [dialect.row(r) for r in native]
    for row in rows:
        kept = set(json.loads(row["quality_flags"]))
        assert kept == set(dialect.quality_flags)
    columnar = curate_columnar(dialect, pa.Table.from_pylist(native)).to_pylist()
    assert [r["quality_flags"] for r in columnar] == [r["quality_flags"] for r in rows]
    full = curate_columnar(dialect, pa.Table.from_pylist(load_golden(dialect_id)["native"])).to_pylist()
    assert [r["quality_flags"] for r in full] == [
        dialect.row(r)["quality_flags"] for r in load_golden(dialect_id)["native"]
    ]


def test_an_error_is_the_canonical_column_else_the_archive_s_and_never_a_sentinel() -> None:
    flags = '{"j_msigcom": 0.03, "gMeanPSFMagErr": -999.0, "k_msigcom": 0.0, "h_msigcom": 7.0}'
    row: dict[str, Any] = {"quality_flags": flags, "j_mag_err": 0.05}
    assert _error(row, "j_mag_err", "j_msigcom", MAX_MAG_ERROR) == 0.05
    assert _error({"quality_flags": flags}, "j_mag_err", "j_msigcom", MAX_MAG_ERROR) == 0.03
    assert _error(row, "ps_g_mag_err", "gMeanPSFMagErr", MAX_MAG_ERROR) == 0.0
    assert _error(row, "k_mag_err", "k_msigcom", MAX_MAG_ERROR) == 0.0
    assert _error(row, "h_mag_err", "h_msigcom", MAX_MAG_ERROR) == 0.0
    assert _error(row, "h_mag_err", "h_msigcom", 7.5) == 7.0
    assert _error(row, "y_mag_err", None, MAX_MAG_ERROR) == 0.0
    assert _error(row, "y_mag_err", "yMeanPSFMagErr", MAX_MAG_ERROR) == 0.0


def test_a_position_error_is_the_rms_of_the_archive_s_two_in_mas() -> None:
    two = SURVEYS["2mass"].position_error
    assert two == ("err_maj", "err_min")
    assert SURVEYS["ps1"].position_error == ("raMeanErr", "decMeanErr")
    row = {"quality_flags": '{"err_maj": 0.08, "err_min": 0.06}'}
    assert position_error(row, two) == pytest.approx(1000.0 * math.sqrt((0.0064 + 0.0036) / 2.0))
    assert position_error(row | {"pos_err_mas": 30.0}, two) == 30.0
    assert position_error(row, None) == 0.0
    assert position_error({"quality_flags": '{"err_maj": 0.08}'}, two) == 0.0
    assert position_error({"quality_flags": '{"err_maj": 0.08, "err_min": -999}'}, two) == 0.0
    assert position_error({"quality_flags": '{"err_maj": 9.0, "err_min": 9.5}'}, two) == pytest.approx(
        1000.0 * math.sqrt((81.0 + 90.25) / 2.0)
    )
    assert position_error({"quality_flags": '{"err_maj": 9.0, "err_min": 10.0}'}, two) == 0.0


def test_the_comparisons_carry_the_archive_s_errors() -> None:
    gaia = {
        "ra_deg": 10.0,
        "dec_deg": 0.0,
        "epoch_jyear": 2016.0,
        "pm_ra_cosdec_masyr": 0.0,
        "pm_dec_masyr": 0.0,
        "pm_ra_cosdec_masyr_err": 0.0,
        "pm_dec_masyr_err": 0.0,
    }
    row = {
        "ra_deg": 10.0,
        "dec_deg": 0.0,
        "epoch_jyear": 2000.0,
        "j_mag": 12.0,
        "quality_flags": '{"ph_qual": "AAA", "j_msigcom": 0.04, "err_maj": 0.1, "err_min": 0.1}',
    }
    builder = _Builder()
    _position(builder, 0, gaia, row, 12.0, 1.0, False, survey_error=SURVEYS["2mass"].position_error)
    assert builder.build("2mass:position", "2mass", "position").sigma[0] == pytest.approx(100.0)
    bands = _Builder()
    _band(bands, 0, SURVEYS["2mass"].bands[0], SURVEYS["2mass"], row, 12.0, 1.0, gaia, False)
    assert bands.build("2mass:J", "2mass", "band").sigma[0] == pytest.approx(0.04)


@pytest.fixture(scope="module")
def reported() -> dict[str, Any]:
    test = CrossSurveyContradiction()
    return {str(r.subject.object_key): r for r in test.run(field(reported=True), THRESHOLDS, context())}


def test_with_the_true_errors_reported_the_field_adds_little(reported: dict[str, Any]) -> None:
    for name in ("2mass:J", "2mass:position", "ps1:g"):
        rows = [comparisons(r)[name] for r in reported.values() if name in comparisons(r)]
        counted = [c for c in rows if c["counted"]]
        assert len(counted) > 40
        own = np.array([c["reported_sigma"] for c in counted])
        extra = np.array([c["calibration_sigma"] for c in counted])
        assert np.median(own) > 0
        assert np.median(extra) < 0.6 * np.median(own), name


def test_the_reported_band_error_is_the_row_s_own(reported: dict[str, Any]) -> None:
    g = {b.object_key: float(b.rows("gaia")[0]["g_mag"]) for b in field(reported=True)}
    checked = 0
    for key, result in reported.items():
        c = comparisons(result).get("2mass:J")
        if c:
            assert c["reported_sigma"] == pytest.approx(sigma_mag(g[key]))
            checked += 1
    assert checked > 100


def test_the_planted_contradictions_are_still_found(reported: dict[str, Any]) -> None:
    flagged = {k for k, r in reported.items() if r.p_value is not None and r.p_value < 0.01}
    assert {f"gaia:DR3:{MOVED}", f"gaia:DR3:{KS_EXCESS}"} <= flagged
    assert not flagged - {f"gaia:DR3:{i}" for i in PLANTED}


def test_null_fields_with_reported_errors_stay_near_the_nominal_rate() -> None:
    null = CrossSurveyContradiction().null(field(reported=True), THRESHOLDS, context(9), trials=20, alpha=0.01)
    assert null.examined > 2000
    assert null.false_positives / null.examined < 0.025

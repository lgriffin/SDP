"""T8R: the hidden-companion screen on real Gaia DR3 orbits, checked against Leigh's PC run and against Gaia's
own masses (TST-011, STL-020, STL-023).

``orbits-slice.parquet`` is cut by rule (``tools/realdata_slice.py nss-orbits``) from the full table of 169,227
Gaia DR3 orbits the screen reads; ``field-dr3.csv`` is the field list ``sdp companions field`` wrote on Leigh's PC
on 2026-11-04. Both are real data, neither was written or edited by hand (tests/cassettes/gaia-nss-dr3/README.md).
The settings file is read, never changed: UC-06 is locked (uc06-prereg-v1).
"""

from __future__ import annotations

import csv
import hashlib
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq
import pytest
from tests.conftest import realdata_file

from sdp_stellar import companions as c

pytestmark = [pytest.mark.tier("T8"), pytest.mark.scientific]

HERE = Path(__file__).resolve().parents[1] / "cassettes" / "gaia-nss-dr3"
SLICE = HERE / "orbits-slice.parquet"
SLICE_SHA256 = "22f265dbaf5e42f91e3e5982e24d3ca89080380e1d24b91dbed2745c2ec79f96"
FIELD = HERE / "field-dr3.csv"
FIELD_SHA256 = "9645ec45d8697c7b7b53cb0cad5a53c8a01e0a097dd04238f431fd1a4f9eee5c"
FULL = "companions/gaia-dr3-orbits.parquet"
GAIA_BH1 = 4373465352415301632


def read(path: Path) -> dict[str, Any]:
    table = pq.read_table(path)
    return {name: table.column(name).to_numpy(zero_copy_only=False) for name in table.column_names}


@pytest.fixture(scope="module")
def orbits() -> dict[str, Any]:
    return read(SLICE)


@pytest.fixture(scope="module")
def pc_field() -> dict[int, dict[str, str]]:
    with FIELD.open(newline="", encoding="utf-8") as handle:
        return {int(r["source_id"]): r for r in csv.DictReader(handle)}


def assert_reproduces_pc_field(rows: list[dict[str, Any]], pc_field: dict[int, dict[str, str]]) -> None:
    """Same orbits, same verdicts, same masses to the six decimals the PC run wrote."""
    ours = {r["source_id"]: r for r in rows}
    assert set(ours) == set(pc_field)
    for sid, theirs in pc_field.items():
        mine = ours[sid]
        assert mine["verdict"] == theirs["verdict"], sid
        for key in ("m2_lower_msun", "m2_dark_msun", "m1_msun", "period_days", "parallax_mas"):
            want = theirs[key]
            assert (mine[key] is None) == (want == ""), (sid, key)
            if want != "":
                assert mine[key] == pytest.approx(float(want), abs=1.5e-6), (sid, key)


@pytest.mark.req("TST-011")
def test_the_committed_recordings_are_the_ones_cut_from_the_full_table() -> None:
    assert hashlib.sha256(SLICE.read_bytes()).hexdigest() == SLICE_SHA256
    assert hashlib.sha256(FIELD.read_bytes()).hexdigest() == FIELD_SHA256
    meta = pq.read_schema(SLICE).metadata
    assert meta[b"sdp.release"] == b"gaia:DR3"
    assert b"15226 of 169227 orbits" in meta[b"sdp.slice"]


@pytest.mark.req("TST-011", "STL-023")
def test_the_slice_gives_back_leighs_pc_field_list_orbit_for_orbit(
    orbits: dict[str, Any], pc_field: dict[int, dict[str, str]]
) -> None:
    # the slice holds every flagged orbit, so its field list must be the PC run's whole list
    rows = c.field_orbits(orbits, c.load_settings())
    assert Counter(r["verdict"] for r in rows) == {c.COMPACT: 136, c.DARK: 5098}
    assert_reproduces_pc_field(rows, pc_field)
    bh1 = next(r for r in rows if r["source_id"] == GAIA_BH1)
    assert bh1["verdict"] == c.COMPACT


@pytest.mark.req("TST-011", "STL-020")
def test_sdps_kepler_arithmetic_gives_back_gaias_dark_partner_mass_for_every_dark_orbit(orbits: dict[str, Any]) -> None:
    # an independent implementation on 5,000 real orbits: Gaia's binary_masses m2 for a dark partner comes from
    # the same mass function, so SDP's own solve must agree with it to rounding
    masses, verdict = c.judge(orbits, c.load_settings())
    gaia_m2 = np.asarray(orbits["m2"], dtype=float)
    dark = np.isin(verdict, c.FLAGGED) & np.isfinite(gaia_m2)
    assert dark.sum() >= 5000
    np.testing.assert_allclose(masses["m2_msun"][dark], gaia_m2[dark], rtol=1e-6)


@pytest.mark.req("TST-011")
def test_every_verdict_on_the_slice_is_as_main_gave_it_on_2026_11_25(orbits: dict[str, Any]) -> None:
    # a snapshot, not a known answer: the orbits near a cut are in the slice so a code change that moves any
    # verdict shows here first; if one moves on purpose, say why in the change and update the counts
    _, verdict = c.judge(orbits, c.load_settings())
    assert Counter(verdict.tolist()) == {c.ORDINARY: 6065, c.DARK: 5098, c.UNUSABLE: 3927, c.COMPACT: 136}


@pytest.mark.realdata
@pytest.mark.req("TST-011")
def test_the_full_dr3_orbit_table_gives_back_leighs_pc_field_list(pc_field: dict[int, dict[str, str]]) -> None:
    path = realdata_file(FULL)
    # the PC fetches its own copy (`sdp companions fetch`), so its bytes may differ from the share's; the rows may not
    assert pq.read_schema(path).metadata[b"sdp.release"] == b"gaia:DR3"
    full = read(path)
    assert full["source_id"].size == 169_227
    assert_reproduces_pc_field(c.field_orbits(full, c.load_settings()), pc_field)


@pytest.mark.req("TST-011")
def test_the_known_answer_goes_red_when_the_rule_moves(
    orbits: dict[str, Any], pc_field: dict[int, dict[str, str]]
) -> None:
    # the tier's signal that it can fail: a copy of the settings with the mass cut moved by 0.1 solar masses
    # (the locked file itself is not touched) no longer gives back the PC run
    moved = c.load_settings()
    moved["mass"] = {**moved["mass"], "chandrasekhar_msun": 1.5}
    with pytest.raises(AssertionError):
        assert_reproduces_pc_field(c.field_orbits(orbits, moved), pc_field)

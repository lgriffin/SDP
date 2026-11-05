"""T1: the Gaia release delta engine on invented clusters (STL-030, STL-031). Not Gaia data."""

from __future__ import annotations

import math

import numpy as np
import pytest

from sdp_stellar import dr4_delta as d

pytestmark = pytest.mark.tier("T1")
SETTINGS = d.load_settings()
CLUSTER = d.Cluster(
    "Test_1", ra=100.0, dec=20.0, rt=0.5, plx=2.0, s_plx=0.05, pmra=-5.0, s_pmra=0.2, pmdec=3.0, s_pmdec=0.2
)


def stars(
    n_members: int = 200, n_field: int = 2000, seed: int = 3, plx_shift: float = 0.0, first_id: int = 1
) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    m = {
        "source_id": np.arange(first_id, first_id + n_members, dtype=np.int64),
        "ra": 100.0 + rng.normal(0, 0.1, n_members),
        "dec": 20.0 + rng.normal(0, 0.1, n_members),
        "parallax": rng.normal(2.0 + plx_shift, 0.03, n_members),
        "parallax_error": np.full(n_members, 0.05),
        "pmra": rng.normal(-5.0, 0.1, n_members),
        "pmra_error": np.full(n_members, 0.05),
        "pmdec": rng.normal(3.0, 0.1, n_members),
        "pmdec_error": np.full(n_members, 0.05),
        "phot_g_mean_mag": rng.uniform(10, 17, n_members),
    }
    f = {
        "source_id": np.arange(10_000_000, 10_000_000 + n_field, dtype=np.int64),
        "ra": rng.uniform(99.0, 101.0, n_field),
        "dec": rng.uniform(19.0, 21.0, n_field),
        "parallax": rng.uniform(1.0, 3.0, n_field),
        "parallax_error": np.full(n_field, 0.05),
        "pmra": rng.normal(0.0, 8.0, n_field),
        "pmra_error": np.full(n_field, 0.05),
        "pmdec": rng.normal(0.0, 8.0, n_field),
        "pmdec_error": np.full(n_field, 0.05),
        "phot_g_mean_mag": rng.uniform(10, 17, n_field),
    }
    return {k: np.concatenate([m[k], f[k]]) for k in m}


@pytest.mark.req("STL-030")
def test_the_selection_finds_the_members_and_almost_no_field_stars() -> None:
    s = stars()
    chosen = d.members(s, CLUSTER, SETTINGS)
    assert np.count_nonzero(chosen[:200]) >= 190
    assert np.count_nonzero(chosen[200:]) <= 5


@pytest.mark.req("STL-030")
@pytest.mark.parametrize(
    ("column", "value"),
    [("ra", 101.0), ("phot_g_mean_mag", 18.5), ("parallax_error", 1.0), ("parallax", 2.5), ("pmra", -2.0)],
)
def test_each_cut_alone_removes_a_member(column: str, value: float) -> None:
    s = stars(n_field=0)
    assert d.members(s, CLUSTER, SETTINGS)[0] or column == "ra"
    s[column] = s[column].astype(float).copy()
    s[column][0] = value
    assert not d.members(s, CLUSTER, SETTINGS)[0]


@pytest.mark.req("STL-030")
def test_the_distance_on_the_sky_is_right_near_the_pole_and_across_ra_zero() -> None:
    assert d.angular_distance_deg(359.9, 0.0, np.array([0.1]), np.array([0.0]))[0] == pytest.approx(0.2, abs=1e-9)
    assert d.angular_distance_deg(0.0, 89.9, np.array([180.0]), np.array([89.9]))[0] == pytest.approx(0.2, abs=1e-9)


@pytest.mark.req("STL-030")
def test_the_fetch_window_is_wide_enough_that_the_local_cut_decides() -> None:
    lo, hi = d.fetch_parallax_window(CLUSTER, SETTINGS)
    assert (lo, hi) == pytest.approx((1.0, 3.0))
    wide = d.Cluster("Hyades", 66.7, 16.1, 10.35, 21.2, 1.55, 104.1, 14.8, -28.7, 10.8)
    assert d.fetch_parallax_window(wide, SETTINGS) == pytest.approx((21.2 - 7.75, 21.2 + 7.75))


@pytest.mark.req("STL-030")
def test_a_cluster_row_needs_every_parameter() -> None:
    row = {
        "Name": "X",
        "RA_ICRS": 1,
        "DE_ICRS": 2,
        "rt": 0.1,
        "Plx": 1,
        "s_Plx": 0.1,
        "pmRA": 1,
        "s_pmRA": 0.1,
        "pmDE": 1,
        "s_pmDE": 0.1,
    }
    assert d.Cluster.from_row(row).rt == 0.1
    with pytest.raises(ValueError, match="s_Plx"):
        d.Cluster.from_row({**row, "s_Plx": ""})


@pytest.mark.req("STL-030")
def test_the_seed_set_is_the_24_clusters_and_the_settings_are_a_draft_until_frozen() -> None:
    names = [c["hr23"] for c in SETTINGS["clusters"]]
    assert len(names) == 24 == len(set(names))
    assert {"Melotte_25", "NGC_2682", "Ruprecht_147", "Collinder_261"} <= set(names)
    assert SETTINGS["status"].startswith("DRAFT") and SETTINGS["freeze_before"] == "2026-12-02"
    assert len(d.settings_sha256()) == 64


@pytest.mark.req("STL-031")
def test_a_release_that_moves_every_parallax_moves_the_cluster_by_that_much() -> None:
    a = stars(n_field=0)
    b = {k: v.copy() for k, v in a.items()}
    b["parallax"] = b["parallax"] + 0.02
    row = d.compare(CLUSTER, a, b, None, SETTINGS)
    assert row["delta_parallax_mas"] == pytest.approx(0.02, abs=1e-9)
    assert row["delta_distance_pc"] < 0
    assert row["lost"] == row["new"] == row["unmatched"] == 0 and row["kept"] == row["earlier"]["n"]


@pytest.mark.req("STL-031")
def test_members_are_counted_kept_lost_unmatched_and_new_through_the_match() -> None:
    a = stars(n_members=10, n_field=0)
    b = {k: v.copy() for k, v in a.items()}
    b["source_id"] = b["source_id"] + 1000
    b["pmra"][0] = 10.0  # no longer moves with the cluster
    counterpart = {int(i): int(i) + 1000 for i in a["source_id"][1:9]}  # star 10 has no counterpart
    extra = {k: v[:1].copy() for k, v in b.items()}
    extra["source_id"][:] = 99_999
    extra["pmra"][:] = -5.0
    later = {k: np.concatenate([b[k], extra[k]]) for k in b}
    row = d.compare(CLUSTER, a, later, counterpart, SETTINGS)
    assert row["earlier"]["n"] == 10
    assert row["unmatched"] == 2  # star 1 (not in the map) and star 10
    assert row["kept"] == 8 and row["lost"] == 0
    # star 10's later twin (1010) and the extra star are members no earlier member maps to;
    # star 1's twin (1001) moves away, so it is not a member at all
    assert row["new"] == 2


@pytest.mark.req("STL-031")
def test_a_member_that_leaves_the_selection_is_lost() -> None:
    a = stars(n_members=5, n_field=0)
    b = {k: v.copy() for k, v in a.items()}
    b["parallax"] = b["parallax"].copy()
    b["parallax"][0] = 2.6
    row = d.compare(CLUSTER, a, b, None, SETTINGS)
    assert row["lost"] == 1 and row["kept"] == 4 and row["delta_n"] == -1


@pytest.mark.req("STL-031")
def test_the_weighted_mean_and_an_empty_cluster() -> None:
    assert d.weighted_mean(np.array([1.0, 3.0]), np.array([1.0, 1.0])) == pytest.approx((2.0, 1 / math.sqrt(2)))
    empty = d.describe(stars(n_members=0, n_field=0), np.zeros(0, dtype=bool))
    assert empty["n"] == 0 and math.isnan(empty["parallax_mas"]) and math.isnan(empty["distance_pc"])


@pytest.mark.req("STL-031")
def test_agreement_with_a_published_member_list() -> None:
    s = stars(n_members=4, n_field=0)
    out = d.agreement_with_catalogue(s, np.array([True, True, True, False]), [1, 2, 7, 8])
    assert out == {
        "selected": 3,
        "catalogue": 4,
        "both": 2,
        "catalogue_found_share": 0.5,
        "selected_in_catalogue_share": 0.6667,
    }
    assert d.flat({"a": {"b": 1}, "c": 2}) == {"a_b": 1, "c": 2}

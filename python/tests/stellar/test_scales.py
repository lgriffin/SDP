"""T1/T8: the sweep's distance scales (STL-012). The shells meet end to end, each candidate and star
is kept by one shell only, and a wide cluster 120 pc away that 2-degree tiles cut into fragments is
found whole by the near shell's 12-degree tiles."""

from __future__ import annotations

import math
from functools import cache
from typing import Any

import numpy as np
import pyarrow as pa
import pytest

from sdp.adapters.bootstrap import clusterers
from sdp_stellar import sweep
from sdp_stellar.quality import column
from sdp_stellar.synthetic import SCHEMA, _rows

from .test_sweep import settings

pytestmark = [pytest.mark.tier("T1"), pytest.mark.req("STL-012")]

RA0, DEC0 = 66.0, 15.0
WIDE = "synthetic:wide"


@cache
def wide_cluster_sky(seed: int = 3, stars: int = 12000, members: int = 150, sigma_deg: float = 1.6) -> pa.Table:
    """A 24 x 28 degree patch: field stars near (2.5-9 mas) and far, and a 150-star cluster at 120 pc
    whose members spread 1.6 degrees (3.4 pc) from its centre, so it spans several 2-degree tiles."""
    rng = np.random.default_rng(seed)
    dec = rng.uniform(DEC0 - 12, DEC0 + 12, stars)
    ra = (RA0 + rng.uniform(-14, 14, stars) / np.cos(np.radians(dec))) % 360
    plx = np.concatenate([rng.uniform(2.5, 9, stars // 3), rng.lognormal(np.log(0.9), 0.5, stars - stars // 3)])
    g = rng.uniform(8, 18, stars)
    pm = (rng.normal(0, 25, stars), rng.normal(0, 25, stars))
    field = _rows(rng, "synthetic:f", ra, dec, *pm, plx, g, rng.uniform(0.4, 2.6, stars))
    cdec = DEC0 + rng.normal(0, sigma_deg, members)
    cra = (RA0 + rng.normal(0, sigma_deg, members) / math.cos(math.radians(DEC0))) % 360
    abs_g = rng.uniform(0.5, 8, members)
    colour = 0.35 + 0.28 * (abs_g - 0.5) + rng.normal(0, 0.03, members)
    cluster = _rows(
        rng,
        WIDE,
        cra,
        cdec,
        30 + rng.normal(0, 0.6, members),
        -25 + rng.normal(0, 0.6, members),
        np.full(members, 1000 / 120),
        abs_g + 5 * np.log10(12),
        colour,
    )
    return pa.Table.from_pylist(field + cluster, schema=SCHEMA)


def members_found(scale: sweep.Scale, st: dict[str, Any]) -> list[int]:
    """Planted members in each candidate holding more than ten of them, over the tiles near the cluster."""
    sky = wide_cluster_sky()
    ra, dec = sweep.ra_dec(sky)
    found = []
    for band in range(sweep.bands(scale.tile_deg)):
        lo = -90 + band * scale.tile_deg
        if not (lo < DEC0 + 8 and lo + scale.tile_deg > DEC0 - 8):
            continue
        for i in range(sweep.tiles_in_band(band, scale.tile_deg)):
            t = sweep.tile(band, i, scale.tile_deg, scale.name)
            if abs(((t.ra_deg - RA0 + 180) % 360) - 180) > 9:
                continue
            rows = sky.filter(pa.array(sweep.neighbourhood(t, scale.margin_deg, ra, dec)))
            result = sweep.sweep_tile(rows, t, st, clusterers(), [], 2026, scale)
            for c in result["candidates"]:
                k = sum(1 for key in c["member_keys"] if key.startswith(WIDE))
                if k > 10:
                    found.append(k)
    return found


@pytest.mark.tier("T8")
def test_a_wide_nearby_cluster_is_found_whole_by_the_near_shell_and_cut_up_by_two_degree_tiles() -> None:
    st = settings()
    near = sweep.scales(st)[0]
    assert (near.name, near.tile_deg) == ("near", 12.0)
    whole = members_found(near, st)
    assert len(whole) == 1 and whole[0] >= 0.75 * 150
    legacy = settings(scales=None)
    pieces = members_found(sweep.scales(legacy)[0], legacy)
    assert len(pieces) >= 4 and max(pieces) < 0.5 * 150


def test_the_shells_meet_end_to_end_nearest_first() -> None:
    near, mid, far = sweep.scales(sweep.load_settings())
    assert (near.parallax_min, near.parallax_max) == (4.0, math.inf)
    assert (mid.parallax_min, mid.parallax_max) == (1.0, 4.0)
    assert (far.parallax_min, far.parallax_max) == (-math.inf, 1.0)
    parallax = np.array([0.2, 0.999, 1.0, 3.999, 4.0, 30.0])
    owners = np.vstack([s.owns(parallax) for s in (near, mid, far)])
    assert owners.sum(axis=0).tolist() == [1] * parallax.size  # every parallax in exactly one shell
    assert mid.window == pytest.approx((0.85, 4.6))
    assert near.window[0] == pytest.approx(3.4) and near.window[1] == math.inf


def test_without_scales_the_sweep_is_the_single_legacy_scale() -> None:
    (only,) = sweep.scales(settings(scales=None))
    assert (only.name, only.tile_deg, only.margin_deg) == ("", 2.0, 0.5)
    assert sweep.tile(57, 3, 2.0).tile_id == "b057-t003"
    assert sweep.tile(9, 3, 12.0, "near").tile_id == "near-b009-t003"
    assert bool(only.owns(np.array([0.01]))[0]) and only.window == (-math.inf, math.inf)


def _scale(**changes: Any) -> dict[str, Any]:
    return {
        "name": "near",
        "parallax_min_mas": 4.0,
        "parallax_max_mas": None,
        "parallax_rim": 0.15,
        "tile_deg": 12.0,
        "margin_deg": 3.0,
        **changes,
    }


def _far(**changes: Any) -> dict[str, Any]:
    far = {"name": "far", "parallax_min_mas": None, "parallax_max_mas": 4.0, "tile_deg": 2.0, "margin_deg": 0.5}
    return _scale(**{**far, **changes})


@pytest.mark.parametrize(
    ("doc", "message"),
    [
        ([], "non-empty"),
        ([_scale(name="Near"), _far()], "lower-case"),
        ([_scale(), _far(name="near")], "unique"),
        ([_scale(tile_deg=7.0), _far()], "does not divide"),
        ([_scale(parallax_rim=1.0), _far()], "below 1"),
        ([_scale(margin_deg=-1.0), _far()], "at least 0"),
        ([_scale(), _far(parallax_max_mas=3.0)], "meet at one positive parallax"),
        ([_far(), _scale()], "nearest scale needs no upper parallax"),
        ([_scale(parallax_max_mas=40.0), _far()], "nearest scale needs no upper parallax"),
        ([_scale(parallax_min_mas=True), _far()], "parallax of at least 0"),
    ],
)
def test_malformed_scales_are_refused(doc: list[dict[str, Any]], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        sweep.scales({"scales": doc})


def _star(parallax: float, ra: float = 66.5, dec: float = 15.5) -> dict[str, Any]:
    return {"ra_deg": ra, "dec_deg": dec, "parallax_mas": parallax}


def test_a_candidate_is_kept_only_by_the_shell_that_owns_its_mean_parallax() -> None:
    near, mid, _ = sweep.scales(sweep.load_settings())
    t_near = sweep.tile(8, 5, 12.0, "near")
    t_mid = next(
        t
        for t in (sweep.tile(26, i, 4.0, "mid") for i in range(sweep.tiles_in_band(26, 4.0)))
        if t.contains(66.5, 15.5)
    )
    candidates: list[dict[str, Any]] = [{"centre": _star(4.2)}, {"centre": _star(3.9)}, {"centre": None}]
    assert sweep._owned(candidates, t_near, near) == [candidates[0]]
    assert sweep._owned(candidates, t_mid, mid) == [candidates[1]]


def test_each_star_counts_in_one_shell_only() -> None:
    parallaxes = np.array([0.3, 0.9, 1.0, 2.0, 3.99, 4.0, 12.0])
    stars = pa.table(
        {
            "ra_deg": np.full(parallaxes.size, 66.5),
            "dec_deg": np.full(parallaxes.size, 15.5),
            "parallax_mas": parallaxes,
        }
    )
    counted = []
    for scale in sweep.scales(sweep.load_settings()):
        band = int(sweep.band_of(np.array([15.5]), scale.tile_deg)[0])
        t = next(
            t
            for t in (
                sweep.tile(band, i, scale.tile_deg, scale.name)
                for i in range(sweep.tiles_in_band(band, scale.tile_deg))
            )
            if t.contains(66.5, 15.5)
        )
        counted.append(sweep._owned_stars(stars, t, scale))
    assert counted == [2, 3, 2] and sum(counted) == parallaxes.size


def test_a_tile_of_another_scale_is_refused() -> None:
    near, _, _ = sweep.scales(sweep.load_settings())
    with pytest.raises(ValueError, match="not a tile of scale near"):
        sweep.sweep_tile(
            wide_cluster_sky().slice(0, 0), sweep.tile(26, 0, 4.0, "mid"), sweep.load_settings(), {}, [], 1, near
        )


def test_a_shell_searches_only_its_parallax_window() -> None:
    from sdp_stellar.quality import QualityCuts

    near = sweep.scales(sweep.load_settings())[0]
    t = sweep.tile(8, 5, 12.0, "near")
    sky = wide_cluster_sky()
    ra, dec = sweep.ra_dec(sky)
    rows = sky.filter(pa.array(sweep.neighbourhood(t, near.margin_deg, ra, dec)))
    st = {**sweep.load_settings(), "max_tile_stars": 1}
    result = sweep.sweep_tile(rows, t, st, clusterers(), [], 1, near)
    kept, _ = QualityCuts.from_parameters(st["quality_cuts"]).apply(rows)
    expected = int(np.count_nonzero(column(kept, "parallax_mas") >= 4.0 * 0.85))
    assert 0 < expected < kept.num_rows
    assert result["skipped"] == f"{expected} stars pass the cuts, more than max_tile_stars 1"

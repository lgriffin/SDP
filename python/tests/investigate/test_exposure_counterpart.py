"""The exposure-counterpart test (INT-024): a light curve's exposures, each where the archive saw the
source at that visit, reconciled with the field's Gaia stars by both association methods."""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Any

import numpy as np
import pytest
from scipy import stats

from sdp.adapters.association import BayesFactorAssociation, LikelihoodRatioAssociation
from sdp.kernel.domain.association import (
    Association,
    AssociationGraph,
    AssociationPolicy,
    CatalogueSource,
    Detection,
    Motion,
    ObjectHypothesis,
    PositionCovariance,
)
from sdp.kernel.domain.ids import ArtifactId
from sdp.kernel.domain.investigation import (
    EvidenceBundle,
    IdentificationMethod,
    IdentifiedObject,
    ProductEvidence,
    ProductRef,
)
from sdp.kernel.science.association import propagate
from sdp.kernel.science.investigation import PRODUCTS_SERVICE
from sdp.kernel.science.plan import Backend, TaskContext
from sdp.kernel.science.products import EXPOSURE_COLUMNS, lightcurve_csv, parse_exposures, parse_lightcurve
from sdp_investigate import ExposureCounterpart, registered_tests
from sdp_investigate.base import DEFAULT_POLICY
from sdp_investigate.exposures import MIN_MATCHED, chosen_option, exposure_records, mean_offset_f, nearby, usable
from sdp_investigate.imaging import MISSING_STATISTIC

pytestmark = [pytest.mark.tier("T1"), pytest.mark.req("INT-024")]

BEAM = 6.1
CENTRE = (56.53, 23.35)
MJD = np.linspace(56700.0, 60300.0, 60)


def ident(n: int) -> ArtifactId:
    return ArtifactId(f"sha256:{n:064x}")


def gaia_row(
    key: str, east: float = 0.0, north: float = 0.0, pm: tuple[float, float] = (20.0, -45.0)
) -> dict[str, Any]:
    """A Gaia star ``east`` and ``north`` arcsec from the centre at 2016.0."""
    return {
        "source_key": key,
        "release_label": "gaia:DR3",
        "ra_deg": CENTRE[0] + east / 3600.0 / math.cos(math.radians(CENTRE[1])),
        "dec_deg": CENTRE[1] + north / 3600.0,
        "epoch_jyear": 2016.0,
        "pm_ra_cosdec_masyr": pm[0],
        "pm_dec_masyr": pm[1],
        "parallax_mas": 7.0,
    }


def anchor_of(row: dict[str, Any]) -> CatalogueSource:
    return CatalogueSource(
        row["source_key"],
        "gaia",
        "gaia:DR3",
        row["source_key"],
        row["ra_deg"],
        row["dec_deg"],
        PositionCovariance(0.002, 0.002),
        2016.0,
        Motion(row["pm_ra_cosdec_masyr"], row["pm_dec_masyr"], 7.0),
    )


def exposures_at(
    rows: list[dict[str, Any]],
    weights: list[float],
    rng: np.random.Generator,
    *,
    offset: tuple[float, float] = (0.0, 0.0),
    sigma: float = 0.3,
    mjd: np.ndarray = MJD,
    positions: bool = True,
) -> bytes:
    """A light curve whose exposures sit at the stars' light-weighted centroid at each epoch, plus
    ``offset`` (arcsec east, north) and Gaussian scatter ``sigma``."""
    ra, dec = [], []
    for t in mjd:
        epoch = 2000.0 + (t - 51544.5) / 365.25
        moved = [propagate(anchor_of(r), epoch) for r in rows]
        r0 = sum(w * m.ra_deg for w, m in zip(weights, moved, strict=True) if m is not None)
        d0 = sum(w * m.dec_deg for w, m in zip(weights, moved, strict=True) if m is not None)
        east, north = offset[0] + rng.normal(0.0, sigma), offset[1] + rng.normal(0.0, sigma)
        dec.append(d0 + north / 3600.0)
        ra.append(r0 + east / 3600.0 / math.cos(math.radians(d0)))
    n = len(mjd)
    columns = (np.array(ra), np.array(dec), np.full(n, sigma), np.full(n, sigma), np.full(n, BEAM))
    return lightcurve_csv(mjd, np.full(n, 10.0), np.full(n, 0.02), columns if positions else None)


class Field:
    """Bundles of Gaia stars, some with a light curve, and the bytes the test may read."""

    def __init__(self) -> None:
        self.bundles: list[EvidenceBundle] = []
        self.products: dict[str, bytes] = {}

    def add(self, row: dict[str, Any], curves: tuple[bytes, ...] = ()) -> None:
        n = len(self.bundles) * 10
        products = []
        for i, data in enumerate(curves):
            artifact = ident(n + i + 1)
            self.products[artifact.value] = data
            ref = ProductRef("irsa-wise", f"WISE {row['source_key']}/{i}", f"https://irsa/{row['source_key']}/{i}")
            products.append(ProductEvidence(ref, "lightcurve", artifact, ref.uri))
        key = row["source_key"]
        self.bundles.append(
            EvidenceBundle(
                IdentifiedObject(
                    key, IdentificationMethod.CATALOGUE, row["ra_deg"], row["dec_deg"], (ident(n),), gaia_source_key=key
                ),
                {"gaia": (row,)},
                {"gaia": ident(n)},
                products=tuple(products),
            )
        )

    def context(self) -> TaskContext:
        services = {
            PRODUCTS_SERVICE: self.products,
            "associators": (BayesFactorAssociation(), LikelihoodRatioAssociation()),
        }
        return TaskContext("t", 1, DEFAULT_POLICY, Backend.CPU, services)

    def run(self, **thresholds: Any) -> dict[str, Any]:
        return self.run_bundles(self.bundles, **thresholds)

    def run_bundles(self, bundles: list[EvidenceBundle], **thresholds: Any) -> dict[str, Any]:
        results = ExposureCounterpart().run(bundles, thresholds, self.context())
        return {str(r.subject.object_key): r for r in results}


def test_the_test_is_installed_and_reads_gaia_and_light_curves() -> None:
    test = next(t for t in registered_tests() if t.name == "exposure-counterpart")
    assert isinstance(test, ExposureCounterpart) and test.version == "1.0.0"
    assert test.inputs == ("catalogue:gaia", "product:lightcurve") and test.needs_products == ("lightcurve",)


def test_exposures_on_their_star_are_not_flagged_and_both_methods_agree() -> None:
    rng = np.random.default_rng(1)
    field = Field()
    star = gaia_row("gaia:1")
    field.add(star, (exposures_at([star], [1.0], rng),))
    field.add(gaia_row("gaia:2", east=40.0), ())
    [result] = field.run().values()
    d = result.details
    assert result.subject.object_key == "gaia:1" and result.p_value is not None and result.p_value > 0.05
    assert d["counterpart_found"] and d["exposures"] == d["matched"] == 60 and d["roles"] == {"alone": 60}
    assert d["other_methods"] == {"likelihood-ratio": 1.0} and d["method"] == "bayes-factor"
    assert d["blend_of"] == [] and d["search_radius_arcsec"] == BEAM and len(d["graph_ids"]) == 2
    assert d["products"] == ["irsa-wise/WISE gaia:1/0"]
    assert result.effect_size == pytest.approx(math.hypot(*d["mean_offset_arcsec"]))
    assert all(0.2 < s < 0.4 for s in d["scatter_arcsec"])


def test_exposures_displaced_from_their_star_are_flagged_unless_the_archive_is_that_uncertain() -> None:
    rng = np.random.default_rng(2)
    field = Field()
    star = gaia_row("gaia:1")
    field.add(star, (exposures_at([star], [1.0], rng, offset=(0.4, -0.3)),))
    result = field.run()["gaia:1"]
    assert result.p_value is not None and result.p_value < 1e-6
    assert result.details["mean_offset_arcsec"] == pytest.approx([0.4, -0.3], abs=0.1)
    assert result.effect_size == pytest.approx(0.5, abs=0.1)
    floor = field.run(systematic_arcsec=1.0)["gaia:1"]
    assert floor.p_value is not None and floor.p_value > 0.5, "a 1-arcsec systematic swallows a 0.5-arcsec offset"


def test_a_beam_holding_two_stars_is_measured_against_the_blend_wherever_the_light_falls() -> None:
    rng = np.random.default_rng(3)
    field = Field()
    bright, faint = gaia_row("gaia:1"), gaia_row("gaia:2", east=3.5, north=-3.0, pm=(2.0, -6.0))
    # the bright star carries 85% of the light, so the centroid sits well off the members' mean
    field.add(bright, (exposures_at([bright, faint], [0.85, 0.15], rng),))
    field.add(faint, ())
    result = field.run()["gaia:1"]
    d = result.details
    assert d["roles"] == {"blend": 60} and d["blend_of"] == ["gaia:1", "gaia:2"]
    assert result.p_value is not None and result.p_value > 0.01, "the blend's covariance allows the flux split"
    assert d["other_methods"]["likelihood-ratio"] == 1.0


def test_a_star_whose_exposures_saw_nothing_there_has_no_counterpart() -> None:
    rng = np.random.default_rng(4)
    field = Field()
    star, elsewhere = gaia_row("gaia:1"), gaia_row("nowhere", east=20.0)
    field.add(star, (exposures_at([elsewhere], [1.0], rng),))
    result = field.run()["gaia:1"]
    assert result.p_value == 0.0 and result.statistic == MISSING_STATISTIC and result.effect_size is None
    d = result.details
    assert not d["counterpart_found"] and d["matched"] == 0 and d["roles"] == {"no-counterpart": 60}
    assert "mean_offset_arcsec" not in d and "scatter_arcsec" not in d


def test_exposures_of_a_neighbour_beyond_the_beam_are_not_the_star() -> None:
    rng = np.random.default_rng(5)
    field = Field()
    star, neighbour = gaia_row("gaia:1"), gaia_row("gaia:2", east=8.0, pm=(0.0, 0.0))
    field.add(star, (exposures_at([neighbour], [1.0], rng),))
    field.add(neighbour, ())
    result = field.run(search_radius_arcsec=10.0)["gaia:1"]
    assert result.p_value == 0.0 and result.details["roles"] == {"other-star": 60}
    assert result.details["search_radius_arcsec"] == 10.0


def test_half_the_exposures_must_be_the_star() -> None:
    rng = np.random.default_rng(6)
    star, elsewhere = gaia_row("gaia:1"), gaia_row("nowhere", east=20.0)
    on = exposures_at([star], [1.0], rng, mjd=MJD[:30])
    off = exposures_at([elsewhere], [1.0], rng, mjd=MJD[30:])
    for fraction, found in ((0.5, True), (0.51, False)):
        field = Field()
        field.add(star, (on, off))
        result = field.run(min_matched_fraction=fraction)["gaia:1"]
        assert result.details["exposures"] == 60 and result.details["matched"] == 30
        assert result.details["counterpart_found"] is found
    assert result.details["products"] == ["irsa-wise/WISE gaia:1/0", "irsa-wise/WISE gaia:1/1"]


def test_three_matched_exposures_are_enough_and_two_are_not() -> None:
    rng = np.random.default_rng(7)
    star, elsewhere = gaia_row("gaia:1"), gaia_row("nowhere", east=20.0)
    for matched in (MIN_MATCHED, MIN_MATCHED - 1):
        field = Field()
        field.add(
            star,
            (
                exposures_at([star], [1.0], rng, mjd=MJD[:matched]),
                exposures_at([elsewhere], [1.0], rng, mjd=MJD[10:12]),
            ),
        )
        result = field.run(min_matched_fraction=0.0, min_exposures=2)["gaia:1"]
        assert result.details["counterpart_found"] is (matched == MIN_MATCHED)


def test_light_curves_without_positions_or_with_too_few_exposures_are_left_out() -> None:
    rng = np.random.default_rng(8)
    field = Field()
    plain, few, enough = gaia_row("gaia:1"), gaia_row("gaia:2", east=60.0), gaia_row("gaia:3", east=120.0)
    field.add(plain, (exposures_at([plain], [1.0], rng, positions=False),))
    field.add(few, (exposures_at([few], [1.0], rng, mjd=MJD[:4]),))
    field.add(enough, (exposures_at([enough], [1.0], rng, mjd=MJD[:5]),))
    assert set(field.run()) == {"gaia:3"}, "five exposures by default"
    assert set(field.run(min_exposures=4)) == {"gaia:2", "gaia:3"}


def test_a_star_without_gaia_coordinates_or_motion_far_from_gaia_epoch_is_left_out() -> None:
    rng = np.random.default_rng(9)
    field = Field()
    star = gaia_row("gaia:1")
    curve = exposures_at([star], [1.0], rng)
    field.add({**star, "ra_deg": None}, (curve,))
    still = {**gaia_row("gaia:2", east=60.0), "pm_ra_cosdec_masyr": None}
    field.add(still, (exposures_at([gaia_row("x", east=60.0, pm=(0.0, 0.0))], [1.0], rng),))
    assert field.run() == {}, "no motion: Gaia's 2016 position cannot be compared a decade away"


def test_the_null_field_draws_f_for_every_star_and_rarely_flags_one() -> None:
    rng = np.random.default_rng(10)
    field = Field()
    for i in range(6):
        row = gaia_row(f"gaia:{i}", east=30.0 * i)
        field.add(row, (exposures_at([row], [1.0], rng),))
    elsewhere = gaia_row("nowhere", east=300.0)
    field.add(gaia_row("gaia:lost", east=500.0), (exposures_at([elsewhere], [1.0], rng),))
    test = ExposureCounterpart()
    context = field.context()
    features = test.features(field.bundles, {}, context)
    assert features.arrays["n"] == [60] * 6 + [MIN_MATCHED]
    null = test.null_features(features, {}, np.random.default_rng(0))
    assert (
        all(f is not None and f >= 0 for f in null.arrays["f"]) and null.arrays["details"] == features.arrays["details"]
    )
    result = test.null(field.bundles, {}, context, trials=200, alpha=0.01)
    assert result.examined == 7 * 200 and result.false_positives <= 30


def test_chosen_option_is_the_most_probable_option_ties_broken_by_members() -> None:
    policy = AssociationPolicy("p", "1.0.0", 2016.0, 1.0)

    def assoc(hypothesis: str, posterior: float, **evidence: Any) -> Association:
        return Association("r", hypothesis, "m", "1.0.0", 0.5, 1.0, posterior, None, (), evidence)

    def graph(*associations: Association) -> AssociationGraph:
        hypotheses = {a.hypothesis_id for a in associations}
        return AssociationGraph(
            policy,
            "m",
            "1.0.0",
            (Detection("r", "p", "x", 1.0, 1.0, PositionCovariance(0.1, 0.1), 2016.0, 1.0, "i"),),
            tuple(ObjectHypothesis(h, ("r",)) for h in sorted(hypotheses)),
            associations,
            0.0,
        )

    blend = {"role": "blend", "blend_of": ["b", "a"], "option_posterior": 0.6}
    assert chosen_option(graph(assoc("obj:a", 0.3, **blend), assoc("obj:c", 0.4, role="counterpart")), "r") == (
        "a",
        "b",
    )
    assert (
        chosen_option(graph(assoc("obj:c", 0.5, role="counterpart"), assoc("obj:r", 0.5, role="no-counterpart")), "r")
        == ()
    )
    assert chosen_option(
        graph(assoc("obj:c", 0.5, role="counterpart"), assoc("obj:b", 0.5, role="counterpart")), "r"
    ) == ("b",)
    assert chosen_option(graph(), "r") == ()


def test_the_mean_offset_statistic_is_hotelling_t2_as_f() -> None:
    offsets = np.array([[0.1, 0.0], [0.3, 0.2], [0.2, -0.1], [0.0, 0.1], [0.4, 0.3]])
    option = np.array([np.diag([0.01, 0.04])] * 5)
    f, mean, scatter = mean_offset_f(offsets, option, systematic_arcsec=0.1)
    v = np.cov(offsets.T, ddof=1) / 5 + np.diag([0.01, 0.04]) + 0.01 * np.eye(2)
    t2 = mean @ np.linalg.solve(v, mean)
    assert f == pytest.approx(t2 * 3 / 8) and mean == pytest.approx([0.2, 0.1])
    assert scatter == pytest.approx(np.cov(offsets.T, ddof=1))
    assert mean_offset_f(offsets, option)[0] > f, "the systematic floor only ever lowers the statistic"


def test_the_p_value_is_the_f_survival_with_n_minus_2_dof() -> None:
    rng = np.random.default_rng(11)
    field = Field()
    star = gaia_row("gaia:1")
    field.add(star, (exposures_at([star], [1.0], rng, offset=(0.1, 0.0)),))
    result = field.run()["gaia:1"]
    assert result.p_value == pytest.approx(stats.f.sf(result.statistic, 2, 58))


def test_the_canonical_light_curve_carries_exposure_positions_when_given() -> None:
    mjd = np.array([2.0, 1.0])
    columns = tuple(np.array([a, b]) for a, b in ((10.0, 11.0), (20.0, 21.0), (0.1, 0.2), (0.3, 0.4), (6.1, 6.1)))
    data = lightcurve_csv(mjd, np.array([9.0, 8.0]), np.array([0.01, 0.02]), columns)
    assert data.splitlines()[0] == b"mjd,mag,mag_err," + ",".join(EXPOSURE_COLUMNS).encode()
    exposures = parse_exposures(b"# stamped\n" + data)
    assert exposures is not None
    assert list(exposures.mjd) == [1.0, 2.0] and list(exposures.ra_deg) == [11.0, 10.0]
    assert list(exposures.dec_err_arcsec) == [0.4, 0.3] and list(exposures.beam_arcsec) == [6.1, 6.1]
    assert list(parse_lightcurve(data).mag) == [8.0, 9.0], "a reader of mjd, mag, mag_err is unaffected"
    assert parse_exposures(lightcurve_csv(mjd, mjd, mjd)) is None
    with pytest.raises(ValueError, match=r"^exposures are the columns \('ra_deg'"):
        lightcurve_csv(mjd, mjd, mjd, columns[:4])


def test_a_neighbour_is_offered_if_it_is_near_at_the_first_middle_or_last_exposure() -> None:
    star = anchor_of(gaia_row("gaia:1", pm=(0.0, 0.0)))
    # 1.5 arcsec a year eastward, 15 arcsec west of the star in 2016: 2.6 arcsec off by 2024
    runner = anchor_of(gaia_row("gaia:2", east=-15.0, pm=(1500.0, 0.0)))
    anchors = {"gaia:1": star, "gaia:2": runner}
    assert nearby(star, anchors, (2016.0,), 10.0) == [star]
    assert nearby(star, anchors, (2014.0, 2016.0, 2024.0), 10.0) == [star, runner]
    still = anchor_of({**gaia_row("gaia:3"), "pm_ra_cosdec_masyr": 0.0})
    assert nearby(replace(still, motion=None), anchors, (2024.0,), 10.0) == [], "Gaia's epoch is too far away"


def test_a_product_listed_twice_is_read_once_and_bad_positions_are_counted_not_raised() -> None:
    rng = np.random.default_rng(12)
    star = gaia_row("gaia:1")
    rows = exposures_at([star], [1.0], rng).decode().splitlines()
    # column 3 is ra_deg, 4 dec_deg, 1 mag: off the sky, no position at all, an empty cell, and a
    # visit with no magnitude, which is not an epoch of the light curve at all
    for i, column, bad in ((1, 4, "95.0"), (2, 4, "nan"), (3, 3, "-1.0"), (4, 3, "360.0"), (5, 3, ""), (6, 1, "nan")):
        cells = rows[i].split(",")
        cells[column] = bad
        rows[i] = ",".join(cells)
    field = Field()
    field.add(star, ("\n".join(rows).encode(),))
    bundle = field.bundles[0]
    first = bundle.products[0]
    elsewhere = replace(first, product=replace(first.product, archive="other-archive"))
    listed = replace(bundle, products=(first, first, elsewhere))
    dets, unusable = exposure_records(listed, field.context())
    assert unusable == 2 * 5 and len(dets) == 2 * 54, "the other archive's product is its own"
    assert len({d.record_id for d in dets}) == len(dets)
    assert {d.instrument for d in dets} == {"irsa-wise", "other-archive"}
    both = field.run_bundles([listed])["gaia:1"].details["products"]
    assert both == [f"irsa-wise/{first.product.product_id}", f"other-archive/{first.product.product_id}"]
    assert dets[0].record_id == f"irsa-wise/{first.product.product_id}#0005", "the first five epochs have no position"
    for sigma, beam in ((0.0, BEAM), (0.3, 0.0), (math.inf, BEAM), (0.3, math.inf)):
        assert not usable(10.0, 20.0, sigma, 0.3, beam)
        assert not usable(10.0, 20.0, 0.3, sigma, beam)
    assert usable(0.0, -90.0, 0.3, 0.3, BEAM) and not usable(360.0, 0.0, 0.3, 0.3, BEAM)
    result = field.run()["gaia:1"]
    assert result.details["exposures"] == 54 and result.details["without_position"] == 5


def test_epochs_without_a_position_count_against_the_matched_fraction() -> None:
    rng = np.random.default_rng(13)
    star = gaia_row("gaia:1")
    rows = exposures_at([star], [1.0], rng, mjd=MJD[:10]).decode().splitlines()
    for i in range(1, 6):
        cells = rows[i].split(",")
        cells[3] = "nan"
        rows[i] = ",".join(cells)
    field = Field()
    field.add(star, ("\n".join(rows).encode(),))
    assert field.run(min_matched_fraction=0.5)["gaia:1"].details["counterpart_found"]
    assert not field.run(min_matched_fraction=0.51)["gaia:1"].details["counterpart_found"]


def test_null_fields_draw_the_same_statistic_with_the_systematic_floor() -> None:
    rng = np.random.default_rng(14)
    field = Field()
    for i in range(5):
        row = gaia_row(f"gaia:{i}", east=30.0 * i)
        field.add(row, (exposures_at([row], [1.0], rng),))
    test = ExposureCounterpart()
    features = test.features(field.bundles, {"systematic_arcsec": 0.5}, field.context())
    assert all(s is not None and s[0].shape == (60, 2) for s in features.arrays["samples"])
    draws = [
        f
        for seed in range(100)
        for f in test.null_features(features, {"systematic_arcsec": 0.5}, np.random.default_rng(seed)).arrays["f"]
    ]
    # a 0.5-arcsec floor dwarfs a 0.04-arcsec scatter of the mean: the statistic is chi-squared over 2
    # with F's small-sample factor, never the far larger values F(2, 58) allows without the floor
    assert np.mean(draws) == pytest.approx(1.0 * 58 / 59, rel=0.15)
    assert np.mean([stats.f.sf(f, 2, 58) < 0.01 for f in draws]) < 0.02

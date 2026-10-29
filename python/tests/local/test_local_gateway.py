"""T3/T4: a local source's solved frames as an observation archive, and the my-frames profile run over
them (#71). Synthetic frames with planted stars at a known WCS; nothing is read from an archive."""

from __future__ import annotations

import io
import os
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import yaml

from sdp.adapters.association import BayesFactorAssociation, LikelihoodRatioAssociation
from sdp.adapters.bootstrap import local_image_gateways
from sdp.adapters.imaging.photutils_detector import PhotutilsDetector, read_image
from sdp.adapters.local.fits_header import read_header
from sdp.adapters.local.gateway import LocalFrameDialect, LocalImageGateway
from sdp.adapters.local.index import FrameIndex
from sdp.adapters.settings import Settings
from sdp.kernel.application.media import JSON, decode_artifact
from sdp.kernel.domain.investigation import (
    ArchiveRole,
    ObservationCriteria,
    ProductRef,
    Stage,
    StageState,
    TestProfile,
)
from sdp.kernel.ports.investigate import ImageFrame, InvestigateFieldCommand, ObservationTarget
from sdp.testing.investigate import CANONICAL_COLUMNS, gaia_archive, investigate_ports
from sdp.testing.wiring import build_in_memory_kernel
from sdp_investigate.imaging import ImageCounterpart

from .fits_files import fits_bytes
from .synthetic import LOCAL_DATE, LocalWorld, Truth, local_world, write_solution

pytestmark = pytest.mark.tier("T3")
PROFILE = Path(__file__).resolve().parents[3] / "profiles" / "my-frames.yaml"
LIGHT, STACK = "M31/lights/sub-001.fits", "M31/M31_stacked.fits"


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> LocalWorld:
    return local_world(tmp_path_factory.mktemp("local"))


def gateway(world: LocalWorld) -> LocalImageGateway:
    return LocalImageGateway(world.source, world.data)


def ref(world: LocalWorld, relative: str) -> ProductRef:
    return ProductRef("local:astro", relative, (world.source.path / relative).as_uri())


def criteria(**overrides: object) -> ObservationCriteria:
    return ObservationCriteria(**{"product_types": ("image",), "radius_arcsec": 60.0, **overrides})  # type: ignore[arg-type]


def sky_error_arcsec(truth: Truth, wcs: object, cut_xy: np.ndarray, raw_xy: np.ndarray) -> float:
    """The largest distance between where the cut's WCS and the truth put matching pixels."""
    ra, dec = wcs.all_pix2world(cut_xy[:, 0], cut_xy[:, 1], 1)  # type: ignore[attr-defined]
    true_ra, true_dec = truth.wcs.all_pix2world(raw_xy[:, 0], raw_xy[:, 1], 1)
    return float((np.hypot((ra - true_ra) * np.cos(np.radians(dec)), dec - true_dec) * 3600).max())


@pytest.mark.req("LOC-005")
def test_a_search_lists_the_dated_solved_frames_that_reach_the_target(world: LocalWorld) -> None:
    g = gateway(world)
    ra, dec = world.centre
    index = g.search(ObservationTarget("field", ra, dec), criteria())
    assert index.archive == "local:astro" and index.release_label == "local:astro"
    assert sorted((r.product.product_id, r.mission) for r in index.records) == [(STACK, "stack"), (LIGHT, "light")]
    assert all(
        r.product_type == "image" and r.epoch_mjd == pytest.approx(60932.917396, abs=1e-6) for r in index.records
    )
    # the kinds a profile asks for, and a combined stack alone at calibration level 3
    assert [
        r.product.product_id for r in g.search(ObservationTarget("f", ra, dec), criteria(missions=("light",))).records
    ] == [LIGHT]
    assert [
        r.product.product_id
        for r in g.search(ObservationTarget("f", ra, dec), criteria(min_calibration_level=3)).records
    ] == [STACK]
    # 2 degrees away no frame reaches
    assert g.search(ObservationTarget("far", ra, dec + 2.0), criteria()).records == ()


@pytest.mark.req("LOC-005")
def test_an_unsolved_or_calibration_frame_is_never_offered(tmp_path: Path) -> None:
    w = local_world(tmp_path, size=600)
    # a "solution" beside the dark is ignored: only light and stack frames are images of the sky
    write_solution(w.data, w.source, "M31/darks/dark-001.fits", w.light, 600, 600)
    (w.source.output_dir(w.data, "solutions") / (LIGHT + ".wcs.json")).unlink()
    found = LocalImageGateway(w.source, w.data).search(ObservationTarget("f", *w.centre), criteria())
    assert [r.product.product_id for r in found.records] == [STACK]
    # a stack replaced since it was solved has no solution until it is solved again
    stack = w.source.path / STACK
    stack.write_bytes(stack.read_bytes())
    os.utime(stack, ns=(1, 1))
    FrameIndex(w.source, w.data).update()
    assert LocalImageGateway(w.source, w.data).search(ObservationTarget("f", *w.centre), criteria()).records == ()


@pytest.mark.req("LOC-005")
@pytest.mark.parametrize(("relative", "binning"), [(LIGHT, 2), (STACK, 1)])
def test_a_cutout_carries_the_frames_solution_onto_its_own_pixels(
    world: LocalWorld, relative: str, binning: int
) -> None:
    g = gateway(world)
    truth = world.light if relative == LIGHT else world.stack
    ra, dec = world.centre
    got = g.cutout_product(ref(world, relative), ra, dec, 900.0)
    assert got.media_type == "application/fits" and got.release_label == "local:astro"
    assert got.product.product_id != relative and relative in got.product.product_id
    data, wcs = read_image(ImageFrame("cut", got.data, got.media_type))
    side = 900.0 / 1.65 / binning
    assert abs(data.shape[0] - side) < 4 and abs(data.shape[1] - side) < 4
    # the cut's pixels and the truth agree on the sky: pixels the same number of raw pixels from the
    # field centre in both, over a grid of the cut
    cut_xy = np.array([(x, y) for x in (1.0, data.shape[1] / 2, data.shape[1]) for y in (1.0, data.shape[0])])
    centre_px = wcs.all_world2pix([ra], [dec], 1)
    raw_centre = truth.wcs.all_world2pix([ra], [dec], 1)
    raw_xy = (cut_xy - [centre_px[0][0], centre_px[1][0]]) * binning + [raw_centre[0][0], raw_centre[1][0]]
    assert sky_error_arcsec(truth, wcs, cut_xy, raw_xy) < 0.05
    # and a bright planted star sits on the pixel the cut's WCS puts it
    stars = world.catalogue.to_pylist()[:5]
    for star in stars:
        x, y = (float(v[0]) - 1 for v in wcs.all_world2pix([star["ra_deg"]], [star["dec_deg"]], 1))
        ix, iy = round(x), round(y)
        patch = data[iy - 3 : iy + 4, ix - 3 : ix + 4]
        py, px = np.unravel_index(int(np.argmax(patch)), patch.shape)
        assert abs(px - 3) <= 1 and abs(py - 3) <= 1, star["source_key"]


@pytest.mark.req("LOC-005")
def test_a_cutout_off_the_frame_is_refused(world: LocalWorld) -> None:
    ra, dec = world.centre
    with pytest.raises(ValueError, match="is off"):
        gateway(world).cutout_product(ref(world, STACK), ra, dec + 3.0, 600.0)


@pytest.mark.req("LOC-005")
def test_the_dialect_curates_a_whole_solved_frame_and_leaves_the_rest(world: LocalWorld) -> None:
    g = gateway(world)
    dialect = LocalFrameDialect(g)
    whole = g.fetch(ref(world, LIGHT))
    assert whole.data == (world.source.path / LIGHT).read_bytes()
    curated = dialect.curate("image", whole)
    assert curated is not None and curated.media_type == "application/fits"
    data, wcs = read_image(ImageFrame("whole", curated.data, curated.media_type))
    assert data.shape == (900, 900)
    # binned pixel q is raw pixel 2q - 1/2 (1-based)
    cut_xy = np.array([[1.0, 1.0], [450.0, 300.0], [900.0, 900.0]])
    assert sky_error_arcsec(world.light, wcs, cut_xy, 2 * cut_xy - 0.5) < 0.05
    assert dialect.curate("lightcurve", whole) is None
    elsewhere = ref(world, "elsewhere.fits")
    assert dialect.curate("image", replace(whole, product=elsewhere, read_from=elsewhere.uri)) is None


def rebuilt_stack(w: LocalWorld) -> None:
    """The stack rewritten as some software saves it: an empty primary dated DATE_OBS, a 1-D extension,
    then the image; indexed and solved again."""
    path = w.source.path / STACK
    raw = path.read_bytes()
    head = (raw.index(b"END" + b" " * 77) // 2880 + 1) * 2880
    size = 600
    image = {
        "XTENSION": "IMAGE", "BITPIX": -32, "NAXIS": 3, "NAXIS1": size, "NAXIS2": size, "NAXIS3": 3,
        "PCOUNT": 0, "GCOUNT": 1,
    }  # fmt: skip
    one_d = {"XTENSION": "IMAGE", "BITPIX": 16, "NAXIS": 1, "NAXIS1": 8, "PCOUNT": 0, "GCOUNT": 1}
    primary = {"SIMPLE": True, "BITPIX": 8, "NAXIS": 0, "EXTEND": True, "OBJECT": "M31", "STACKCNT": 120}
    primary["DATE_OBS"] = LOCAL_DATE
    data = raw[head : head + size * size * 3 * 4]
    path.write_bytes(fits_bytes(primary) + fits_bytes(one_d, bytes(16)) + fits_bytes(image, data))
    FrameIndex(w.source, w.data).update()
    write_solution(w.data, w.source, STACK, w.stack, size, size)


@pytest.mark.req("LOC-005")
def test_a_frame_dated_date_obs_behind_other_extensions_is_still_cut_and_dated(tmp_path: Path) -> None:
    w = local_world(tmp_path, size=600)
    rebuilt_stack(w)
    g = LocalImageGateway(w.source, w.data)
    cut = g.cutout_product(ref(w, STACK), *w.centre, 600.0)
    whole = LocalFrameDialect(g).curate("image", g.fetch(ref(w, STACK)))
    assert whole is not None
    for body in (cut.data, whole.data):
        header = read_header(io.BytesIO(body))
        assert header["DATE-OBS"] == LOCAL_DATE and header["MJD-OBS"] == pytest.approx(60932.917396, abs=1e-6)
        data, _ = read_image(ImageFrame("x", body, "application/fits"))
        assert data.ndim == 2 and min(data.shape) > 300


@pytest.mark.req("LOC-005")
def test_a_frame_gone_or_linked_outside_the_source_is_skipped_not_fatal(tmp_path: Path) -> None:
    w = local_world(tmp_path, size=600)
    g = LocalImageGateway(w.source, w.data)
    outside = tmp_path / "elsewhere.fits"
    outside.write_bytes((w.source.path / LIGHT).read_bytes())
    (w.source.path / LIGHT).unlink()
    reads: list[Callable[[], object]] = [
        lambda: g.fetch(ref(w, LIGHT)),
        lambda: g.cutout_product(ref(w, LIGHT), *w.centre, 600.0),
    ]
    for read in reads:
        with pytest.raises(LookupError, match="could not be read"):
            read()
    (w.source.path / LIGHT).symlink_to(outside)
    for read in reads:
        with pytest.raises(LookupError, match="outside"):
            read()
    with pytest.raises(LookupError, match="not a solved frame"):
        g.fetch(ref(w, "../elsewhere.fits"))


@pytest.mark.req("LOC-005")
def test_bootstrap_offers_each_indexed_local_source(world: LocalWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings.from_env({"SDP_ARTIFACT_ROOT": str(world.data / "artifacts"), "SDP_DATABASE_URL": "x"})
    monkeypatch.delenv("SDP_DATA", raising=False)
    monkeypatch.delenv("SDP_DESKTOP_DATA", raising=False)
    monkeypatch.setenv("SDP_ARTIFACT_ROOT", str(world.data / "artifacts"))
    assert set(local_image_gateways(settings)) == {"local:astro"}
    monkeypatch.setenv("SDP_DATA", str(world.data.parent / "empty"))
    assert local_image_gateways(settings) == {}
    broken = world.data.parent / "broken"
    broken.mkdir(exist_ok=True)
    (broken / "local-sources.yaml").write_text("sources: [astro\n", "utf-8")
    monkeypatch.setenv("SDP_DATA", str(broken))
    assert local_image_gateways(settings) == {}, "a broken registry leaves the other archives running"


def my_frames_profile() -> TestProfile:
    """The shipped my-frames profile on the synthetic world: Gaia's rows are already canonical there."""
    profile = TestProfile.from_document(yaml.safe_load(PROFILE.read_text("utf-8")))
    identify = replace(
        profile.identify, source_id="synthetic-gaia", columns=CANONICAL_COLUMNS, quality_cuts=(), resolve=False
    )
    tests = tuple(replace(t, thresholds={**t.thresholds, "source_id": "synthetic-gaia"}) for t in profile.tests)
    return replace(profile, identify=identify, tests=tests)


@pytest.mark.tier("T4")
@pytest.mark.req("LOC-005")
def test_the_my_frames_profile_finds_the_one_gaia_star_the_frames_do_not_show(world: LocalWorld) -> None:
    profile = my_frames_profile()
    (local,) = [a for a in profile.archives if a.role is ArchiveRole.OBSERVATIONS]
    assert (local.source_id, local.missions, local.cutout_arcsec) == ("local:astro", ("stack",), 1800.0)
    g = gateway(world)
    ports = investigate_ports(ImageCounterpart(), catalogue=world.catalogue, resolve=False)
    ports.observation_gateways = {"local:astro": g}
    ports.product_dialects = {"local:astro": LocalFrameDialect(g)}
    ports.services = {
        "detector": PhotutilsDetector(),
        "associators": (BayesFactorAssociation(), LikelihoodRatioAssociation()),
    }
    k = build_in_memory_kernel(gateways={"synthetic-gaia": gaia_archive(world.catalogue)}, investigate=ports)
    assert k.kernel.investigate_field is not None
    from sdp.kernel.domain.archive import Cone
    from sdp.kernel.domain.investigation import Field

    field_ = Field(Cone(*world.centre, 0.12), epoch_jyear=2016.0, label="synthetic M31 frames")
    run = k.kernel.investigate_field.execute(InvestigateFieldCommand(field_, profile, seed=1))
    assert all(r.state is StageState.COMPLETED for r in run.investigation_set.stages.values())
    stage = run.investigation_set.stages[Stage.TEST].artifact_id
    assert stage is not None
    results = {r["subject"]["object_key"]: r for r in decode_artifact(k.store.get(stage), JSON)["results"]}
    assert len(results) == world.catalogue.num_rows >= 8
    flagged = {key for key, r in results.items() if r["p_value"] < 0.01}
    vanished = next(key for key, r in results.items() if world.vanished in str(r))
    assert flagged == {vanished}, "every other star is on the stack where Gaia puts it"
    # the frames were only read: the source folder holds just what the test wrote
    assert sorted(p.name for p in world.source.path.rglob("*") if p.is_file()) == [
        "M31_stacked.fits",
        "dark-001.fits",
        "sub-001.fits",
    ]

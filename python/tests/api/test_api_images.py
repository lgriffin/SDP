"""T4 for the image view of a field (INV-020): each image the set read with its envelope's checks and
the Gaia stars on it, the image's bytes, and its north-up picture, over the synthetic JWST slice."""

from __future__ import annotations

import struct
import zlib
from typing import Any

import numpy as np
import pytest
from starlette.testclient import TestClient
from tests.usecase.test_jwst_slice import run_slice, slice_kernel

from sdp.adapters.products.preview import binned, png, preview_png, stretch
from sdp.api.app import create_app
from sdp.kernel.application.field_images import chance_p, field_images, image_size
from sdp.kernel.application.media import JSON, decode_artifact
from sdp.kernel.domain.canonical import canonical_json
from sdp.kernel.domain.ids import ArtifactId
from sdp.kernel.domain.investigation import Stage
from sdp.testing.jwst import IMAGE_PIXELS, PAIR_A, PAIR_B, VANISHED, jwst_i2d
from sdp.testing.wiring import InMemoryKernel

pytestmark = pytest.mark.tier("T4")
IMAGES = "query S($id: ID!) { investigationSet(id: $id) { images } }"


@pytest.fixture(scope="module")
def world() -> tuple[InMemoryKernel, dict[str, Any]]:
    k = slice_kernel()
    _, run = run_slice(k)
    return k, run


def graphql(client: TestClient, query: str, **variables: Any) -> Any:
    response = client.post("/graphql", json={"query": query, "variables": variables})
    assert response.status_code == 200 and "errors" not in response.json(), response.text
    return response.json()["data"]


def decode_png(data: bytes) -> np.ndarray:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", data[16:24])
    body, at = b"", 8
    while at < len(data):
        (length,) = struct.unpack(">I", data[at : at + 4])
        kind = data[at + 4 : at + 8]
        if kind == b"IDAT":
            body += data[at + 8 : at + 8 + length]
        at += 12 + length
    rows = zlib.decompress(body)
    return np.frombuffer(rows, dtype=np.uint8).reshape(height, width + 1)[:, 1:]


@pytest.mark.req("INV-020")
def test_the_set_lists_its_image_with_every_gaia_star_flagged_matched_or_ambiguous(
    world: tuple[InMemoryKernel, dict[str, Any]],
) -> None:
    k, run = world
    client = TestClient(create_app(k))
    [image] = graphql(client, IMAGES, id=run["set"].set_id)["investigationSet"]["images"]
    assert image["product_id"].endswith("_i2d.fits") and image["archive"] == "synthetic-mast"
    assert image["checks"] is None and image["passed"] is None, "this archive has no normaliser"
    assert (image["width"], image["height"]) == (IMAGE_PIXELS, IMAGE_PIXELS)
    stars = {s["object_key"]: s for s in image["stars"]}
    assert set(stars) == set(run["results"])
    assert image["stars"][0]["object_key"] == VANISHED, "flagged stars come first"
    vanished = stars[VANISHED]
    assert vanished["flagged"] and vanished["counterpart_px"] is None and vanished["p_value"] == 0.0
    assert vanished["investigation_id"] is not None, "a flagged star links to its candidate"
    for key in (PAIR_A, PAIR_B):
        assert stars[key]["ambiguous"] and not stars[key]["flagged"]
        [blend] = stars[key]["counterparts"]
        assert blend["role"] == "blend"
    for key, star in stars.items():
        x, y = star["predicted_px"]
        assert 0 <= x < IMAGE_PIXELS and 0 <= y < IMAGE_PIXELS
        assert star["p_value"] == pytest.approx(run["results"][key]["p_value"])
        if key != VANISHED:
            assert not star["flagged"] and star["investigation_id"] is None


@pytest.mark.req("INV-020")
def test_the_envelopes_checks_come_with_the_image(world: tuple[InMemoryKernel, dict[str, Any]]) -> None:
    k, run = world
    s = run["set"]
    record = s.stages[Stage.RETRIEVE]
    assert record.artifact_id is not None
    retrieved = decode_artifact(k.store.get(record.artifact_id), JSON)
    report = {"passed": False, "checks": [{"field": "size_bytes", "passed": False, "detail": "6 bytes read"}]}
    envelope = {
        "metadata": {"instrument": "NIRCAM", "filter_or_grating": "F200W", "query": "long"},
        "validation": report,
    }
    envelope_id = k.store.put(canonical_json(envelope).encode(), JSON)
    for b in retrieved["bundles"]:
        for p in b["products"]:
            p["envelope_artifact_id"] = envelope_id.value
    record.artifact_id = k.store.put(canonical_json(retrieved).encode(), JSON)
    [image] = field_images(k.store, s)
    assert image["summary"] == {"instrument": "NIRCAM", "filter_or_grating": "F200W"}
    assert image["passed"] is False and image["checks"] == report["checks"]
    assert image["envelope_artifact_id"] == envelope_id.value
    assert all(star["investigation_id"] is None for star in image["stars"]), "no investigations were given"


@pytest.mark.req("INV-020")
def test_an_image_is_served_as_stored_and_drawn_north_up(world: tuple[InMemoryKernel, dict[str, Any]]) -> None:
    k, run = world
    client = TestClient(create_app(k))
    [image] = graphql(client, IMAGES, id=run["set"].set_id)["investigationSet"]["images"]
    raw = client.get(f"/artifacts/{image['artifact_id']}")
    assert raw.status_code == 200 and raw.content == k.store.get(ArtifactId(image["artifact_id"]))
    assert "immutable" in raw.headers["cache-control"]
    picture = client.get(f"/artifacts/{image['artifact_id']}/preview.png")
    assert picture.status_code == 200 and picture.headers["content-type"] == "image/png"
    grey = decode_png(picture.content)
    assert grey.shape == (IMAGE_PIXELS, IMAGE_PIXELS)
    # The brightest drawn star sits where the overlay puts a matched star, once y is flipped.
    star = next(s for s in image["stars"] if s["counterpart_px"])
    x, y = (round(v) for v in star["counterpart_px"])
    assert grey[IMAGE_PIXELS - 1 - y, x] > np.median(grey) + 40

    missing = "sha256:" + "0" * 64
    assert client.get(f"/artifacts/{missing}").status_code == 404
    assert client.get(f"/artifacts/{missing}/preview.png").status_code == 404
    assert client.get("/artifacts/not-an-id").status_code == 400
    envelope = k.store.put(b'{"a": 1}', JSON)
    refused = client.get(f"/artifacts/{envelope.value}/preview.png")
    assert refused.status_code == 415 and "not a readable FITS file" in refused.json()["error"]
    assert graphql(client, IMAGES, id="invset-missing")["investigationSet"] is None


def test_an_image_size_is_read_from_the_science_header() -> None:
    import io

    from astropy.io import fits

    def fits_bytes(*hdus: fits.ImageHDU | fits.PrimaryHDU) -> bytes:
        sink = io.BytesIO()
        fits.HDUList(list(hdus)).writeto(sink)
        return sink.getvalue()

    assert image_size(jwst_i2d()) == (IMAGE_PIXELS, IMAGE_PIXELS)
    primary = fits.PrimaryHDU(np.zeros((3, 5), dtype=np.float32))
    sci = fits.ImageHDU(np.zeros((7, 9), dtype=np.int16), name="SCI")
    assert image_size(fits_bytes(primary, sci)) == (9, 7), "SCI wins over an earlier image"
    assert image_size(fits_bytes(primary)) == (5, 3)
    assert image_size(fits_bytes(fits.PrimaryHDU(), fits.ImageHDU(np.zeros(4), name="ERR"))) is None
    assert image_size(b"short") is None


def test_the_picture_stretch_bins_and_png() -> None:
    assert chance_p(None) == 0.0 and chance_p(0.0) == 1.0 and chance_p(2.0) == pytest.approx(np.exp(-1))
    pixels = np.array([[np.nan, 0.0], [1.0, 100.0]])
    grey = stretch(pixels)
    assert grey[0, 0] == 0 and grey[1, 1] == 255 and 0 <= grey[0, 1] < grey[1, 0] < 255
    assert (stretch(np.full((2, 2), np.nan)) == 0).all()
    big = np.arange(36.0).reshape(6, 6)
    assert binned(big, 3).shape == (3, 3) and binned(big, 3)[0, 0] == pytest.approx(3.5)
    assert binned(big, 6) is big
    assert (decode_png(png(np.array([[1, 2], [3, 4]], dtype=np.uint8))) == [[1, 2], [3, 4]]).all()
    drawn = decode_png(preview_png(jwst_i2d(), max_side=100))
    assert drawn.shape == (80, 80), "320 pixels binned by 4"
    with pytest.raises(ValueError, match="not a readable FITS file"):
        preview_png(b"not fits")

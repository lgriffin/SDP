"""Records for the images a field investigation read (INV-020): the FITS header walk that sizes an
image, and how Retrieve's products and the counterpart test's results become stars on a picture."""

from __future__ import annotations

import hashlib
import json
import math
from types import SimpleNamespace
from typing import Any

import pytest

from sdp.kernel.application import field_images as module
from sdp.kernel.application.field_images import BLOCK, field_images, image_size
from sdp.kernel.domain.ids import ArtifactId
from sdp.kernel.domain.investigation import Stage

pytestmark = pytest.mark.tier("T1")


def header(*cards: str) -> bytes:
    """One header: each card padded to 80 characters, END, the block padded to 2880 bytes."""
    text = "".join(card.ljust(80) for card in (*cards, "END"))
    return text.ljust(-(-len(text) // BLOCK) * BLOCK).encode("latin-1")


def data(size: int) -> bytes:
    return bytes(-(-size // BLOCK) * BLOCK)


def test_the_sci_image_wins_over_an_earlier_one_after_skipping_each_data_unit() -> None:
    fits = (
        header("SIMPLE  = T", "BITPIX  = 8", "NAXIS   = 0")
        + header("XTENSION= 'IMAGE   '", "BITPIX  = -32", "NAXIS   = 2", "NAXIS1  = 1000", "NAXIS2  = 1")
        + data(4000)
        + header("XTENSION= 'BINTABLE'", "BITPIX  = 8", "NAXIS   = 2", "NAXIS1  = 10", "NAXIS2  = 1", "PCOUNT  = 3000")
        + data(3010)
        + header(
            "XTENSION= 'IMAGE   '", "BITPIX  = -32", "NAXIS   = 2", "NAXIS1  = 3", "NAXIS2  = 2", "EXTNAME = 'SCI'"
        )
        + data(24)
    )
    assert image_size(fits) == (3, 2)


def test_without_a_sci_image_the_first_two_axis_image_is_measured() -> None:
    fits = (
        header("SIMPLE  = T", "BITPIX  = 16", "NAXIS   = 2", "NAXIS1  = 300", "NAXIS2  = 200")
        + data(120000)
        + header("XTENSION= 'IMAGE   '", "BITPIX  = 8", "NAXIS   = 2", "NAXIS1  = 7", "NAXIS2  = 9")
        + data(63)
    )
    assert image_size(fits) == (300, 200)


def test_a_header_that_ends_the_file_is_still_read() -> None:
    assert image_size(header("SIMPLE  = T", "BITPIX  = 8", "NAXIS   = 2", "NAXIS1  = 1", "NAXIS2  = 5")) == (1, 5)


def test_an_empty_axis_an_ascii_table_and_a_one_axis_image_are_not_pictures() -> None:
    assert image_size(header("SIMPLE  = T", "NAXIS   = 2", "NAXIS1  = 0", "NAXIS2  = 5")) is None
    assert image_size(header("SIMPLE  = T", "NAXIS   = 2", "NAXIS2  = 5")) is None, "a missing axis is empty"
    assert image_size(header("SIMPLE  = T", "NAXIS   = 1", "NAXIS1  = 5")) is None
    table = header("SIMPLE  = T", "NAXIS   = 0") + header(
        "XTENSION= 'TABLE   '", "BITPIX  = 8", "NAXIS   = 2", "NAXIS1  = 4", "NAXIS2  = 4"
    )
    assert image_size(table + data(16)) is None


def test_a_header_without_naxis_or_with_odd_bytes_is_read_without_failing() -> None:
    assert image_size(header("SIMPLE  = T", "COMMENT \xff\xfe")) is None
    assert image_size(header("SIMPLE  = T", "NAXIS   = 2", "NAXIS1  = 4", "NAXIS2  = 4", "COMMENT \xff")) == (4, 4)
    assert image_size(header("SIMPLE  = T", "NAXIS   = two")) is None
    assert image_size(b"") is None


def test_a_compressed_image_is_measured_by_its_znaxis_cards() -> None:
    fits = header("SIMPLE  = T", "NAXIS   = 0") + header(
        "XTENSION= 'BINTABLE'", "BITPIX  = 8", "NAXIS   = 2", "NAXIS1  = 8", "NAXIS2  = 2",
        "ZIMAGE  = T", "ZNAXIS  = 2", "ZNAXIS1 = 40", "ZNAXIS2 = 30",
    )  # fmt: skip
    assert image_size(fits + data(16)) == (40, 30)


class Store:
    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}
        self.reads: list[str] = []

    def put(self, name: str, value: Any) -> str:
        artifact = f"sha256:{hashlib.sha256(name.encode()).hexdigest()}"
        self.blobs[artifact] = value if isinstance(value, bytes) else json.dumps(value).encode()
        return artifact

    def get(self, artifact: ArtifactId) -> bytes:
        assert isinstance(artifact, ArtifactId)
        self.reads.append(artifact.value)
        return self.blobs[artifact.value]


@pytest.fixture(autouse=True)
def no_cached_sizes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "_sizes", {})


IMAGE_A = header("SIMPLE  = T", "BITPIX  = 8", "NAXIS   = 2", "NAXIS1  = 64", "NAXIS2  = 32")


def product(store: Store, product_id: str, **extra: Any) -> dict[str, Any]:
    return {
        "product_type": "image",
        "product": {"product_id": product_id, "archive": "mast"},
        "artifact_id": store.put(product_id.replace("/", ""), IMAGE_A),
        **extra,
    }


def per_image(image: str, d2: float | None, **extra: Any) -> dict[str, Any]:
    return {
        "image": image,
        "d2": d2,
        "predicted_px": [1.0, 2.0],
        "counterpart_px": [1.5, 2.5],
        "ambiguous": False,
        **extra,
    }


def result(key: str, *on: dict[str, Any], test: str = "image-counterpart", **details: Any) -> dict[str, Any]:
    return {"test_name": test, "subject": {"object_key": key}, "details": {"per_image": list(on), **details}}


def a_set(store: Store, retrieved: Any, tested: Any = None, alpha: float = 0.01) -> Any:
    stages = {Stage.RETRIEVE: SimpleNamespace(artifact_id=ArtifactId(store.put("r", retrieved)))}
    if tested is not None:
        stages[Stage.TEST] = SimpleNamespace(artifact_id=ArtifactId(store.put("t", tested)))
    return SimpleNamespace(stages=stages, profile=SimpleNamespace(ranking=SimpleNamespace(alpha=alpha)))


def test_an_image_names_its_files_and_is_sized_once() -> None:
    store = Store()
    raw = {"raw_artifact_id": "sha256:raw", "read_from": "s3://bucket/a.fits"}
    first = product(store, "o/a.fits", **raw)
    again = {**first, "raw_artifact_id": "sha256:other"}
    other = {**first, "product_type": "catalogue", "product": {"product_id": "o/c", "archive": "x"}}
    s = a_set(store, {"bundles": [{"products": [first, other]}, {"products": [again]}]})
    [image] = field_images(store, s)  # type: ignore[arg-type]
    assert image == {
        "product_id": "o/a.fits",
        "width": 64,
        "height": 32,
        "archive": "mast",
        "artifact_id": first["artifact_id"],
        "raw_artifact_id": "sha256:raw",
        "envelope_artifact_id": None,
        "read_from": "s3://bucket/a.fits",
        "summary": {},
        "checks": None,
        "passed": None,
        "stars": [],
    }
    field_images(store, s)  # type: ignore[arg-type]
    assert store.reads.count(first["artifact_id"]) == 1, "an artifact's size is measured once"


def test_an_unsized_image_has_no_width_or_height_and_images_sort_by_id() -> None:
    store = Store()
    blank = {**product(store, "o/b.fits"), "artifact_id": store.put("blank", b"not fits")}
    s = a_set(store, {"bundles": [{"products": [blank, product(store, "o/a.fits")]}]})
    images = field_images(store, s)  # type: ignore[arg-type]
    assert [(i["product_id"], i["width"], i["height"]) for i in images] == [
        ("o/a.fits", 64, 32),
        ("o/b.fits", None, None),
    ]


def test_a_set_without_retrieve_has_no_images_and_without_test_no_stars() -> None:
    store = Store()
    nothing = SimpleNamespace(stages={Stage.RETRIEVE: SimpleNamespace(artifact_id=None)})
    assert field_images(store, nothing) == []  # type: ignore[arg-type]
    assert field_images(store, SimpleNamespace(stages={})) == []  # type: ignore[arg-type]


def test_each_star_lands_on_its_image_flagged_at_the_alpha() -> None:
    store = Store()
    a, b = product(store, "o/a.fits"), product(store, "o/b.fits")
    alpha = math.exp(-0.5)  # the p of d2 = 1 exactly: a star at the alpha is not flagged
    tested = {
        "results": [
            result("calm", per_image("o/a.fits", 0.1), per_image("o/elsewhere.fits", 0.1)),
            result("edge", per_image("o/a.fits", 1.0)),
            result("gone", per_image("o/a.fits", None, counterpart_px=None), per_image("o/b.fits", 0.2)),
            result("twin", per_image("o/a.fits", 0.3, ambiguous=True)),
            result("other", per_image("o/a.fits", 50.0), test="something-else"),
            result("bare"),
        ]
    }
    s = a_set(store, {"bundles": [{"products": [a, b]}]}, tested, alpha=alpha)
    investigation = SimpleNamespace(candidate=SimpleNamespace(object_key="gone"), investigation_id="inv-1")
    blank = SimpleNamespace(candidate=SimpleNamespace(object_key=None), investigation_id="inv-2")
    image_a, image_b = field_images(store, s, [investigation, blank])  # type: ignore[arg-type, list-item]
    assert [star["object_key"] for star in image_a["stars"]] == ["gone", "twin", "calm", "edge"]
    gone = image_a["stars"][0]
    assert gone == {
        "object_key": "gone",
        "investigation_id": "inv-1",
        "predicted_px": [1.0, 2.0],
        "counterpart_px": None,
        "d2": None,
        "p_value": 0.0,
        "flagged": True,
        "ambiguous": False,
        "counterparts": None,
    }
    edge = image_a["stars"][3]
    assert edge["p_value"] == alpha and not edge["flagged"] and edge["investigation_id"] is None
    assert [star["object_key"] for star in image_b["stars"]] == ["gone"]


def test_the_competing_hypotheses_are_each_images_own() -> None:
    store = Store()
    a, b = product(store, "o/a.fits"), product(store, "o/b.fits")
    tested = {
        "results": [
            result("own", per_image("o/a.fits", 0.1, counterparts=["mine"])),
            result(
                "old", per_image("o/a.fits", 0.1), per_image("o/b.fits", 0.1), image="o/a.fits", counterparts=["top"]
            ),
        ]
    }
    image_a, image_b = field_images(store, a_set(store, {"bundles": [{"products": [a, b]}]}, tested))  # type: ignore[arg-type]
    by_key = {star["object_key"]: star["counterparts"] for star in image_a["stars"]}
    assert by_key == {"own": ["mine"], "old": ["top"]}
    assert [star["counterparts"] for star in image_b["stars"]] == [None]


def test_an_envelope_gives_the_summary_and_the_checks() -> None:
    store = Store()
    envelope = store.put(
        "env",
        {
            "metadata": {"mission": "JWST", "filter_or_grating": "F200W", "extra": 1},
            "validation": {"checks": [{"name": "wcs", "passed": False}], "passed": False},
        },
    )
    a = product(store, "o/a.fits", envelope_artifact_id=envelope)
    [image] = field_images(store, a_set(store, {"bundles": [{"products": [a]}]}))  # type: ignore[arg-type]
    assert image["summary"] == {"mission": "JWST", "filter_or_grating": "F200W"}
    assert image["checks"] == [{"name": "wcs", "passed": False}] and image["passed"] is False
    assert image["envelope_artifact_id"] == envelope

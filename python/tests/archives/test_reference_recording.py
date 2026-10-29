"""The reference-product recording run against a primed fake MAST: what it writes is what the T5s
loader reads once a person has filled the reference record (Stage A, D35)."""

from __future__ import annotations

import hashlib
import io
import json
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import IO

import pytest

from sdp.adapters.archives.capabilities import VoCapabilityDiscovery
from sdp.adapters.archives.mast import MAST_OBSCORE
from sdp.adapters.archives.mast_inventory import MastProductInventory
from sdp.adapters.archives.reference_recording import (
    MAX_PRODUCT_BYTES,
    REFERENCE_FIELDS,
    ProductTooLarge,
    describe_mast,
    ivoid_request,
    mast_source,
    parse_size,
    record_product,
)
from sdp.adapters.archives.transport import HttpRequest
from sdp.adapters.archives.vo import literal, sync_request
from sdp.contracts import validate
from sdp.kernel.domain.errors import InvariantViolation
from sdp.kernel.domain.integration import ReferenceRecord, ReferenceStatus
from sdp.kernel.domain.investigation import ProductRef
from sdp.testing.fakes.integration import InMemoryCapabilityRegistry

from .test_capability_discovery import primed as primed_discovery
from .test_mast_inventory import complete
from .vo_fakes import PrimedService

pytestmark = pytest.mark.tier("T3")

OBS_ID = "jw99999-o001_t001_nircam_clear-f200w"
NAME = f"{OBS_ID}_i2d.fits"
URL = f"https://mast.stsci.edu/portal/Download/file/JWST/product/{NAME}"
IVOID = "ivo://example.invalid/mast/caom"
DATA = b"SIMPLE  =                    T / synthetic bytes, never parsed by the recording".ljust(2880)


def prime_mast(fake: PrimedService, data: bytes) -> None:
    fake.table(ivoid_request(MAST_OBSCORE), {"ivoid": [IVOID]})
    source = mast_source(IVOID)
    primed_discovery(fake, VoCapabilityDiscovery(fake, InMemoryCapabilityRegistry(), {"mast": source}), source)
    fake.table(
        sync_request(MAST_OBSCORE, f"SELECT access_url FROM ivoa.obscore WHERE obs_id = {literal(OBS_ID)}", 100),
        {"access_url": [URL.replace("_i2d.fits", "_i2d.jpg"), URL]},
    )
    fake.raw(HttpRequest("GET", URL), data, content_type="application/octet-stream")
    inventory = MastProductInventory(fake)
    ref = ProductRef("mast", OBS_ID, URL)
    fake.table(
        inventory.obscore_request(ref),
        {"obs_id": [OBS_ID], "obs_collection": ["JWST"], "calib_level": [3], "data_rights": ["PUBLIC"]},
    )
    fake.raw(inventory.observation_request(OBS_ID), complete([{"obsid": "1", "obs_id": OBS_ID}]))
    fake.raw(
        inventory.products_request("1"),
        complete([{"productFilename": NAME, "productType": "SCIENCE", "calib_level": 3, "dataRights": "PUBLIC"}]),
    )


def test_the_descriptor_is_found_by_url_and_saved(tmp_path: Path) -> None:
    fake = PrimedService()
    prime_mast(fake, DATA)
    doc = describe_mast(fake, tmp_path, today=lambda: date(2026, 10, 28))
    assert doc["ivoid"] == IVOID and doc["origin"] == "registry"
    assert validate("capability-descriptor", doc) == []
    assert json.loads((tmp_path / "mast.descriptor.json").read_text()) == doc


@pytest.mark.req("INT-005")
def test_a_recorded_product_is_laid_out_for_review_and_loads_once_described(tmp_path: Path) -> None:
    fake = PrimedService()
    data = DATA
    prime_mast(fake, data)
    recorded = record_product(fake, OBS_ID, NAME, tmp_path / "nircam-i2d")
    assert recorded.access_url == URL and recorded.size_bytes == len(data)
    d = recorded.directory
    assert (d / NAME).read_bytes() == data, "the raw bytes exactly as served"
    inventory = json.loads((d / "inventory.json").read_text())
    assert inventory["productType"] == "SCIENCE" and "Mast.Caom.Products" in inventory["rendered_query"]
    template = json.loads((d / "reference.template.json").read_text())
    assert template["status"] == "to_verify" and set(REFERENCE_FIELDS) <= set(template)

    filled = {
        **template,
        "product_type": "image",
        "mission": "JWST",
        "instrument": "NIRCAM",
        "frame": "ICRS",
        "time_scale": "UTC",
        "calibration_level": 3,
        "extensions": {"SCI": "data"},
        "has_wcs": True,
        "progenitors": [],
        "documentation": "https://jwst-docs.stsci.edu/ (synthetic test)",
    }
    ref = ReferenceRecord.from_document(filled)
    assert ref.status is ReferenceStatus.TO_VERIFY and not ref.admits
    verified = ReferenceRecord.from_document({**filled, "status": "verified", "verified_by": ["leigh"]})
    assert verified.admits, "SDP has one user, so one named verifier is enough"
    with pytest.raises(InvariantViolation, match="names who verified it"):
        ReferenceRecord.from_document({**filled, "status": "verified", "verified_by": []})


def test_a_file_obscore_does_not_list_is_named_with_what_it_does_list(tmp_path: Path) -> None:
    fake = PrimedService()
    prime_mast(fake, DATA)
    with pytest.raises(LookupError, match=r"_i2d\.jpg"):
        record_product(fake, OBS_ID, "jw00000_cal.fits", tmp_path / "x")


def test_a_product_too_large_to_commit_is_refused_before_anything_is_written(tmp_path: Path) -> None:
    fake = PrimedService()
    prime_mast(fake, DATA)
    with pytest.raises(ProductTooLarge, match="over the 100-byte limit"):
        record_product(fake, OBS_ID, NAME, tmp_path / "big", max_bytes=100)
    assert not (tmp_path / "big").exists()


# -- local pulls over the committed-product cap (#61) -----------------------------------------------


def opener_of(data: bytes, opened: list[str]) -> Callable[[str], IO[bytes]]:
    def opener(url: str) -> IO[bytes]:
        opened.append(url)
        return io.BytesIO(data)

    return opener


@pytest.mark.req("INT-021")
@pytest.mark.parametrize(
    ("text", "size"),
    [("35MB", 35 * 1024**2), ("500MB", 500 * 1024**2), ("2GB", 2 * 1024**3), ("2.5 gb", int(2.5 * 1024**3)),
     ("4096", 4096), ("10K", 10 * 1024)],
)  # fmt: skip
def test_sizes_are_read_like_500mb_or_2gb(text: str, size: int) -> None:
    assert parse_size(text) == size


@pytest.mark.req("INT-021")
def test_a_bad_size_is_refused_with_an_example() -> None:
    with pytest.raises(ValueError, match="like 500MB"):
        parse_size("lots")


@pytest.mark.req("INT-021")
def test_under_the_cap_the_product_is_fetched_through_the_cassette_transport(tmp_path: Path) -> None:
    fake = PrimedService()
    prime_mast(fake, DATA)
    opened: list[str] = []
    recorded = record_product(fake, OBS_ID, NAME, tmp_path / "small", opener=opener_of(b"never", opened))
    assert not recorded.streamed and opened == []
    assert recorded.sha256 == "sha256:" + hashlib.sha256(DATA).hexdigest()
    assert (tmp_path / "small" / NAME).read_bytes() == DATA


@pytest.mark.req("INT-021")
def test_over_the_default_cap_without_the_flag_is_refused(tmp_path: Path) -> None:
    fake = PrimedService()
    big = DATA * (MAX_PRODUCT_BYTES // len(DATA) + 1)
    prime_mast(fake, big)
    with pytest.raises(ProductTooLarge, match="--max-bytes and an --out outside fixtures/"):
        record_product(fake, OBS_ID, NAME, tmp_path / "big")
    assert not (tmp_path / "big").exists()


@pytest.mark.req("INT-021")
def test_over_the_cap_with_the_flag_streams_outside_fixtures_and_keeps_no_product_cassette(tmp_path: Path) -> None:
    fake = PrimedService()
    prime_mast(fake, DATA)
    big = b"\x01" * (3 * 1024**2 + 17)  # spans several 1 MB reads
    opened: list[str] = []
    out = tmp_path / "reports" / "local-products" / "full"
    recorded = record_product(fake, OBS_ID, NAME, out, max_bytes=parse_size("400MB"), opener=opener_of(big, opened))
    assert opened == [URL] and recorded.streamed and recorded.size_bytes == len(big)
    assert (out / NAME).read_bytes() == big and not (out / f"{NAME}.part").exists()
    assert recorded.sha256 == "sha256:" + hashlib.sha256(big).hexdigest()
    assert json.loads((out / "inventory.json").read_text())["productFilename"] == NAME
    assert HttpRequest("GET", URL).key() not in fake.sent, "the product itself is never sent through the cassettes"


@pytest.mark.req("INT-021")
def test_a_large_pull_into_fixtures_is_refused_before_anything_is_fetched(tmp_path: Path) -> None:
    fake = PrimedService()
    prime_mast(fake, DATA)
    target = tmp_path / "fixtures" / "reference-products" / "full"
    with pytest.raises(ProductTooLarge, match="choose an --out outside fixtures/"):
        record_product(fake, OBS_ID, NAME, target, max_bytes=parse_size("400MB"))
    assert not target.exists() and fake.sent == []


@pytest.mark.req("INT-021")
def test_a_stream_that_passes_its_limit_is_stopped_and_removed(tmp_path: Path) -> None:
    fake = PrimedService()
    prime_mast(fake, DATA)
    out = tmp_path / "local"
    limit = MAX_PRODUCT_BYTES + 1
    with pytest.raises(ProductTooLarge, match="passed"):
        record_product(fake, OBS_ID, NAME, out, max_bytes=limit, opener=opener_of(b"\0" * (limit + 1), []))
    assert not (out / NAME).exists() and not (out / f"{NAME}.part").exists()

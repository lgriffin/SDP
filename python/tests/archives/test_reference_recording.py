"""The reference-product recording run against a primed fake MAST: what it writes is what the T5s
loader reads once a person has filled the reference record (Stage A, D35)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from sdp.adapters.archives.capabilities import VoCapabilityDiscovery
from sdp.adapters.archives.mast import MAST_OBSCORE
from sdp.adapters.archives.mast_inventory import MastProductInventory
from sdp.adapters.archives.reference_recording import (
    REFERENCE_FIELDS,
    ProductTooLarge,
    describe_mast,
    ivoid_request,
    mast_source,
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

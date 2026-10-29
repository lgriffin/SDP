"""Record one public MAST product as a T5s reference product (Integration v0.4.0 §6, Stage A).

The only code that reads MAST live for the product model. It is run by the manual "Integration
recordings" workflow, never in CI, and writes what ``fixtures/reference-products/README.md`` lays
out: the raw file, ``inventory.json``, ``obscore.json`` and a ``reference.template.json`` a person
fills from the mission documentation and renames to ``reference.json``. Every exchange is also kept
as a cassette, and MAST's capability descriptor is read and saved beside them.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import IO, Any

from sdp.adapters.archives.capabilities import VoCapabilityDiscovery, VoSource
from sdp.adapters.archives.mast import CITATION, LICENCE, MAST_OBSCORE, MastObservationGateway, file_name
from sdp.adapters.archives.mast_inventory import MastProductInventory
from sdp.adapters.archives.registry import REGTAP
from sdp.adapters.archives.transport import HttpRequest, Transport
from sdp.adapters.archives.vo import literal, run_sync, sync_request
from sdp.kernel.domain.integration import AdapterInfo, Rights, SourceCapabilityDescriptor
from sdp.kernel.domain.investigation import ProductRef

#: Every field ``ReferenceRecord`` reads; null until a person fills it from the documentation.
REFERENCE_FIELDS = (
    "product_id",
    "product_type",
    "mission",
    "instrument",
    "detector",
    "filter_or_grating",
    "frame",
    "time_scale",
    "time_start_mjd",
    "time_end_mjd",
    "units",
    "calibration_level",
    "extensions",
    "has_wcs",
    "progenitors",
    "documentation",
)


class Unregistered:
    """Describing reads the VO only; nothing is registered from a recording run."""

    def put(self, descriptor: SourceCapabilityDescriptor) -> None:
        raise RuntimeError("a recording run registers nothing")

    def get(self, source_id: str) -> SourceCapabilityDescriptor | None:
        return None

    def all(self) -> tuple[SourceCapabilityDescriptor, ...]:
        return ()

    def stale(self, older_than: date) -> tuple[SourceCapabilityDescriptor, ...]:
        return ()


@dataclass(frozen=True)
class Recorded:
    directory: Path
    access_url: str
    size_bytes: int
    seconds: float = 0.0
    #: streamed straight to disk, outside the cassettes (a local pull over the committed-product cap)
    streamed: bool = False
    sha256: str = ""


def ivoid_request(access_url: str, regtap_url: str = REGTAP) -> HttpRequest:
    adql = (
        "SELECT DISTINCT i.ivoid FROM rr.interface AS i "
        f"WHERE i.access_url = {literal(access_url)} OR i.access_url = {literal(access_url + '/')}"
    )
    return sync_request(regtap_url, adql, 10)


def mast_source(ivoid: str) -> VoSource:
    """MAST as a VO source, served by the ``mast-observations`` adapter. Its ObsCore view has no
    ``data_rights`` column, and a scan of every collection times out (HTTP 504, checked live
    2026-10-28), so only JWST is scanned."""
    return VoSource(
        "mast",
        ivoid,
        MAST_OBSCORE,
        Rights(LICENCE, CITATION),
        adapter=AdapterInfo("mast-observations", "1.0.0"),
        collections=("JWST",),
        rights_column=None,
    )


def mast_ivoid(transport: Transport) -> str:
    """MAST's ObsCore service's registry id, found in the registry by its URL."""
    ivoids = sorted(
        {str(r["ivoid"]).strip() for r in run_sync(transport, ivoid_request(MAST_OBSCORE), "RegTAP").to_pylist()}
    )
    if not ivoids:
        raise LookupError(f"the registry lists no service at {MAST_OBSCORE}")
    return ivoids[0]


def describe_mast(transport: Transport, out: Path, today: Callable[[], date] | None = None) -> dict[str, Any]:
    """Find MAST's ObsCore service in the registry by its URL, read its descriptor, and save it."""
    source = mast_source(mast_ivoid(transport))
    discovery = VoCapabilityDiscovery(transport, Unregistered(), {"mast": source})
    if today is not None:
        discovery.today = today
    doc = discovery.describe("mast").to_document()
    out.mkdir(parents=True, exist_ok=True)
    (out / "mast.descriptor.json").write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    return doc


def access_url_for(transport: Transport, obs_id: str, filename: str) -> str:
    adql = f"SELECT access_url FROM ivoa.obscore WHERE obs_id = {literal(obs_id)}"
    urls = [
        str(r["access_url"]) for r in run_sync(transport, sync_request(MAST_OBSCORE, adql, 100), "ObsCore").to_pylist()
    ]
    for url in urls:
        if file_name(url) == filename:
            return url
    raise LookupError(
        f"ObsCore lists no access URL for {filename} in {obs_id}; it lists {sorted(file_name(u) for u in urls)}"
    )


#: The recording is committed with its cassette, so each copy must stay well under GitHub's 100 MB file limit.
MAX_PRODUCT_BYTES = 35 * 1024**2

#: Size units for ``--max-bytes``; MB and GB are binary (1 MB = 1024**2 bytes), as MAX_PRODUCT_BYTES is.
_UNITS = {"": 1, "B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
_SIZE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([KMGT]?B?)\s*$", re.IGNORECASE)

Opener = Callable[[str], IO[bytes]]


def parse_size(text: str) -> int:
    """``500MB``, ``2GB``, ``2.5 GB`` or a plain byte count, as bytes."""
    match = _SIZE.match(text)
    if match is None:
        raise ValueError(f"not a size: {text!r}; write it like 500MB or 2GB")
    unit = match[2].upper()
    unit = unit if unit.endswith("B") or not unit else unit + "B"
    return int(float(match[1]) * _UNITS[unit])


class ProductTooLarge(ValueError):
    """The product is too large to commit as a reference product."""


def _check_size(filename: str, size: int, limit: int) -> None:
    if size > limit:
        hint = (
            "choose a smaller product (a MIRI image, or a level 2 file), or pull it for local testing "
            "with --max-bytes and an --out outside fixtures/"
            if limit == MAX_PRODUCT_BYTES
            else "raise --max-bytes"
        )
        raise ProductTooLarge(f"{filename} is {size} bytes, over the {limit}-byte limit; {hint}")


def _urlopen(url: str) -> IO[bytes]:
    return urllib.request.urlopen(url, timeout=300)  # type: ignore[no-any-return]


def stream_to(opener: Opener, url: str, target: Path, max_bytes: int) -> tuple[int, str]:
    """Download ``url`` to ``target`` a megabyte at a time, never holding the product in memory; a
    download that passes ``max_bytes`` is stopped and removed. Returns the size and SHA-256."""
    partial = target.with_name(target.name + ".part")
    digest, size = hashlib.sha256(), 0
    try:
        with opener(url) as response, partial.open("wb") as out:
            while chunk := response.read(1 << 20):
                size += len(chunk)
                if size > max_bytes:
                    raise ProductTooLarge(
                        f"{target.name} passed {max_bytes} bytes while downloading; raise --max-bytes"
                    )
                digest.update(chunk)
                out.write(chunk)
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)
    return size, "sha256:" + digest.hexdigest()


def record_product(
    transport: Transport,
    obs_id: str,
    filename: str,
    out: Path,
    max_bytes: int = MAX_PRODUCT_BYTES,
    opener: Opener = _urlopen,
) -> Recorded:
    """Fetch the product and its rows exactly as Retrieve would, and lay them out for review. A product
    whose inventory size or fetched bytes exceed ``max_bytes`` is refused before anything is written.

    Above the committed-product cap the run is a local pull (#61): it must write outside ``fixtures/``,
    so a large file is never committed by accident, and the product is streamed to disk rather than
    held in memory or kept in a cassette; the metadata exchanges are still recorded."""
    large = max_bytes > MAX_PRODUCT_BYTES
    if large and "fixtures" in out.resolve().parts:
        raise ProductTooLarge("products this large are for local testing; choose an --out outside fixtures/")
    url = access_url_for(transport, obs_id, filename)
    ref = ProductRef("mast", obs_id, url)
    rows = MastProductInventory(transport).rows(ref)
    listed = rows.inventory_row.get("size")
    if isinstance(listed, int | float) and not isinstance(listed, bool):
        _check_size(filename, int(listed), max_bytes)
    started = time.monotonic()
    if large:
        out.mkdir(parents=True, exist_ok=True)
        size, sha256 = stream_to(opener, url, out / filename, max_bytes)
    else:
        fetched = MastObservationGateway(transport).fetch(ref)
        _check_size(filename, len(fetched.data), max_bytes)
        out.mkdir(parents=True, exist_ok=True)
        (out / filename).write_bytes(fetched.data)
        size, sha256 = len(fetched.data), "sha256:" + hashlib.sha256(fetched.data).hexdigest()
    seconds = time.monotonic() - started
    inventory = {**rows.inventory_row, "productFilename": filename, "rendered_query": rows.rendered_query}
    inventory.setdefault("dataURL", url)
    (out / "inventory.json").write_text(json.dumps(inventory, indent=2, sort_keys=True, default=str) + "\n")
    (out / "obscore.json").write_text(json.dumps(dict(rows.obscore_row), indent=2, sort_keys=True, default=str) + "\n")
    template: dict[str, Any] = dict.fromkeys(REFERENCE_FIELDS)
    template.update({"product_id": filename, "status": "to_verify", "verified_by": [], "documentation": ""})
    (out / "reference.template.json").write_text(json.dumps(template, indent=2) + "\n")
    return Recorded(out, url, size, round(seconds, 3), large, sha256)

"""Record one public MAST product as a T5s reference product (Integration v0.4.0 §6, Stage A).

The only code that reads MAST live for the product model. It is run by the manual "Integration
recordings" workflow, never in CI, and writes what ``fixtures/reference-products/README.md`` lays
out: the raw file, ``inventory.json``, ``obscore.json`` and a ``reference.template.json`` a person
fills from the mission documentation and renames to ``reference.json``. Every exchange is also kept
as a cassette, and MAST's capability descriptor is read and saved beside them.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from sdp.adapters.archives.capabilities import VoCapabilityDiscovery, VoSource
from sdp.adapters.archives.mast import CITATION, LICENCE, MAST_OBSCORE, MastObservationGateway
from sdp.adapters.archives.mast_inventory import MastProductInventory, file_name
from sdp.adapters.archives.registry import REGTAP
from sdp.adapters.archives.transport import HttpRequest, Transport
from sdp.adapters.archives.vo import literal, run_sync, sync_request
from sdp.kernel.domain.integration import Rights, SourceCapabilityDescriptor
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


class _Unregistered:
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


def ivoid_request(access_url: str, regtap_url: str = REGTAP) -> HttpRequest:
    adql = (
        "SELECT DISTINCT i.ivoid FROM rr.interface AS i "
        f"WHERE i.access_url = {literal(access_url)} OR i.access_url = {literal(access_url + '/')}"
    )
    return sync_request(regtap_url, adql, 10)


def describe_mast(transport: Transport, out: Path, today: Callable[[], date] | None = None) -> dict[str, Any]:
    """Find MAST's ObsCore service in the registry by its URL, read its descriptor, and save it."""
    ivoids = sorted(
        {str(r["ivoid"]).strip() for r in run_sync(transport, ivoid_request(MAST_OBSCORE), "RegTAP").to_pylist()}
    )
    if not ivoids:
        raise LookupError(f"the registry lists no service at {MAST_OBSCORE}")
    source = VoSource("mast", ivoids[0], MAST_OBSCORE, Rights(LICENCE, CITATION))
    discovery = VoCapabilityDiscovery(transport, _Unregistered(), {"mast": source})
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


def record_product(transport: Transport, obs_id: str, filename: str, out: Path) -> Recorded:
    """Fetch the product and its rows exactly as Retrieve would, and lay them out for review."""
    url = access_url_for(transport, obs_id, filename)
    ref = ProductRef("mast", obs_id, url)
    fetched = MastObservationGateway(transport).fetch(ref)
    rows = MastProductInventory(transport).rows(ref)
    out.mkdir(parents=True, exist_ok=True)
    (out / filename).write_bytes(fetched.data)
    inventory = {**rows.inventory_row, "productFilename": filename, "rendered_query": rows.rendered_query}
    inventory.setdefault("dataURL", url)
    (out / "inventory.json").write_text(json.dumps(inventory, indent=2, sort_keys=True, default=str) + "\n")
    (out / "obscore.json").write_text(json.dumps(dict(rows.obscore_row), indent=2, sort_keys=True, default=str) + "\n")
    template: dict[str, Any] = dict.fromkeys(REFERENCE_FIELDS)
    template.update({"product_id": filename, "status": "to_verify", "verified_by": [], "documentation": ""})
    (out / "reference.template.json").write_text(json.dumps(template, indent=2) + "\n")
    return Recorded(out, url, len(fetched.data))

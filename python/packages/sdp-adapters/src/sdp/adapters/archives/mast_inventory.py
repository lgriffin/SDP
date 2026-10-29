"""MAST's own description of a product, beside the ObsCore row (Integration v0.4.0 §3, §7, Stage A).

ObsCore lists observations; the files in an observation (science, previews, auxiliaries, each with
its type, calibration level, rights and size) come from MAST's product inventory, the service
``astroquery.mast``'s ``get_product_list`` calls. Both are asked through the archive ``Transport``
(D31), so CI replays recordings:

1. the ObsCore row for the product, by ``obs_id`` and access URL, from MAST's TAP service;
2. the CAOM observation id for that ``obs_id`` (``Mast.Caom.Filtered``);
3. the observation's products (``Mast.Caom.Products``), from which the row whose file name or data
   URI matches the fetched product is taken.
"""

from __future__ import annotations

import json
import urllib.parse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sdp.adapters.archives.mast import MAST_OBSCORE, file_name, obs_id_of
from sdp.adapters.archives.transport import HttpRequest, Transport, TransportError
from sdp.adapters.archives.untrusted import UntrustedInput
from sdp.adapters.archives.vo import literal, run_sync, sync_request
from sdp.kernel.domain.investigation import ProductRef
from sdp.kernel.ports.integration import ProductRows

MAST_API = "https://mast.stsci.edu/api/v0/invoke"
MAX_RESPONSE_BYTES = 64 * 1024**2

#: The ObsCore columns the envelope reads (ObsCore 1.1 names, as MAST's CAOM view serves them; it has no
#: ``data_rights``, so rights come from the inventory's ``dataRights``).
OBSCORE_COLUMNS = (
    "obs_id",
    "obs_collection",
    "instrument_name",
    "dataproduct_type",
    "calib_level",
    "target_name",
    "s_ra",
    "s_dec",
    "s_region",
    "s_resolution",
    "s_fov",
    "t_min",
    "t_max",
    "em_min",
    "em_max",
    "access_url",
    "access_format",
    "access_estsize",
)


class InventoryMissing(LookupError):
    """MAST lists no product matching what the gateway fetched."""


def _invoke(api_url: str, request: Mapping[str, Any]) -> HttpRequest:
    body = urllib.parse.urlencode({"request": json.dumps(request, sort_keys=True, separators=(",", ":"))}).encode()
    return HttpRequest("POST", api_url, body, {"Content-Type": "application/x-www-form-urlencoded"})


def _rows(transport: Transport, request: HttpRequest, what: str) -> list[dict[str, Any]]:
    response = transport.send(request)
    if response.status != 200:
        raise TransportError(f"{what}: HTTP {response.status}", response.status)
    if len(response.body) > MAX_RESPONSE_BYTES:
        raise UntrustedInput(f"{what}: response of {len(response.body)} bytes exceeds {MAX_RESPONSE_BYTES}")
    doc = json.loads(response.body)
    if not isinstance(doc, dict) or str(doc.get("status", "")).upper() != "COMPLETE":
        raise TransportError(f"{what}: MAST answered status {doc.get('status') if isinstance(doc, dict) else doc!r}")
    data = doc.get("data")
    if not isinstance(data, list):
        raise TransportError(f"{what}: MAST answered without a data list")
    return [dict(r) for r in data if isinstance(r, dict)]


@dataclass
class MastProductInventory:
    """``ProductInventory`` for MAST products a ``MastObservationGateway`` fetched."""

    transport: Transport
    service_url: str = MAST_OBSCORE
    api_url: str = MAST_API
    page_size: int = 2000
    max_pages: int = 50

    def obscore_request(self, product: ProductRef) -> HttpRequest:
        adql = (
            f"SELECT TOP 1 {', '.join(OBSCORE_COLUMNS)} FROM ivoa.obscore "
            f"WHERE obs_id = {literal(obs_id_of(product.product_id))} AND access_url = {literal(product.uri)}"
        )
        return sync_request(self.service_url, adql, 1)

    def observation_request(self, obs_id: str) -> HttpRequest:
        return _invoke(
            self.api_url,
            {
                "service": "Mast.Caom.Filtered",
                "format": "json",
                "pagesize": 10,
                "page": 1,
                "params": {"columns": "obsid,obs_id", "filters": [{"paramName": "obs_id", "values": [obs_id]}]},
            },
        )

    def products_request(self, obsid: str, page: int = 1) -> HttpRequest:
        return _invoke(
            self.api_url,
            {
                "service": "Mast.Caom.Products",
                "format": "json",
                "pagesize": self.page_size,
                "page": page,
                "params": {"obsid": obsid},
            },
        )

    def rows(self, product: ProductRef) -> ProductRows:
        obscore = run_sync(self.transport, self.obscore_request(product), "MAST ObsCore row").to_pylist()
        obs_id = obs_id_of(product.product_id)
        observations = _rows(self.transport, self.observation_request(obs_id), "MAST observation id")
        obsids = sorted({str(r["obsid"]) for r in observations if r.get("obsid") is not None})
        if not obsids:
            raise InventoryMissing(f"MAST has no CAOM observation with obs_id {obs_id}")
        wanted = file_name(product.uri)
        rendered = [self.obscore_request(product), self.observation_request(obs_id)]
        for obsid in obsids:
            # Page until the file is found or a short page says the inventory has run out.
            for page in range(1, self.max_pages + 1):
                request = self.products_request(obsid, page)
                rendered.append(request)
                page_rows = _rows(self.transport, request, "MAST product inventory")
                for row in page_rows:
                    if str(row.get("productFilename") or "") == wanted or (
                        row.get("dataURI") and file_name(str(row["dataURI"])) == wanted
                    ):
                        return ProductRows(
                            inventory_row=row,
                            obscore_row=obscore[0] if obscore else {},
                            links=(),
                            rendered_query=render(rendered),
                        )
                if len(page_rows) < self.page_size:
                    break
        raise InventoryMissing(f"MAST lists no product named {wanted} in observation {obs_id}")


def render(requests: Sequence[HttpRequest]) -> str:
    """Every request as ``METHOD URL BODY``, one per line: the provenance of the rows."""
    return "\n".join(f"{r.method} {r.url} {r.body.decode('utf-8', 'replace')}".rstrip() for r in requests)

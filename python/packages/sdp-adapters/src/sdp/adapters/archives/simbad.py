"""SIMBAD behind ObjectResolver (Investigate §3, stage one): is this position or name already known?

Positions go to SIMBAD's TAP service (``basic``, nearest first, then its bibliography through
``has_ref`` and ``ref``); names go to the CDS Sesame resolver, restricted to SIMBAD. Both travel
through a Transport, so CI replays recordings and never calls SIMBAD (ADR-015). SIMBAD has no
release, so the release label names the service and the recording date travels with the cassette.
"""

from __future__ import annotations

import math
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from sdp.adapters.archives.transport import HttpRequest, Transport, TransportError
from sdp.adapters.archives.vo import literal, run_sync, sync_request
from sdp.kernel.domain.investigation import ResolverRecord

SIMBAD_TAP = "https://simbad.cds.unistra.fr/simbad/sim-tap"
SESAME = "https://cds.unistra.fr/cgi-bin/nph-sesame/-ox/S"
LICENCE = "SIMBAD: acknowledge use per the CDS conditions of use (https://cds.unistra.fr/conditions-of-use/)"
CITATION = "Wenger et al. 2000, A&AS 143, 9 (SIMBAD)"


@dataclass
class SimbadResolver:
    transport: Transport
    tap_url: str = SIMBAD_TAP
    sesame_url: str = SESAME
    max_bibcodes: int = 50
    resolver_id: str = "simbad-tap"
    release_label: str = "simbad:live"

    # -- requests, public so test servers can be primed with exactly what the adapter sends

    def position_request(self, ra_deg: float, dec_deg: float, radius_arcsec: float) -> HttpRequest:
        here = f"POINT('ICRS', {float(ra_deg)!r}, {float(dec_deg)!r})"
        adql = (
            "SELECT TOP 1 oid, main_id, ra, dec, otype, "
            f"DISTANCE(POINT('ICRS', ra, dec), {here}) AS dist "
            f"FROM basic WHERE 1 = CONTAINS(POINT('ICRS', ra, dec), "
            f"CIRCLE('ICRS', {float(ra_deg)!r}, {float(dec_deg)!r}, {radius_arcsec / 3600.0!r})) "
            "ORDER BY dist, oid"
        )
        return sync_request(self.tap_url, adql, 1)

    def bibcode_request(self, main_id: str) -> HttpRequest:
        adql = (
            f"SELECT TOP {self.max_bibcodes} r.bibcode FROM has_ref AS h "
            "JOIN ref AS r ON h.oidbibref = r.oidbib JOIN basic AS b ON h.oidref = b.oid "
            f"WHERE b.main_id = {literal(main_id)} ORDER BY r.bibcode"
        )
        return sync_request(self.tap_url, adql, self.max_bibcodes)

    def name_request(self, name: str) -> HttpRequest:
        return HttpRequest("GET", f"{self.sesame_url}?{urllib.parse.quote(name.strip())}")

    # -- ObjectResolver

    def by_position(self, ra_deg: float, dec_deg: float, radius_arcsec: float) -> ResolverRecord | None:
        table = run_sync(self.transport, self.position_request(ra_deg, dec_deg, radius_arcsec), "SIMBAD cone")
        if table.num_rows == 0:
            return None
        row = table.slice(0, 1).to_pylist()[0]
        main_id = str(row["main_id"]).strip()
        return ResolverRecord(
            main_id=main_id,
            object_type=str(row["otype"]).strip(),
            bibcodes=self._bibcodes(main_id),
            ra_deg=_float(row.get("ra")),
            dec_deg=_float(row.get("dec")),
            separation_arcsec=float(row["dist"]) * 3600.0,
        )

    def by_name(self, name: str) -> ResolverRecord | None:
        response = self.transport.send(self.name_request(name))
        if response.status != 200:
            raise TransportError(f"Sesame {name!r}: HTTP {response.status}", response.status)
        found = parse_sesame(response.body)
        if found is None:
            return None
        main_id, otype, ra, dec = found
        return ResolverRecord(main_id, otype, self._bibcodes(main_id), ra, dec, None)

    def _bibcodes(self, main_id: str) -> tuple[str, ...]:
        table = run_sync(self.transport, self.bibcode_request(main_id), "SIMBAD bibliography")
        if "bibcode" not in table.column_names:
            return ()
        return tuple(sorted({str(b).strip() for b in table.column("bibcode").to_pylist() if b}))


def parse_sesame(body: bytes) -> tuple[str, str, float | None, float | None] | None:
    """Main identifier, object type and position from Sesame XML; None when SIMBAD has no match."""
    root = ET.fromstring(body)
    for resolver in root.iter("Resolver"):
        oname = resolver.findtext("oname")
        if oname:
            return (
                oname.strip(),
                (resolver.findtext("otype") or "").strip(),
                _float(resolver.findtext("jradeg")),
                _float(resolver.findtext("jdedeg")),
            )
    return None


def _float(value: object) -> float | None:
    if value is None or value == "":
        return None
    f = float(value)  # type: ignore[arg-type]
    return f if math.isfinite(f) else None

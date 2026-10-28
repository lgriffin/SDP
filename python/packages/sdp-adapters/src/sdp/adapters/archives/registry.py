"""The IVOA Registry behind SourceRegistry, through RegTAP (Investigate §3): which VO services of a
standard (TAP, SIA, SSA, cone search) claim coverage of a field.

The query is the one pyvo's ``regsearch(servicetype=…, spatial=…)`` sends to a RegTAP service:
resources joined to their capabilities and standard interfaces, filtered by standard identifier and
by a MOC of the field contained in the resource's coverage. It travels through a Transport, so CI
replays recordings (ADR-015).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from sdp.adapters.archives.transport import HttpRequest, Transport
from sdp.adapters.archives.vo import literal, run_sync, sync_request
from sdp.kernel.domain.archive import Box, Cone
from sdp.kernel.domain.investigation import Field
from sdp.kernel.ports.investigate import ServiceRecord

REGTAP = "https://reg.g-vo.org/tap"

#: The standard identifiers behind the names callers use, as pyvo maps them.
STANDARDS = {
    "tap": "ivo://ivoa.net/std/tap",
    "table": "ivo://ivoa.net/std/tap",
    "sia": "ivo://ivoa.net/std/sia",
    "image": "ivo://ivoa.net/std/sia",
    "ssa": "ivo://ivoa.net/std/ssa",
    "spectrum": "ivo://ivoa.net/std/ssa",
    "conesearch": "ivo://ivoa.net/std/conesearch",
    "scs": "ivo://ivoa.net/std/conesearch",
}


def bounding_cone(field: Field) -> Cone:
    region = field.query_region()
    if isinstance(region, Cone):
        return region
    assert isinstance(region, Box)
    ra = (region.ra_min_deg + region.ra_max_deg) / 2
    dec = (region.dec_min_deg + region.dec_max_deg) / 2
    half_ra = (region.ra_max_deg - region.ra_min_deg) / 2 * math.cos(math.radians(dec))
    half_dec = (region.dec_max_deg - region.dec_min_deg) / 2
    return Cone(ra, dec, min(10.0, math.hypot(half_ra, half_dec)))


@dataclass
class RegTapRegistry:
    transport: Transport
    service_url: str = REGTAP
    max_services: int = 200
    moc_order: int = 6

    def request(self, field: Field, standard: str) -> HttpRequest:
        key = standard.strip().lower()
        if key not in STANDARDS:
            raise ValueError(f"unknown VO standard {standard!r}; expected one of {sorted(set(STANDARDS))}")
        cone = bounding_cone(field)
        moc = f"MOC({self.moc_order}, CIRCLE({cone.ra_deg!r}, {cone.dec_deg!r}, {cone.radius_deg!r}))"
        adql = (
            f"SELECT TOP {self.max_services} r.ivoid, r.res_title, c.standard_id, i.access_url, r.waveband "
            "FROM rr.resource AS r "
            "JOIN rr.capability AS c ON c.ivoid = r.ivoid "
            "JOIN rr.interface AS i ON i.ivoid = c.ivoid AND i.cap_index = c.cap_index "
            f"WHERE c.standard_id LIKE {literal(STANDARDS[key] + '%')} AND i.intf_role = 'std' "
            f"AND r.ivoid IN (SELECT DISTINCT ivoid FROM rr.stc_spatial WHERE 1 = CONTAINS({moc}, coverage)) "
            "ORDER BY r.ivoid, i.access_url"
        )
        return sync_request(self.service_url, adql, self.max_services)

    def services(self, field: Field, standard: str) -> Sequence[ServiceRecord]:
        table = run_sync(self.transport, self.request(field, standard), f"RegTAP {standard}")
        out: list[ServiceRecord] = []
        seen: set[tuple[str, str]] = set()
        for row in table.to_pylist():
            ivoid, url = str(row["ivoid"]).strip(), str(row["access_url"]).strip()
            if not ivoid or not url or (ivoid, url) in seen:
                continue
            seen.add((ivoid, url))
            bands = tuple(b for b in str(row.get("waveband") or "").split("#") if b)
            out.append(ServiceRecord(ivoid, str(row["res_title"]).strip(), standard.lower(), url, bands))
        return out

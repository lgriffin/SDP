"""SIMBAD behind ObjectResolver (Investigate §3, stage one): is this position or name already known?

Positions go to SIMBAD's TAP service (``basic``, nearest first, then its bibliography through
``has_ref`` and ``ref``); names go to the CDS Sesame resolver, restricted to SIMBAD. A whole field of
positions is resolved with one ``basic`` query over its bounding box (split while a page comes back full)
and batched bibliographies, then matched locally by the same nearest-within-radius rule (#65). Both travel
through a Transport, so CI replays recordings and never calls SIMBAD (ADR-015). SIMBAD has no
release, so the release label names the service and the recording date travels with the cassette.
"""

from __future__ import annotations

import math
import urllib.parse
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree

from sdp.adapters.archives import adql
from sdp.adapters.archives.transport import HttpRequest, Transport, TransportError
from sdp.adapters.archives.vo import literal, run_sync, sync_request
from sdp.kernel.domain.archive import Cone
from sdp.kernel.domain.investigation import ResolverRecord

SIMBAD_TAP = "https://simbad.cds.unistra.fr/simbad/sim-tap"
SESAME = "https://cds.unistra.fr/cgi-bin/nph-sesame/-ox/S"
#: CDS's official copies at Harvard, tried in order when Strasbourg gives no answer at all
MIRRORS: dict[str, tuple[str, ...]] = {
    SIMBAD_TAP: ("https://simbad.harvard.edu/simbad/sim-tap",),
    SESAME: ("https://vizier.cfa.harvard.edu/viz-bin/nph-sesame/-ox/S",),
}
LICENCE = "SIMBAD: acknowledge use per the CDS conditions of use (https://cds.unistra.fr/conditions-of-use/)"
CITATION = "Wenger et al. 2000, A&AS 143, 9 (SIMBAD)"


@dataclass(frozen=True)
class _Object:
    oid: int
    main_id: str
    ra: float
    dec: float
    otype: str


@dataclass
class SimbadResolver:
    transport: Transport
    tap_url: str = SIMBAD_TAP
    sesame_url: str = SESAME
    max_bibcodes: int = 50
    #: rows per field query; a box that fills it is split in four and asked again
    field_rows: int = 100_000
    #: objects per bibliography query
    bibcode_batch: int = 100
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
            # ORDER BY names aliases: the Harvard mirror's ADQL parser refuses qualified columns there
            f"SELECT TOP {self.max_bibcodes} r.bibcode AS bibcode FROM has_ref AS h "
            "JOIN ref AS r ON h.oidbibref = r.oidbib JOIN basic AS b ON h.oidref = b.oid "
            f"WHERE b.main_id = {literal(main_id)} ORDER BY bibcode"
        )
        return sync_request(self.tap_url, adql, self.max_bibcodes)

    def box_request(self, ra_min: float, ra_max: float, dec_min: float, dec_max: float) -> HttpRequest:
        adql_text = (
            f"SELECT TOP {self.field_rows} oid, main_id, ra, dec, otype FROM basic "
            f"WHERE ra BETWEEN {ra_min!r} AND {ra_max!r} AND dec BETWEEN {dec_min!r} AND {dec_max!r} ORDER BY oid"
        )
        return sync_request(self.tap_url, adql_text, self.field_rows)

    def bibcodes_request(self, oids: Sequence[int]) -> HttpRequest:
        adql_text = (
            "SELECT h.oidref AS oidref, r.bibcode AS bibcode FROM has_ref AS h JOIN ref AS r ON h.oidbibref = r.oidbib "
            f"WHERE h.oidref IN ({', '.join(str(int(o)) for o in oids)}) ORDER BY oidref, bibcode"
        )
        return sync_request(self.tap_url, adql_text, 2_000_000)

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

    def resolve_positions(
        self, positions: Sequence[tuple[float, float]], radius_arcsec: float
    ) -> list[ResolverRecord | None]:
        """FieldResolver: what ``by_position`` would say of each position, from one field query (#65)."""
        if not positions:
            return []
        objects: dict[int, _Object] = {}
        for box in _boxes(_cover(positions, radius_arcsec)):
            for row in self._objects_in(*box):
                ra, dec = _float(row.get("ra")), _float(row.get("dec"))
                if ra is not None and dec is not None:
                    oid = int(str(row["oid"]))
                    objects[oid] = _Object(oid, str(row["main_id"]).strip(), ra, dec, str(row["otype"]).strip())
        if not objects:
            return [None] * len(positions)
        found = sorted(objects.values(), key=lambda o: o.oid)
        tree = cKDTree(_unit([(o.ra, o.dec) for o in found]))
        chord = 2 * math.sin(math.radians(radius_arcsec / 3600.0) / 2) * (1 + 1e-9)
        nearest: list[tuple[float, _Object] | None] = []
        for (ra, dec), near in zip(positions, tree.query_ball_point(_unit(positions), chord), strict=True):
            within = [
                (sep, found[j])
                for j in near
                if (sep := _separation_deg(ra, dec, found[j].ra, found[j].dec)) * 3600.0 <= radius_arcsec
            ]
            # nearest first, then the lower oid: the order by_position's query uses
            nearest.append(min(within, key=lambda f: (f[0], f[1].oid)) if within else None)
        bibcodes = self._bibcodes_of(sorted({n[1].oid for n in nearest if n is not None}))
        return [
            None
            if n is None
            else ResolverRecord(n[1].main_id, n[1].otype, bibcodes.get(n[1].oid, ()), n[1].ra, n[1].dec, n[0] * 3600.0)
            for n in nearest
        ]

    def _objects_in(self, ra_min: float, ra_max: float, dec_min: float, dec_max: float) -> list[dict[str, object]]:
        table = run_sync(self.transport, self.box_request(ra_min, ra_max, dec_min, dec_max), "SIMBAD field")
        if table.num_rows < self.field_rows:
            rows: list[dict[str, object]] = table.to_pylist()
            return rows
        ra_mid, dec_mid = (ra_min + ra_max) / 2, (dec_min + dec_max) / 2
        if ra_max - ra_min < 1e-4 and dec_max - dec_min < 1e-4:
            raise TransportError(f"SIMBAD field: more than {self.field_rows} objects within 0.36 arcsec")
        return [
            row
            for box in (
                (ra_min, ra_mid, dec_min, dec_mid),
                (ra_mid, ra_max, dec_min, dec_mid),
                (ra_min, ra_mid, dec_mid, dec_max),
                (ra_mid, ra_max, dec_mid, dec_max),
            )
            for row in self._objects_in(*box)
        ]

    def _bibcodes_of(self, oids: Sequence[int]) -> dict[int, tuple[str, ...]]:
        found: dict[int, set[str]] = {}
        for start in range(0, len(oids), self.bibcode_batch):
            table = run_sync(
                self.transport, self.bibcodes_request(oids[start : start + self.bibcode_batch]), "SIMBAD bibliography"
            )
            if "bibcode" not in table.column_names:
                continue
            for oid, bibcode in zip(
                table.column("oidref").to_pylist(), table.column("bibcode").to_pylist(), strict=True
            ):
                if bibcode:
                    found.setdefault(int(oid), set()).add(str(bibcode).strip())
        # by_position keeps the first max_bibcodes in bibcode order
        return {oid: tuple(sorted(codes)[: self.max_bibcodes]) for oid, codes in found.items()}

    def _bibcodes(self, main_id: str) -> tuple[str, ...]:
        table = run_sync(self.transport, self.bibcode_request(main_id), "SIMBAD bibliography")
        if "bibcode" not in table.column_names:
            return ()
        return tuple(sorted({str(b).strip() for b in table.column("bibcode").to_pylist() if b}))


def _unit(positions: Sequence[tuple[float, float]]) -> np.ndarray:
    ra = np.radians([p[0] for p in positions])
    dec = np.radians([p[1] for p in positions])
    return np.column_stack((np.cos(dec) * np.cos(ra), np.cos(dec) * np.sin(ra), np.sin(dec)))


def _separation_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    """Great-circle separation (haversine), as SIMBAD's DISTANCE gives it."""
    p1, p2 = math.radians(dec1), math.radians(dec2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(ra2 - ra1) / 2) ** 2
    return math.degrees(2 * math.asin(min(1.0, math.sqrt(h))))


def _cover(positions: Sequence[tuple[float, float]], radius_arcsec: float) -> Cone:
    """A cone holding every position plus the match radius, centred on their mean direction."""
    x, y, z = _unit(positions).mean(axis=0)
    ra = math.degrees(math.atan2(y, x)) % 360.0
    dec = math.degrees(math.atan2(z, math.hypot(x, y)))
    reach = max(_separation_deg(ra, dec, p[0], p[1]) for p in positions)
    return Cone(ra, dec, min(180.0, reach + radius_arcsec / 3600.0 + 1e-6))


def _boxes(cone: Cone) -> list[tuple[float, float, float, float]]:
    """The RA/Dec boxes that hold a cone, split where its RA range crosses 0/360."""
    ra_range, dec_min, dec_max = adql.cone_bounds(cone)
    if ra_range is None:
        return [(0.0, 360.0, dec_min, dec_max)]
    lo, hi = ra_range
    if lo < 0.0:
        return [(lo + 360.0, 360.0, dec_min, dec_max), (0.0, hi, dec_min, dec_max)]
    if hi >= 360.0:
        return [(lo, 360.0, dec_min, dec_max), (0.0, hi - 360.0, dec_min, dec_max)]
    return [(lo, hi, dec_min, dec_max)]


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

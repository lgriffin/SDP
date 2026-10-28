"""Capability discovery from the Virtual Observatory (Integration v0.4.0 §2, ADR-017, Stage C).

A descriptor is read, not written, wherever the source publishes the standards: the RegTAP record
gives identity, rights and the temporal and spectral extent; the service's VOSI ``capabilities``
gives protocols, data models, row limits and upload; the ObsCore view gives product types,
missions, instruments, access and its own extent. Every request goes through the archive
``Transport`` (D31), so CI replays recordings. A native service that publishes none of this is
described by hand in the same schema and loaded with :func:`load_descriptors`.

``CapabilityBackedSourceRegistry`` answers the Investigate ``SourceRegistry`` question from the
registered descriptors, so a stage only ever finds services SDP has admitted.
"""

from __future__ import annotations

import json
import logging
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from sdp.adapters.archives.registry import REGTAP, bounding_cone
from sdp.adapters.archives.transport import HttpRequest, Transport, TransportError
from sdp.adapters.archives.untrusted import UntrustedInput
from sdp.adapters.archives.vo import literal, run_sync, sync_request
from sdp.kernel.domain.integration import (
    AccessStatus,
    AdapterInfo,
    CapabilityMatch,
    Coverage,
    EvidenceRequirement,
    Operation,
    ProductType,
    ProtocolEndpoint,
    Rights,
    ServiceLimits,
    SourceCapabilityDescriptor,
    match_requirement,
    order_matches,
)
from sdp.kernel.domain.investigation import Field
from sdp.kernel.ports.integration import CapabilityRegistry
from sdp.kernel.ports.investigate import ServiceRecord

log = logging.getLogger("sdp.adapters.capabilities")

MAX_CAPABILITIES_BYTES = 4 * 1024**2
H_C_J_M = 6.62607015e-34 * 299792458.0
"""Planck's constant times the speed of light: RegTAP gives spectral extent as photon energy in J."""

#: IVOA standard identifiers (without fragment) and the names descriptors use; VOSI ones are skipped.
STANDARD_NAMES = {
    "ivo://ivoa.net/std/tap": "TAP",
    "ivo://ivoa.net/std/sia": "SIA2",
    "ivo://ivoa.net/std/ssa": "SSA",
    "ivo://ivoa.net/std/conesearch": "SCS",
    "ivo://ivoa.net/std/datalink": "DataLink",
    "ivo://ivoa.net/std/soda": "SODA",
}

#: The ``SourceRegistry`` standard names Investigate uses, and the descriptor protocol behind each.
REGISTRY_STANDARDS = {
    "tap": "TAP",
    "table": "TAP",
    "sia": "SIA2",
    "image": "SIA2",
    "ssa": "SSA",
    "spectrum": "SSA",
    "conesearch": "SCS",
    "scs": "SCS",
}

#: ObsCore ``dataproduct_type`` values SDP models; others (visibility, sed, event) are not claimed.
OBSCORE_PRODUCT_TYPES = {
    "image": ProductType.IMAGE,
    "spectrum": ProductType.SPECTRUM,
    "cube": ProductType.CUBE,
    "timeseries": ProductType.TIMESERIES,
    "measurements": ProductType.MEASUREMENTS,
}

UPLOAD_METHOD = "ivo://ivoa.net/std/tapregext#upload"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(element: ET.Element, name: str) -> ET.Element | None:
    return next((c for c in element if _local(c.tag) == name), None)


def _children(element: ET.Element, name: str) -> list[ET.Element]:
    return [c for c in element if _local(c.tag) == name]


def _int(text: str | None) -> int | None:
    try:
        return int(str(text).strip()) if text is not None else None
    except ValueError:
        return None


@dataclass(frozen=True)
class VosiCapabilities:
    """What a service's VOSI ``capabilities`` document declares."""

    protocols: tuple[ProtocolEndpoint, ...]
    data_models: tuple[str, ...]
    default_rows: int | None
    hard_rows: int | None
    upload: bool


def parse_capabilities(body: bytes) -> VosiCapabilities:
    """Protocols with versions and URLs, TAPRegExt data models, output limits and upload methods."""
    if len(body) > MAX_CAPABILITIES_BYTES:
        raise UntrustedInput(f"capabilities document of {len(body)} bytes exceeds {MAX_CAPABILITIES_BYTES}")
    head = body[:65536].upper()
    if b"<!DOCTYPE" in head or b"<!ENTITY" in head:
        raise UntrustedInput("VOSI capabilities carry no DTD; refusing a document that declares one")
    root = ET.fromstring(body)
    protocols: list[ProtocolEndpoint] = []
    models: list[str] = []
    default_rows = hard_rows = None
    upload = False
    for cap in root.iter():
        if _local(cap.tag) != "capability":
            continue
        standard_id = (cap.get("standardID") or "").strip()
        base, _, fragment = standard_id.lower().partition("#")
        name = STANDARD_NAMES.get(base)
        if name is None:
            continue
        for intf in _children(cap, "interface"):
            if intf.get("role", "std") != "std" and name == "TAP":
                continue
            url_element = _child(intf, "accessURL")
            url = (url_element.text or "").strip() if url_element is not None else ""
            if not url:
                continue
            version = intf.get("version") or (fragment.rsplit("-", 1)[-1] if "-" in fragment else "unknown")
            protocols.append(ProtocolEndpoint(name, version.strip(), url))
        if name == "TAP":
            for dm in _children(cap, "dataModel"):
                if dm.text and dm.text.strip():
                    models.append(dm.text.strip())
            limit = _child(cap, "outputLimit")
            if limit is not None:
                d, h = _child(limit, "default"), _child(limit, "hard")
                if d is not None and d.get("unit", "row") == "row":
                    default_rows = _int(d.text)
                if h is not None and h.get("unit", "row") == "row":
                    hard_rows = _int(h.text)
            upload = upload or any(
                (u.get("ivo-id") or "").lower().startswith(UPLOAD_METHOD) for u in _children(cap, "uploadMethod")
            )
    unique = sorted(set(protocols), key=lambda p: (p.standard, p.base_url, p.version))
    return VosiCapabilities(tuple(unique), tuple(sorted(set(models))), default_rows, hard_rows, upload)


@dataclass(frozen=True)
class VoSource:
    """A source SDP reads its descriptor for: its registry id and ObsTAP service, plus what only
    SDP knows (the adapter that serves it) and the cited fallback where the VO is silent."""

    source_id: str
    ivoid: str
    obscore_url: str
    rights: Rights
    adapter: AdapterInfo | None = None
    obscore_table: str = "ivoa.obscore"
    collections: tuple[str, ...] = ()
    """When set, the ObsCore scans read only these ``obs_collection`` values: a whole-table scan of a
    large archive (MAST) times out, and SDP only uses the collections it names."""
    rights_column: str | None = "data_rights"
    """ObsCore's optional ``data_rights`` column; None for a service without it (MAST's view has
    none, so access is read per product from its inventory)."""

    def collection_filter(self) -> str:
        if not self.collections:
            return ""
        return f" WHERE obs_collection IN ({', '.join(literal(c) for c in self.collections)})"


@dataclass
class VoCapabilityDiscovery:
    """``CapabilityDiscoveryService`` over RegTAP, VOSI and ObsCore, through a Transport (ADR-017)."""

    transport: Transport
    registry: CapabilityRegistry
    sources: Mapping[str, VoSource] = field(default_factory=dict)
    today: Callable[[], date] = date.today
    regtap_url: str = REGTAP

    # -- the requests, separate so cassettes and fakes key on exactly what is sent

    def identity_request(self, source: VoSource) -> HttpRequest:
        # ADQL has no scalar subqueries in a select list (GAVO's parser refuses them): join and group.
        adql = (
            "SELECT r.ivoid, r.res_title, r.rights, p.role_name AS publisher, "
            "MIN(t.time_start) AS t_min, MAX(t.time_end) AS t_max, "
            "MIN(s.spectral_start) AS e_min, MAX(s.spectral_end) AS e_max "
            "FROM rr.resource AS r "
            "LEFT OUTER JOIN rr.res_role AS p ON p.ivoid = r.ivoid AND p.base_role = 'publisher' "
            "LEFT OUTER JOIN rr.stc_temporal AS t ON t.ivoid = r.ivoid "
            "LEFT OUTER JOIN rr.stc_spectral AS s ON s.ivoid = r.ivoid "
            f"WHERE r.ivoid = {literal(source.ivoid.lower())} "
            "GROUP BY r.ivoid, r.res_title, r.rights, p.role_name"
        )
        return sync_request(self.regtap_url, adql, 10)

    def capabilities_request(self, source: VoSource) -> HttpRequest:
        return HttpRequest("GET", f"{source.obscore_url}/capabilities")

    def products_request(self, source: VoSource) -> HttpRequest:
        columns = ["dataproduct_type", "obs_collection", "instrument_name"]
        if source.rights_column:
            columns.append(source.rights_column)
        columns.append("calib_level")
        adql = f"SELECT DISTINCT {', '.join(columns)} FROM {source.obscore_table}{source.collection_filter()}"
        return sync_request(source.obscore_url, adql, 100000)

    def extent_request(self, source: VoSource) -> HttpRequest:
        adql = (
            "SELECT MIN(t_min) AS t_min, MAX(t_max) AS t_max, MIN(em_min) AS em_min, MAX(em_max) AS em_max "
            f"FROM {source.obscore_table}{source.collection_filter()}"
        )
        return sync_request(source.obscore_url, adql, 1)

    # -- the port

    def describe(self, source_id: str) -> SourceCapabilityDescriptor:
        source = self.sources.get(source_id)
        if source is None:
            found = self.registry.get(source_id)
            if found is None:
                raise KeyError(f"no VO source or registered descriptor for {source_id}")
            return found
        return self._read(source)

    def refresh(self, source_id: str) -> SourceCapabilityDescriptor:
        """Read the descriptor again and replace the registered one (the weekly freshness job)."""
        descriptor = self.describe(source_id)
        self.registry.put(descriptor)
        return descriptor

    def match(self, field: Field, need: EvidenceRequirement) -> tuple[CapabilityMatch, ...]:
        region = field.query_region()
        return order_matches(match_requirement(d, region, need) for d in self.registry.all())

    # -- reading

    def _read(self, source: VoSource) -> SourceCapabilityDescriptor:
        identity = run_sync(self.transport, self.identity_request(source), f"RegTAP record {source.ivoid}").to_pylist()
        if not identity:
            raise LookupError(f"the registry has no record for {source.ivoid}")
        record = identity[0]
        response = self.transport.send(self.capabilities_request(source))
        if response.status != 200:
            raise TransportError(f"VOSI capabilities of {source.source_id}: HTTP {response.status}", response.status)
        vosi = parse_capabilities(response.body)
        rows = run_sync(self.transport, self.products_request(source), f"ObsCore products of {source.source_id}")
        products = rows.to_pylist()
        try:
            extent = run_sync(self.transport, self.extent_request(source), f"ObsCore extent of {source.source_id}")
            ext = extent.to_pylist()[0] if extent.num_rows else {}
        except TransportError as exc:
            # An aggregate over a large archive can time out (MAST answers 504); the registry's
            # coverage stands in, as it does for any bound the service leaves null.
            log.warning(
                "ObsCore extent unavailable; using the registry's",
                extra={"source": source.source_id, "error": str(exc)},
            )
            ext = {}

        types = frozenset(
            OBSCORE_PRODUCT_TYPES[t]
            for t in (str(r.get("dataproduct_type") or "").strip().lower() for r in products)
            if t in OBSCORE_PRODUCT_TYPES
        )
        missions = tuple(sorted({str(r["obs_collection"]).strip() for r in products if r.get("obs_collection")}))
        instruments = tuple(sorted({str(r["instrument_name"]).strip() for r in products if r.get("instrument_name")}))
        levels = frozenset(
            level for level in (_calibration_level(r.get("calib_level")) for r in products) if level is not None
        )
        rights_seen = (
            {str(r.get(source.rights_column) or "").strip().lower() for r in products}
            if source.rights_column
            else set()
        )
        # Public as soon as any product is; a service that only serves proprietary data says so.
        restricted = bool(rights_seen & {"proprietary", "secure"})
        access = AccessStatus.EXCLUSIVE_ACCESS if restricted and "public" not in rights_seen else AccessStatus.PUBLIC
        names = {p.standard for p in vosi.protocols}
        operations = set()
        if names & {"TAP", "SIA2", "SCS", "SSA"}:
            operations |= {Operation.CONE, Operation.REGION}
        if "TAP" in names:
            operations.add(Operation.CRITERIA)
        if types:
            operations |= {Operation.PRODUCT_LISTING, Operation.DOWNLOAD}
        if "SODA" in names:
            operations.add(Operation.CUTOUT)
        cutout = "soda" if "SODA" in names else ("per-product" if "DataLink" in names else "none")
        licence = str(record.get("rights") or "").strip() or source.rights.licence

        return SourceCapabilityDescriptor(
            source_id=source.source_id,
            title=str(record.get("res_title") or source.source_id).strip(),
            publisher=str(record.get("publisher") or "unknown").strip(),
            verified_on=self.today(),
            origin="registry",
            ivoid=str(record.get("ivoid") or source.ivoid).strip(),
            protocols=vosi.protocols,
            data_models=vosi.data_models,
            product_types=types,
            missions=missions,
            instruments=instruments,
            calibration_levels=levels,
            coverage=Coverage(
                all_sky=None,
                t_min_mjd=_first_number(ext.get("t_min"), record.get("t_min")),
                t_max_mjd=_first_number(ext.get("t_max"), record.get("t_max")),
                em_min_m=_first_number(ext.get("em_min"), _energy_to_wavelength(record.get("e_max"))),
                em_max_m=_first_number(ext.get("em_max"), _energy_to_wavelength(record.get("e_min"))),
            ),
            operations=frozenset(operations),
            cutout_support={t.value: cutout for t in types},
            access=access,
            limits=ServiceLimits(max_rows=vosi.hard_rows, page_size=vosi.default_rows, upload=vosi.upload),
            rights=Rights(licence, source.rights.citation, source.rights.acknowledgement),
            adapter=source.adapter,
        )


def _calibration_level(value: object) -> int | None:
    """An ObsCore ``calib_level`` (0 to 4), or None for a missing or masked value."""
    number = _number(value)
    return int(number) if number is not None and number.is_integer() and 0 <= number <= 4 else None


def _number(value: object) -> float | None:
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


def _first_number(*values: object) -> float | None:
    for v in values:
        n = _number(v)
        if n is not None:
            return n
    return None


def _energy_to_wavelength(joules: object) -> float | None:
    e = _number(joules)
    return H_C_J_M / e if e and e > 0 else None


def load_descriptors(root: Path) -> list[SourceCapabilityDescriptor]:
    """Hand-written descriptors (``capabilities/descriptors/*.json``), in source-id order."""
    if not root.is_dir():
        return []
    found = [
        SourceCapabilityDescriptor.from_document(json.loads(p.read_text("utf-8"))) for p in sorted(root.glob("*.json"))
    ]
    return sorted(found, key=lambda d: d.source_id)


@dataclass
class CapabilityBackedSourceRegistry:
    """The Investigate ``SourceRegistry`` answered from registered descriptors: only VO sources SDP has
    a descriptor for, and only those whose coverage does not exclude the field's centre (unknown
    coverage is kept, as the stage then finds out by asking)."""

    registry: CapabilityRegistry

    def services(self, field: Field, standard: str) -> Sequence[ServiceRecord]:
        key = standard.strip().lower()
        if key not in REGISTRY_STANDARDS:
            raise ValueError(f"unknown VO standard {standard!r}; expected one of {sorted(REGISTRY_STANDARDS)}")
        wanted = REGISTRY_STANDARDS[key]
        cone = bounding_cone(field)
        out: list[ServiceRecord] = []
        for d in self.registry.all():
            if d.ivoid is None:
                continue  # a hand-written native service is not a VO service
            if d.coverage.covers_position(cone.ra_deg, cone.dec_deg) is False:
                continue
            for p in d.protocols:
                if p.standard == wanted:
                    out.append(ServiceRecord(d.ivoid, d.title, key, p.base_url))
        return sorted(out, key=lambda s: (s.ivoid, s.access_url))

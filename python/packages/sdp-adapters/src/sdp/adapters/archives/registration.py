"""Register the descriptors a live deployment plans against (Integration v0.4.0 §2, Stage C).

The planner matches a field only against registered descriptors, so a fresh database plans nothing
until this has run once (``sdp integration register``). MAST is read from the VO: its registry
record, VOSI capabilities and ObsCore. The catalogue sources SDP ships (Gaia, 2MASS, Pan-STARRS)
are described by hand from their packaged source files, as native TAP services without ObsCore, and
so are Gaia's DataLink products (epoch photometry and BP/RP spectra), which no registry record lists.
No descriptor claims an admitted adapter: conformance comes only from compatibility records, so
their evidence feeds exploratory runs and no verdict until one is admitted (INT-016).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date

from sdp.adapters.archives.capabilities import VoCapabilityDiscovery
from sdp.adapters.archives.reference_recording import Unregistered, mast_ivoid, mast_source
from sdp.adapters.archives.tap import TapSource, load_sources
from sdp.adapters.archives.transport import Transport
from sdp.adapters.gaia_dr3 import ACKNOWLEDGEMENT, DATALINK, DR3_SPAN_MJD, GAIA_BAND_M, GAIA_TAP, LICENCE
from sdp.adapters.gaia_dr3 import CITATION as GAIA_CITATION
from sdp.kernel.domain.integration import (
    AdapterInfo,
    Coverage,
    Operation,
    ProductType,
    ProtocolEndpoint,
    Rights,
    ServiceLimits,
    SourceCapabilityDescriptor,
)
from sdp.kernel.ports.integration import CapabilityRegistry

#: The adapter that serves every packaged catalogue source, at the version its descriptors name.
TAP_ADAPTER = ("tap-archive", "1.0.0")
#: The adapter that serves Gaia's DataLink products (bootstrap admits its products under the same name).
GAIA_DATALINK = ("gaia-datalink", "1.0.0")


def catalogue_descriptor(source: TapSource, today: date) -> SourceCapabilityDescriptor:
    """A packaged TAP catalogue as a hand-written descriptor: cone and region queries over one table.
    Its sky coverage is not stated in the source file, so it stays unknown (partial when matched)."""
    d = source.descriptor
    name, version = TAP_ADAPTER
    return SourceCapabilityDescriptor(
        source_id=d.source_id,
        title=f"{d.release_label} ({d.table})",
        publisher=d.citation,
        verified_on=today,
        origin="hand-written",
        protocols=(ProtocolEndpoint("TAP", "1.1", source.service_url),),
        product_types=frozenset({ProductType.CATALOGUE}),
        coverage=Coverage(),
        operations=frozenset({Operation.CONE, Operation.REGION, Operation.DOWNLOAD}),
        limits=ServiceLimits(max_rows=d.max_rows_per_call, requests_per_second=1.0 / d.min_seconds_between_calls),
        rights=Rights(d.licence or "unknown", d.citation),
        adapter=AdapterInfo(name, version),
    )


def gaia_datalink_descriptor(today: date) -> SourceCapabilityDescriptor:
    """Gaia DR3's DataLink products: sources found by a TAP cone, their light curves and spectra
    downloaded through DataLink. All sky, over DR3's span and the Gaia passbands."""
    name, version = GAIA_DATALINK
    return SourceCapabilityDescriptor(
        source_id="gaia-datalink",
        title="Gaia DR3 DataLink products (epoch photometry, BP/RP spectra)",
        publisher="ESA/Gaia/DPAC",
        verified_on=today,
        origin="hand-written",
        protocols=(ProtocolEndpoint("TAP", "1.1", GAIA_TAP), ProtocolEndpoint("DataLink", "1.0", DATALINK)),
        product_types=frozenset({ProductType.TIMESERIES, ProductType.SPECTRUM}),
        coverage=Coverage(
            all_sky=True,
            t_min_mjd=DR3_SPAN_MJD[0],
            t_max_mjd=DR3_SPAN_MJD[1],
            em_min_m=GAIA_BAND_M[0],
            em_max_m=GAIA_BAND_M[1],
        ),
        operations=frozenset({Operation.CONE, Operation.DOWNLOAD}),
        instruments=("Astro", "BP", "RP"),
        calibration_levels=frozenset({2, 3}),
        limits=ServiceLimits(max_rows=2000, requests_per_second=1.0),
        rights=Rights(LICENCE, GAIA_CITATION, ACKNOWLEDGEMENT),
        adapter=AdapterInfo(name, version),
    )


def register_defaults(
    registry: CapabilityRegistry,
    transport_for: Callable[[str], Transport],
    *,
    sources: Mapping[str, TapSource] | None = None,
    today: Callable[[], date] = date.today,
    mast: bool = True,
) -> list[SourceCapabilityDescriptor]:
    """Register MAST (read from the VO), every packaged catalogue source and Gaia's DataLink
    products; returns what was
    registered. A newer descriptor supersedes the one before it and none is deleted (INT-018)."""
    registered = []
    for source in (sources if sources is not None else load_sources()).values():
        descriptor = catalogue_descriptor(source, today())
        registry.put(descriptor)
        registered.append(descriptor)
    datalink = gaia_datalink_descriptor(today())
    registry.put(datalink)
    registered.append(datalink)
    if mast:
        transport = transport_for("mast")
        discovery = VoCapabilityDiscovery(transport, Unregistered(), {"mast": mast_source(mast_ivoid(transport))})
        discovery.today = today
        descriptor = discovery.describe("mast")
        registry.put(descriptor)
        registered.append(descriptor)
    return registered


def describe(d: SourceCapabilityDescriptor) -> dict[str, object]:
    """What ``sdp integration register`` prints and the evidence UI lists for one descriptor."""
    return {
        "source_id": d.source_id,
        "origin": d.origin,
        "verified_on": d.verified_on.isoformat(),
        "product_types": sorted(t.value for t in d.product_types),
        "operations": sorted(o.value for o in d.operations),
        "adapter": d.adapter.name if d.adapter else None,
        "admitted_for": sorted(t for t, ok in (d.adapter.conformance if d.adapter else {}).items() if ok),
    }


class RegisteredArchives:
    """The registry as sdp-api offers it: what is registered, and registering every source again
    (MAST read from the VO), so a new installation can plan from the app (INV-019)."""

    def __init__(
        self, registry: CapabilityRegistry, transport_for: Callable[[str], Transport], *, mast: bool = True
    ) -> None:
        self.registry = registry
        self.transport_for = transport_for
        self.mast = mast

    def registered(self) -> list[dict[str, object]]:
        return [describe(d) for d in sorted(self.registry.all(), key=lambda d: d.source_id)]

    def register(self) -> list[dict[str, object]]:
        return [describe(d) for d in register_defaults(self.registry, self.transport_for, mast=self.mast)]

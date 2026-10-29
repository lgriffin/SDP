"""Register the descriptors a live deployment plans against (Integration v0.4.0 §2, Stage C).

The planner matches a field only against registered descriptors, so a fresh database plans nothing
until this has run once (``sdp integration register``). MAST is read from the VO: its registry
record, VOSI capabilities and ObsCore. The catalogue sources SDP ships (Gaia, 2MASS, Pan-STARRS)
are described by hand from their packaged source files, as native TAP services without ObsCore.
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


def register_defaults(
    registry: CapabilityRegistry,
    transport_for: Callable[[str], Transport],
    *,
    sources: Mapping[str, TapSource] | None = None,
    today: Callable[[], date] = date.today,
    mast: bool = True,
) -> list[SourceCapabilityDescriptor]:
    """Register MAST (read from the VO) and every packaged catalogue source; returns what was
    registered. A newer descriptor supersedes the one before it and none is deleted (INT-018)."""
    registered = []
    for source in (sources if sources is not None else load_sources()).values():
        descriptor = catalogue_descriptor(source, today())
        registry.put(descriptor)
        registered.append(descriptor)
    if mast:
        transport = transport_for("mast")
        discovery = VoCapabilityDiscovery(transport, Unregistered(), {"mast": mast_source(mast_ivoid(transport))})
        discovery.today = today
        descriptor = discovery.describe("mast")
        registry.put(descriptor)
        registered.append(descriptor)
    return registered

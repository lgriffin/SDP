"""Stage C: capability descriptors read from RegTAP, VOSI and ObsCore over the Transport (INT-001,
INT-003, INT-004), and the Investigate ``SourceRegistry`` answered from them.

The VO services here are fakes primed with invented values (``vo_fakes``); the live shape is checked
by the weekly smoke, whose recordings replay through the same requests.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from sdp.adapters.archives.capabilities import (
    CapabilityBackedSourceRegistry,
    VoCapabilityDiscovery,
    VoSource,
    load_descriptors,
    parse_capabilities,
)
from sdp.adapters.archives.transport import CassetteTransport, Transport
from sdp.adapters.archives.untrusted import UntrustedInput
from sdp.contracts import validate
from sdp.kernel.domain.archive import Cone
from sdp.kernel.domain.integration import (
    AccessStatus,
    AdapterInfo,
    Coverage,
    MatchStatus,
    Operation,
    ProductType,
    ProtocolEndpoint,
    Rights,
)
from sdp.kernel.domain.investigation import Field
from sdp.testing.contracts.integration import CapabilityDiscoveryContract, DiscoveryCase
from sdp.testing.contracts.investigate import RegistryCase, SourceRegistryContract
from sdp.testing.fakes.integration import InMemoryCapabilityRegistry
from sdp.testing.integration import SYNTHETIC_FIELD, image_requirement, registry_descriptors

from .vo_fakes import PrimedService

pytestmark = pytest.mark.tier("T3")

TAP = "https://example.org/obscore"
SOURCE = VoSource(
    source_id="example-obscore",
    ivoid="ivo://example.org/obscore",
    obscore_url=TAP,
    rights=Rights("fallback licence", "Example Archive 2026", "Based on data from the Example Archive."),
    adapter=AdapterInfo("example-observations", "1.0.0"),
)
TODAY = date(2026, 10, 28)

CAPABILITIES = f"""<?xml version="1.0" encoding="UTF-8"?>
<vosi:capabilities xmlns:vosi="http://www.ivoa.net/xml/VOSICapabilities/v1.0"
    xmlns:vs="http://www.ivoa.net/xml/VODataService/v1.1" xmlns:tr="http://www.ivoa.net/xml/TAPRegExt/v1.0"
    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <capability standardID="ivo://ivoa.net/std/TAP" xsi:type="tr:TableAccess">
    <interface xsi:type="vs:ParamHTTP" role="std" version="1.1"><accessURL use="base">{TAP}</accessURL></interface>
    <dataModel ivo-id="ivo://ivoa.net/std/ObsCore#core-1.1">ObsCore-1.1</dataModel>
    <dataModel ivo-id="ivo://ivoa.net/std/CAOM#core-2.4">CAOM-2.4</dataModel>
    <language><name>ADQL</name></language>
    <outputLimit><default unit="row">10000</default><hard unit="row">500000</hard></outputLimit>
  </capability>
  <capability standardID="ivo://ivoa.net/std/VOSI#capabilities">
    <interface xsi:type="vs:ParamHTTP"><accessURL use="full">{TAP}/capabilities</accessURL></interface>
  </capability>
  <capability standardID="ivo://ivoa.net/std/DataLink#links-1.1">
    <interface xsi:type="vs:ParamHTTP" role="std"><accessURL use="base">{TAP}/links</accessURL></interface>
  </capability>
</vosi:capabilities>
""".encode()


def primed(fake: PrimedService, discovery: VoCapabilityDiscovery, source: VoSource = SOURCE) -> None:
    fake.table(
        discovery.identity_request(source),
        {
            "ivoid": [source.ivoid],
            "res_title": ["Example ObsCore"],
            "rights": ["https://example.org/data-policy"],
            "publisher": ["Example Archive"],
            "t_min": [40000.0],
            "t_max": [60000.0],
            "e_min": [1.0e-20],
            "e_max": [1.0e-18],
        },
    )
    fake.raw(discovery.capabilities_request(source), CAPABILITIES)
    fake.table(
        discovery.products_request(source),
        {
            "dataproduct_type": ["image", "image", "spectrum", "visibility", "cube"],
            "obs_collection": ["JWST", "HST", "JWST", "ALMA", "JWST"],
            "instrument_name": ["NIRCAM", "WFC3/IR", "NIRSPEC/MSA", "ALMA", "MIRI/IFU"],
            "data_rights": ["public", "public", "exclusive", "public", "public"],
        },
    )
    fake.table(
        discovery.extent_request(source),
        {"t_min": [50000.0], "t_max": [60500.0], "em_min": [6.0e-7], "em_max": [2.8e-5]},
    )


def discovery_on(transport: Transport, registry: InMemoryCapabilityRegistry | None = None) -> VoCapabilityDiscovery:
    return VoCapabilityDiscovery(
        transport, registry or InMemoryCapabilityRegistry(), {SOURCE.source_id: SOURCE}, today=lambda: TODAY
    )


@pytest.mark.req("INT-001")
def test_the_descriptor_is_read_from_the_registry_vosi_and_obscore() -> None:
    fake = PrimedService()
    discovery = discovery_on(fake)
    primed(fake, discovery)
    d = discovery.describe(SOURCE.source_id)
    assert (d.origin, d.ivoid, d.title, d.publisher, d.verified_on) == (
        "registry",
        "ivo://example.org/obscore",
        "Example ObsCore",
        "Example Archive",
        TODAY,
    )
    assert d.protocols == (
        ProtocolEndpoint("DataLink", "1.1", f"{TAP}/links"),
        ProtocolEndpoint("TAP", "1.1", TAP),
    )
    assert d.data_models == ("CAOM-2.4", "ObsCore-1.1")
    assert d.product_types == {ProductType.IMAGE, ProductType.SPECTRUM, ProductType.CUBE}, "visibility is not claimed"
    assert d.missions == ("ALMA", "HST", "JWST")
    assert "NIRSPEC/MSA" in d.instruments
    assert d.access is AccessStatus.PUBLIC
    assert (d.limits.max_rows, d.limits.page_size, d.limits.upload) == (500000, 10000, False)
    assert d.rights.licence == "https://example.org/data-policy" and d.rights.citation == "Example Archive 2026"
    assert d.adapter == SOURCE.adapter
    assert d.coverage.t_min_mjd == 50000.0 and d.coverage.em_max_m == 2.8e-5, "ObsCore's own extent wins"
    assert d.coverage.all_sky is None, "spatial coverage the service does not state stays unknown"
    assert validate("capability-descriptor", d.to_document()) == []


def test_a_large_archive_is_scanned_by_collection_and_its_extent_falls_back_to_the_registry() -> None:
    fake = PrimedService()
    source = replace(SOURCE, collections=("JWST",), rights_column=None)
    discovery = VoCapabilityDiscovery(
        fake, InMemoryCapabilityRegistry(), {source.source_id: source}, today=lambda: TODAY
    )
    primed(fake, discovery, source)
    fake.raw(discovery.extent_request(source), b"<html>504 Gateway Time-out</html>", status=504)
    products = discovery.products_request(source).body.decode()
    assert "obs_collection+IN+%28%27JWST%27%29" in products and "data_rights" not in products
    d = discovery.describe(source.source_id)
    assert d.coverage.t_min_mjd == 40000.0, "the registry's coverage stands in for the timed-out extent"
    assert d.access is AccessStatus.PUBLIC, "without a rights column, access is read per product"


@pytest.mark.req("INT-004")
def test_operations_and_cutouts_follow_the_declared_protocols() -> None:
    fake = PrimedService()
    discovery = discovery_on(fake)
    primed(fake, discovery)
    d = discovery.describe(SOURCE.source_id)
    assert d.operations == {
        Operation.CONE,
        Operation.REGION,
        Operation.CRITERIA,
        Operation.PRODUCT_LISTING,
        Operation.DOWNLOAD,
    }
    assert d.cutout_support == {"image": "per-product", "spectrum": "per-product", "cube": "per-product"}
    soda = CAPABILITIES.replace(
        b"</vosi:capabilities>",
        b'<capability standardID="ivo://ivoa.net/std/SODA#sync-1.0"><interface role="std">'
        b"<accessURL>https://example.org/soda</accessURL></interface></capability></vosi:capabilities>",
    )
    fake.raw(discovery.capabilities_request(SOURCE), soda)
    with_soda = discovery.describe(SOURCE.source_id)
    assert Operation.CUTOUT in with_soda.operations
    assert set(with_soda.cutout_support.values()) == {"soda"}


def test_registry_extent_is_used_where_obscore_gives_none() -> None:
    fake = PrimedService()
    discovery = discovery_on(fake)
    primed(fake, discovery)
    fake.table(discovery.extent_request(SOURCE), {k: [float("nan")] for k in ("t_min", "t_max", "em_min", "em_max")})
    c = discovery.describe(SOURCE.source_id).coverage
    assert (c.t_min_mjd, c.t_max_mjd) == (40000.0, 60000.0)
    hc = 6.62607015e-34 * 299792458.0
    assert c.em_min_m == pytest.approx(hc / 1.0e-18) and c.em_max_m == pytest.approx(hc / 1.0e-20)


def test_a_service_that_only_serves_proprietary_data_says_so() -> None:
    fake = PrimedService()
    discovery = discovery_on(fake)
    primed(fake, discovery)
    fake.table(
        discovery.products_request(SOURCE),
        {
            "dataproduct_type": ["image"],
            "obs_collection": ["JWST"],
            "instrument_name": ["NIRCAM"],
            "data_rights": ["proprietary"],
        },
    )
    assert discovery.describe(SOURCE.source_id).access is AccessStatus.EXCLUSIVE_ACCESS


@pytest.mark.req("INT-001")
def test_refresh_replaces_the_registered_descriptor() -> None:
    fake = PrimedService()
    registry = InMemoryCapabilityRegistry([replace(registry_descriptors()[0], source_id=SOURCE.source_id)])
    discovery = discovery_on(fake, registry)
    primed(fake, discovery)
    discovery.refresh(SOURCE.source_id)
    got = registry.get(SOURCE.source_id)
    assert got is not None and got.verified_on == TODAY and got.title == "Example ObsCore"


def test_an_unknown_source_falls_back_to_the_registry_or_raises() -> None:
    registry = InMemoryCapabilityRegistry(registry_descriptors())
    discovery = discovery_on(PrimedService(), registry)
    assert discovery.describe("synthetic-dasch").origin == "hand-written"
    with pytest.raises(KeyError):
        discovery.describe("nowhere")


def test_a_capabilities_document_with_a_dtd_is_refused() -> None:
    hostile = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><capabilities/>'
    with pytest.raises(UntrustedInput, match="DTD"):
        parse_capabilities(hostile)


@pytest.mark.req("INT-001")
def test_hand_written_descriptors_load_from_files(tmp_path: Path) -> None:
    for d in registry_descriptors():
        (tmp_path / f"{d.source_id}.json").write_text(json.dumps(d.to_document()))
    loaded = load_descriptors(tmp_path)
    assert [d.to_document() for d in loaded] == [
        d.to_document() for d in sorted(registry_descriptors(), key=lambda d: d.source_id)
    ]
    assert load_descriptors(tmp_path / "missing") == []


class TestVoCapabilityDiscovery(CapabilityDiscoveryContract):
    @pytest.fixture
    def discovery_case(self) -> DiscoveryCase:
        descriptors = registry_descriptors()
        return DiscoveryCase(
            discovery=discovery_on(PrimedService(), InMemoryCapabilityRegistry(descriptors)),
            field=SYNTHETIC_FIELD,
            need=image_requirement(),
            registered=tuple(d.source_id for d in descriptors),
            expected_satisfied="synthetic-mast",
        )


@pytest.mark.tier("T5")
@pytest.mark.req("INT-001")
def test_discovery_replays_from_cassettes(tmp_path: Path) -> None:
    fake = PrimedService()
    recorder = discovery_on(CassetteTransport(tmp_path, "record", inner=fake))
    primed(fake, recorder)
    recorded = recorder.describe(SOURCE.source_id)
    replayed = discovery_on(CassetteTransport(tmp_path, "replay")).describe(SOURCE.source_id)
    assert replayed.to_document() == recorded.to_document()


@pytest.mark.req("INT-003")
def test_a_discovered_descriptor_matches_a_jwst_image_requirement() -> None:
    fake = PrimedService()
    registry = InMemoryCapabilityRegistry()
    discovery = discovery_on(fake, registry)
    primed(fake, discovery)
    discovery.refresh(SOURCE.source_id)
    (m,) = discovery.match(SYNTHETIC_FIELD, image_requirement())
    assert m.source_id == SOURCE.source_id
    assert {r.field: r.outcome for r in m.reasons}["product_types"] is MatchStatus.SATISFIED


# ---------------------------------------------------------------------------------------------
# The Investigate SourceRegistry answered from descriptors


@pytest.mark.req("INT-017")
class TestCapabilityBackedSourceRegistry(SourceRegistryContract):
    @pytest.fixture
    def registry_case(self) -> RegistryCase:
        return RegistryCase(
            CapabilityBackedSourceRegistry(InMemoryCapabilityRegistry(registry_descriptors())), SYNTHETIC_FIELD, "TAP"
        )


@pytest.mark.req("INT-017")
def test_only_vo_sources_whose_coverage_allows_the_field_are_listed() -> None:
    mast, gaia, dasch = registry_descriptors()
    southern_only = replace(gaia, coverage=Coverage(all_sky=False, regions=(Cone(0.0, -80.0, 5.0),)))
    registry = CapabilityBackedSourceRegistry(InMemoryCapabilityRegistry([mast, southern_only, dasch]))
    services = registry.services(SYNTHETIC_FIELD, "tap")
    assert [s.ivoid for s in services] == ["ivo://synthetic/mast/caom"]
    assert registry.services(Field(Cone(10.0, -80.0, 0.1)), "tap")[1].ivoid == "ivo://synthetic/mast/caom"
    assert registry.services(SYNTHETIC_FIELD, "ssa") == []
    with pytest.raises(ValueError, match="unknown VO standard"):
        registry.services(SYNTHETIC_FIELD, "carrier-pigeon")

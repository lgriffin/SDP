"""T3/T5: SIMBAD and the VO registry behind ObjectResolver and SourceRegistry pass the port contracts
against primed fakes of the services, and again replayed from cassettes the record mode wrote."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs

import pyarrow as pa
import pytest

from sdp.adapters.archives.registry import RegTapRegistry, bounding_cone
from sdp.adapters.archives.simbad import SimbadResolver, parse_sesame
from sdp.adapters.archives.transport import CassetteMissing, CassetteTransport, Transport, TransportError
from sdp.adapters.archives.votable import TapQueryError
from sdp.kernel.domain.archive import Box, Cone
from sdp.kernel.domain.investigation import Field
from sdp.testing.contracts.investigate import (
    ObjectResolverContract,
    RegistryCase,
    ResolverCase,
    SourceRegistryContract,
)

from .vo_fakes import PrimedService, sesame

pytestmark = pytest.mark.tier("T3")

# Invented objects and services, for exercising the adapters only.
KNOWN = (150.0, 2.0)
EMPTY = (151.0, 3.0)
NAME = "SDP Fixture A"
BIBCODES = ["2001FAKE....1..1A", "1999FAKE....2..2B"]
FIELD = Field(Cone(150.0, 2.0, 0.2))


def primed_simbad(fake: PrimedService, resolver: SimbadResolver) -> None:
    fake.table(
        resolver.position_request(*KNOWN, 5.0),
        {
            "oid": [7],
            "main_id": [NAME],
            "ra": [KNOWN[0]],
            "dec": [KNOWN[1] + 0.5 / 3600],
            "otype": ["*"],
            "dist": [0.5 / 3600],
        },
    )
    fake.table(
        resolver.position_request(*EMPTY, 1.0),
        {"oid": pa.array([], pa.int64()), "main_id": pa.array([], pa.string()), "dist": pa.array([], pa.float64())},
    )
    fake.raw(resolver.name_request(NAME), sesame(NAME, "*", *KNOWN))
    fake.table(resolver.bibcode_request(NAME), {"bibcode": BIBCODES})


def primed_registry(fake: PrimedService, registry: RegTapRegistry) -> None:
    fake.table(
        registry.request(FIELD, "tap"),
        {
            "ivoid": ["ivo://example.org/tap", "ivo://example.org/tap", "ivo://example.net/other"],
            "res_title": ["Example TAP", "Example TAP", "Other TAP"],
            "standard_id": ["ivo://ivoa.net/std/tap"] * 3,
            "access_url": ["https://example.org/tap", "https://example.org/tap", "https://example.net/tap"],
            "waveband": ["optical#infrared", "optical#infrared", ""],
        },
    )


def resolver_case(transport: Transport) -> ResolverCase:
    return ResolverCase(SimbadResolver(transport), NAME, *KNOWN, *EMPTY)


class TestSimbadResolver(ObjectResolverContract):
    @pytest.fixture
    def resolver_case(self) -> ResolverCase:
        fake = PrimedService()
        primed_simbad(fake, SimbadResolver(fake))
        return resolver_case(fake)


class TestRegTapRegistry(SourceRegistryContract):
    @pytest.fixture
    def registry_case(self) -> RegistryCase:
        fake = PrimedService()
        registry = RegTapRegistry(fake)
        primed_registry(fake, registry)
        return RegistryCase(registry, FIELD, "TAP")


@pytest.mark.tier("T5")
@pytest.mark.req("INV-004")
class TestSimbadFromCassettes(ObjectResolverContract):
    @pytest.fixture
    def resolver_case(self, tmp_path: Path) -> ResolverCase:
        fake = PrimedService()
        recorder = SimbadResolver(CassetteTransport(tmp_path, "record", inner=fake))
        primed_simbad(fake, recorder)
        recorder.by_position(*KNOWN, 5.0)
        recorder.by_position(*EMPTY, 1.0)
        recorder.by_name(NAME)
        return resolver_case(CassetteTransport(tmp_path, "replay"))


@pytest.mark.tier("T5")
class TestRegistryFromCassettes(SourceRegistryContract):
    @pytest.fixture
    def registry_case(self, tmp_path: Path) -> RegistryCase:
        fake = PrimedService()
        recorder = RegTapRegistry(CassetteTransport(tmp_path, "record", inner=fake))
        primed_registry(fake, recorder)
        recorder.services(FIELD, "tap")
        return RegistryCase(RegTapRegistry(CassetteTransport(tmp_path, "replay")), FIELD, "tap")


def query_of(request_body: bytes) -> str:
    return parse_qs(request_body.decode())["QUERY"][0]


def test_a_position_resolves_nearest_first_with_sorted_bibcodes_and_arcsec_separation() -> None:
    fake = PrimedService()
    resolver = SimbadResolver(fake)
    primed_simbad(fake, resolver)
    rec = resolver.by_position(*KNOWN, 5.0)
    assert rec is not None
    assert rec.main_id == NAME and rec.object_type == "*"
    assert rec.bibcodes == tuple(sorted(BIBCODES))
    assert rec.separation_arcsec == pytest.approx(0.5)
    text = query_of(resolver.position_request(*KNOWN, 5.0).body)
    assert "TOP 1" in text and "ORDER BY dist, oid" in text and "CIRCLE('ICRS', 150.0, 2.0, 0.001388" in text


def test_names_are_quoted_safely() -> None:
    resolver = SimbadResolver(PrimedService())
    assert "b.main_id = 'O''Brien''s star'" in query_of(resolver.bibcode_request("O'Brien's star").body)
    assert resolver.name_request(" NGC 6522 ").url.endswith("/S?NGC%206522")


def test_sesame_without_a_match_resolves_to_nothing() -> None:
    assert parse_sesame(sesame(None)) is None
    fake = PrimedService()
    resolver = SimbadResolver(fake)
    fake.raw(resolver.name_request("nobody"), sesame(None))
    assert resolver.by_name("nobody") is None


def test_service_errors_raise_rather_than_resolve_to_nothing() -> None:
    fake = PrimedService()
    resolver = SimbadResolver(fake)
    fake.raw(resolver.position_request(*KNOWN, 5.0), b"busy", status=503)
    with pytest.raises(TransportError, match="HTTP 503"):
        resolver.by_position(*KNOWN, 5.0)
    error = (
        b'<?xml version="1.0"?><VOTABLE version="1.4" xmlns="http://www.ivoa.net/xml/VOTable/v1.3">'
        b'<RESOURCE type="results"><INFO name="QUERY_STATUS" value="ERROR">bad ADQL</INFO></RESOURCE></VOTABLE>'
    )
    fake.raw(resolver.position_request(*EMPTY, 1.0), error, content_type="application/x-votable+xml")
    with pytest.raises(TapQueryError, match="bad ADQL"):
        resolver.by_position(*EMPTY, 1.0)


def test_registry_deduplicates_interfaces_and_splits_wavebands() -> None:
    fake = PrimedService()
    registry = RegTapRegistry(fake)
    primed_registry(fake, registry)
    found = registry.services(FIELD, "tap")
    assert [s.ivoid for s in found] == ["ivo://example.org/tap", "ivo://example.net/other"]
    assert {s.waveband for s in found} == {("optical", "infrared"), ()}


def test_registry_query_matches_pyvos_regtap_shape() -> None:
    text = query_of(RegTapRegistry(PrimedService()).request(FIELD, "SIA").body)
    assert "c.standard_id LIKE 'ivo://ivoa.net/std/sia%'" in text
    assert "i.intf_role = 'std'" in text
    assert "1 = CONTAINS(MOC(6, CIRCLE(150.0, 2.0, 0.2)), coverage)" in text
    with pytest.raises(ValueError, match="unknown VO standard"):
        RegTapRegistry(PrimedService()).request(FIELD, "carrier-pigeon")


def test_a_box_field_is_searched_by_its_bounding_cone() -> None:
    cone = bounding_cone(Field(Box(10.0, 12.0, -1.0, 1.0)))
    assert (cone.ra_deg, cone.dec_deg) == (11.0, 0.0)
    assert cone.radius_deg == pytest.approx(2**0.5)


def test_replay_never_falls_back_to_the_network(tmp_path: Path) -> None:
    with pytest.raises(CassetteMissing):
        SimbadResolver(CassetteTransport(tmp_path, "replay")).by_name(NAME)

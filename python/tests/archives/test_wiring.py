"""The composition root gives every packaged source a gateway, on cassettes when asked (DSI-007)."""

from __future__ import annotations

from pathlib import Path

import pytest

from sdp.adapters.archives.transport import CassetteTransport, PoliteTransport
from sdp.adapters.bootstrap import archive_gateways, archive_transport, cross_match
from sdp.adapters.settings import Settings, SettingsError

pytestmark = pytest.mark.tier("T3")

ENV = {"SDP_DATABASE_URL": "postgresql://unused", "SDP_ARTIFACT_ROOT": "/tmp/unused"}


@pytest.mark.req("DSI-007")
def test_replay_mode_puts_every_gateway_on_cassettes(tmp_path: Path) -> None:
    settings = Settings.from_env({**ENV, "SDP_ARCHIVE_MODE": "replay", "SDP_CASSETTE_DIR": str(tmp_path)})
    gateways = archive_gateways(settings)
    assert set(gateways) == {"gaia", "2mass", "ps1"}
    for sid, gateway in gateways.items():
        transport = gateway.transport  # type: ignore[attr-defined]
        assert isinstance(transport, CassetteTransport) and transport.mode == "replay"
        assert transport.directory == tmp_path / sid
    assert isinstance(cross_match(settings).transport, CassetteTransport)


def test_live_mode_is_polite_and_record_mode_wraps_it(tmp_path: Path) -> None:
    live = Settings.from_env(ENV)
    assert isinstance(archive_transport(live, "gaia", 1.0), PoliteTransport)
    record = Settings.from_env({**ENV, "SDP_ARCHIVE_MODE": "record", "SDP_CASSETTE_DIR": str(tmp_path)})
    transport = archive_transport(record, "gaia", 1.0)
    assert isinstance(transport, CassetteTransport) and isinstance(transport.inner, PoliteTransport)


def test_an_unknown_archive_mode_is_refused() -> None:
    with pytest.raises(SettingsError, match="SDP_ARCHIVE_MODE"):
        Settings.from_env({**ENV, "SDP_ARCHIVE_MODE": "mock"})

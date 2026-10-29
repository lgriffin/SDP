"""DSI-009 on the live CDS X-Match service: the CrossMatchContract fixture, answered by the real service.

The live class sends the contract's requests to X-Match and records every answer; it runs only when
``SDP_XMATCH_RECORD`` names a directory (the Archive live smoke workflow and
``tools/record_live_data.py`` set it). The replay class runs the same contract on the recorded answers
in every CI run, and skips while ``tests/cassettes/xmatch`` has none. Recordings are never written by hand.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from sdp.adapters.archives.transport import (
    CassetteMissing,
    CassetteTransport,
    HttpRequest,
    HttpResponse,
    PoliteTransport,
    UrllibTransport,
)
from sdp.adapters.archives.xmatch import CdsXMatchAdapter
from sdp.testing.contracts.cross_match import CrossMatchContract

CASSETTES = Path(__file__).resolve().parents[1] / "cassettes" / "xmatch"
RECORD = os.environ.get("SDP_XMATCH_RECORD")


@dataclass
class _SkipWhenUnrecorded:
    inner: CassetteTransport

    def send(self, request: HttpRequest) -> HttpResponse:
        try:
            return self.inner.send(request)
        except CassetteMissing:
            pytest.skip("no recorded X-Match answer yet; run the Archive live smoke workflow")


@pytest.mark.tier("T3")
class TestCdsXMatchOnRecordedAnswers(CrossMatchContract):
    @pytest.fixture
    def cross_match(self) -> CdsXMatchAdapter:
        return CdsXMatchAdapter(_SkipWhenUnrecorded(CassetteTransport(CASSETTES, "replay")))


@pytest.mark.live
@pytest.mark.tier("T11")
@pytest.mark.skipif(not RECORD, reason="set SDP_XMATCH_RECORD to call the live X-Match service and record it")
class TestCdsXMatchLive(CrossMatchContract):
    @pytest.fixture
    def cross_match(self) -> CdsXMatchAdapter:
        live = PoliteTransport(UrllibTransport(), min_seconds_between_calls=1.0)
        return CdsXMatchAdapter(CassetteTransport(Path(str(RECORD)), "record", inner=live, recorder="live-smoke"))

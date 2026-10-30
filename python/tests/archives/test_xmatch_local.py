"""Cross-matching on this machine (DSI-018), and CDS X-Match falling back to it when CDS is down (DSI-019)."""

from __future__ import annotations

from dataclasses import dataclass, field

import astropy.units as u
import numpy as np
import pyarrow as pa
import pytest

from sdp.adapters.archives.transport import TransportError
from sdp.adapters.archives.xmatch import FallbackCrossMatch, LocalCrossMatch
from sdp.kernel.ports.archives import CrossMatchResult
from sdp.testing.contracts.cross_match import CrossMatchContract, frame
from sdp.testing.fakes.archives import AstropyCrossMatch

pytestmark = pytest.mark.tier("T3")


class TestLocalCrossMatch(CrossMatchContract):
    @pytest.fixture
    def cross_match(self) -> LocalCrossMatch:
        return LocalCrossMatch()


@pytest.mark.req("DSI-018")
def test_the_local_join_agrees_with_the_cpu_reference_on_a_crowded_field() -> None:
    rng = np.random.default_rng(7)
    ra, dec = rng.uniform(130.0, 130.5, 400), rng.uniform(19.5, 20.0, 400)
    left = frame(list(ra), list(dec), pmra=-36.0, pmdec=-12.9)
    jitter = rng.normal(0.0, 0.5 / 3600, (2, 400))
    right = frame(list(ra + jitter[0]), list(dec + jitter[1]), epoch=2000.0, pmra=-36.0, pmdec=-12.9)
    for epoch in (None, 2016.0 * u.yr):
        mine = LocalCrossMatch().match(left, right, 1.5 * u.arcsec, epoch=epoch)
        ref = AstropyCrossMatch().match(left, right, 1.5 * u.arcsec, epoch=epoch)
        assert list(mine.left_index) == list(ref.left_index)
        assert list(mine.right_index) == list(ref.right_index)
        np.testing.assert_allclose(mine.separation.to_value(u.arcsec), ref.separation.to_value(u.arcsec), atol=1e-6)


@dataclass
class Remote:
    """A stand-in for CDS X-Match: answers with a fixed result, or fails with the status it is given."""

    failure: TransportError | None = None
    calls: list[int] = field(default_factory=list)

    def match(
        self, left: pa.Table, right: pa.Table, radius: u.Quantity, *, epoch: u.Quantity | None = None
    ) -> CrossMatchResult:
        self.calls.append(left.num_rows)
        if self.failure:
            raise self.failure
        return CrossMatchResult(np.array([0]), np.array([0]), np.array([9.0]) * u.arcsec)


LEFT = frame([10.0], [0.0])
RIGHT = frame([10.0 + 0.5 / 3600], [0.0])


@pytest.mark.req("DSI-019")
@pytest.mark.parametrize("failure", [TransportError("timed out"), TransportError("X-Match: HTTP 503", 503)])
def test_when_cds_gives_no_answer_or_a_server_error_the_match_runs_here(failure: TransportError) -> None:
    remote = Remote(failure)
    result = FallbackCrossMatch(remote, LocalCrossMatch()).match(LEFT, RIGHT, 1 * u.arcsec)
    assert remote.calls == [1]
    assert result.separation[0].to_value(u.arcsec) == pytest.approx(0.5, abs=1e-3)


@pytest.mark.req("DSI-019")
def test_a_refusal_from_cds_is_raised_and_an_answer_is_used_as_given() -> None:
    with pytest.raises(TransportError, match="HTTP 400"):
        FallbackCrossMatch(Remote(TransportError("X-Match: HTTP 400", 400)), LocalCrossMatch()).match(
            LEFT, RIGHT, 1 * u.arcsec
        )
    answered = FallbackCrossMatch(Remote(), LocalCrossMatch()).match(LEFT, RIGHT, 1 * u.arcsec)
    assert answered.separation[0].to_value(u.arcsec) == 9.0

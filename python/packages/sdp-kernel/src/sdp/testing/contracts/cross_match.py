"""CrossMatchService contract on a fixture with known answers (DSI-009)."""

from __future__ import annotations

import astropy.units as u
import numpy as np
import pyarrow as pa
import pytest

from sdp.kernel.ports.archives import CrossMatchService


def frame(ra: list[float], dec: list[float], epoch: float = 2016.0, pmra: float = 0.0, pmdec: float = 0.0) -> pa.Table:
    n = len(ra)
    return pa.table(
        {
            "ra_deg": ra,
            "dec_deg": dec,
            "epoch_jyear": [epoch] * n,
            "pm_ra_cosdec_masyr": [pmra] * n,
            "pm_dec_masyr": [pmdec] * n,
        }
    )


class CrossMatchContract:
    @pytest.mark.req("DSI-009")
    def test_matches_within_radius_only(self, cross_match: CrossMatchService) -> None:
        left = frame([10.0, 20.0, 30.0], [0.0, 10.0, -10.0])
        right = frame([10.0 + 0.5 / 3600, 20.0 + 5.0 / 3600, 100.0], [0.0, 10.0, 0.0])
        result = cross_match.match(left, right, 1.0 * u.arcsec)
        assert list(result.left_index) == [0]
        assert list(result.right_index) == [0]
        assert result.separation[0].to_value(u.arcsec) == pytest.approx(0.5, abs=1e-3)

    def test_empty_inputs_match_nothing(self, cross_match: CrossMatchService) -> None:
        result = cross_match.match(frame([], []), frame([1.0], [1.0]), 1 * u.arcsec)
        assert len(result.left_index) == 0

    @pytest.mark.req("DSI-009")
    def test_epoch_propagation_uses_proper_motion(self, cross_match: CrossMatchService) -> None:
        # a star moving 1000 mas/yr in dec, catalogued at 2000 and at 2016: 16 arcsec apart
        old = frame([50.0], [20.0], epoch=2000.0, pmdec=1000.0)
        new = frame([50.0], [20.0 + 16.0 / 3600], epoch=2016.0, pmdec=1000.0)
        assert len(cross_match.match(old, new, 1 * u.arcsec).left_index) == 0
        result = cross_match.match(old, new, 1 * u.arcsec, epoch=2016.0 * u.yr)
        assert list(result.left_index) == [0]
        assert np.all(result.separation.to_value(u.arcsec) < 0.01)

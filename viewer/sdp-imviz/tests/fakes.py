"""A fake InvestigateClient and a small image with a TAN WCS, so the plugin runs without a kernel."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS

from sdp_imviz.client import InvestigateError, RunResult

CENTRE = (120.0, -10.0)
PROFILE = {"profile_id": "synthetic-comoving", "version": "1.0.0", "tests": [{"name": "pm-excess"}]}


def image_hdu(size: int = 200, scale_deg: float = 1 / 3600) -> fits.ImageHDU:
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = list(CENTRE)
    w.wcs.crpix = [size / 2 + 0.5, size / 2 + 0.5]
    w.wcs.cd = np.array([[-scale_deg, 0.0], [0.0, scale_deg]])
    data = np.random.default_rng(1).normal(100.0, 5.0, (size, size)).astype(np.float32)
    return fits.ImageHDU(data, header=w.to_header(), name="SCI")


def run_document(region: str, n: int = 3) -> dict[str, Any]:
    return {
        "set_id": "invset-000001",
        "field_id": "field-0123456789abcdef",
        "profile": "synthetic-comoving@1.0.0",
        "profile_hash": "sha256:" + "a" * 64,
        "label": "EXPLORATORY",
        "stages": {s: "completed" for s in ("identify", "retrieve", "test", "candidates")},
        "objects_examined": 60,
        "candidates": [
            {
                "investigation_id": f"inv-00000{i + 1}",
                "candidate": f"gaia:DR3:{i}",
                "score": 1.0 - i / 10,
                "state": "open",
                "label": "EXPLORATORY",
                "positions": [[CENTRE[0] + 0.005 * i, CENTRE[1] - 0.004 * i]],
            }
            for i in range(n)
        ],
        "region_seen": region,
    }


@dataclass
class FakeClient:
    """Records each call and answers with a fixed run; ``fail`` makes it refuse like sdp-api would."""

    calls: list[dict[str, Any]] = field(default_factory=list)
    fail: str | None = None

    def investigate(
        self, region: str, profile: Mapping[str, Any], *, seed: int = 0, label: str = "", epoch_jyear: float = 2016.0
    ) -> RunResult:
        self.calls.append({"region": region, "profile": dict(profile), "seed": seed, "label": label})
        if self.fail:
            raise InvestigateError(self.fail)
        return RunResult.from_document(run_document(region))

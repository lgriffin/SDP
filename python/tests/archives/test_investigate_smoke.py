"""T3: the Investigate live smoke names every failing service and DASCH column drift (DSI-008)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sdp.adapters.archives import investigate_smoke
from sdp.adapters.archives.dasch import DaschGateway
from sdp.adapters.archives.investigate_smoke import STAR, run_investigate_smoke
from sdp.adapters.archives.transport import HttpRequest, HttpResponse, Transport, TransportError

from .vo_fakes import PrimedService

pytestmark = pytest.mark.tier("T3")


class Down:
    def send(self, request: HttpRequest) -> HttpResponse:
        raise TransportError(f"{request.url}: connection refused")


def test_every_unreachable_service_is_named(tmp_path: Path) -> None:
    results = run_investigate_smoke(lambda _: Down(), tmp_path)
    assert [r.source_id for r in results] == ["simbad", "regtap", "mast", "dasch"]
    for r in results:
        assert r.problems and r.problems[0].startswith(f"{r.source_id}: ")


def dasch_with(columns: list[str], row: list[str]) -> PrimedService:
    fake = PrimedService()
    gateway = DaschGateway(fake)
    fake.raw(
        gateway.querycat_request(STAR.ra_deg, STAR.dec_deg, 10.0),
        json.dumps(
            ["ref_text,ref_number,gsc_bin_index,ra_deg,dec_deg", f"x,5,9,{STAR.ra_deg},{STAR.dec_deg}"]
        ).encode(),
    )
    fake.raw(gateway.lightcurve_request("apass", 9, 5), json.dumps([",".join(columns), ",".join(row)]).encode())
    return fake


@pytest.mark.req("DSI-008")
def test_dasch_column_drift_is_named() -> None:
    fake = dasch_with(["date_jd", "magcal_magdep"], ["2420000.5", "12.0"])
    problems = investigate_smoke.PROBES["dasch"](fake)
    assert problems and "lacks columns" in problems[0] and "drad_rms2" in problems[0]


def test_a_healthy_dasch_light_curve_passes() -> None:
    columns = sorted(investigate_smoke.LIGHTCURVE_COLUMNS)
    values = {
        "date_jd": "2420000.5",
        "magcal_magdep": "12.0",
        "magcal_magdep_rms": "0.1",
        "ra_deg": str(STAR.ra_deg),
        "dec_deg": str(STAR.dec_deg),
        "gsc_bin_index": "9",
        "sextractor_number": "3",
        "aflags": "0",
        "drad_rms2": "0.5",
    }
    fake: Transport = dasch_with(columns, [values[c] for c in columns])
    assert investigate_smoke.PROBES["dasch"](fake) == []

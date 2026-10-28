"""T4 for the plugin's core: the field it sends, the profiles it offers, and the HTTP client against a
local stand-in for sdp-api."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, ClassVar

import pytest
import yaml
from astropy.wcs import WCS

from sdp_imviz import HttpInvestigateClient, InvestigateError, circle_region, footprint_region, load_profiles

from .fakes import CENTRE, PROFILE, image_hdu, run_document

pytestmark = pytest.mark.tier("T4")


def test_the_footprint_is_the_visible_rectangle_clipped_to_the_image() -> None:
    wcs = WCS(image_hdu().header)
    region = footprint_region(wcs, -50, 250, -50, 250, 200, 200)
    assert region.startswith("icrs;polygon(")
    values = [float(v) for v in region[len("icrs;polygon(") : -1].split(",")]
    ras, decs = values[0::2], values[1::2]
    assert len(ras) == 4
    assert max(decs) - min(decs) == pytest.approx(200 / 3600, rel=1e-3)
    assert min(ras) < CENTRE[0] < max(ras) and min(decs) < CENTRE[1] < max(decs)
    zoomed = footprint_region(wcs, 90, 110, 90, 110, 200, 200)
    zdecs = [float(v) for v in zoomed[len("icrs;polygon(") : -1].split(",")][1::2]
    assert max(zdecs) - min(zdecs) == pytest.approx(20 / 3600, rel=1e-3)


def test_an_empty_view_and_a_bad_circle_are_refused() -> None:
    with pytest.raises(ValueError, match="no part"):
        footprint_region(WCS(image_hdu().header), 300, 400, 300, 400, 200, 200)
    with pytest.raises(ValueError):
        circle_region(1, 2, 0)
    assert circle_region(-1.0, 2.0, 0.5) == "icrs;circle(359.0000000,2.0000000,0.5000000)"


def test_profiles_are_read_by_name_and_version(tmp_path: Path) -> None:
    (tmp_path / "a.yaml").write_text(yaml.safe_dump(PROFILE))
    (tmp_path / "b.yml").write_text(yaml.safe_dump({**PROFILE, "version": "1.1.0"}))
    (tmp_path / "notes.yaml").write_text("just: notes\n")
    assert sorted(load_profiles(tmp_path)) == ["synthetic-comoving@1.0.0", "synthetic-comoving@1.1.0"]
    (tmp_path / "c.yaml").write_text(yaml.safe_dump(PROFILE))
    with pytest.raises(ValueError, match="twice"):
        load_profiles(tmp_path)


class _Api(BaseHTTPRequestHandler):
    status = 200
    seen: ClassVar[list[dict[str, Any]]] = []

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        type(self).seen.append({"path": self.path, "body": body, "type": self.headers["content-type"]})
        if self.status == 200:
            payload = run_document(body["region"])
        else:
            payload = {"error": "Investigate is not wired on this server"}
        data = json.dumps(payload).encode()
        self.send_response(self.status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture
def api() -> Iterator[str]:
    _Api.seen, _Api.status = [], 200
    server = HTTPServer(("127.0.0.1", 0), _Api)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.mark.req("INV-012")
def test_the_client_posts_the_field_and_profile_and_reads_the_run(api: str) -> None:
    result = HttpInvestigateClient(api).investigate("icrs;circle(120,-10,0.1)", PROFILE, seed=7, label="viewer")
    [request] = _Api.seen
    assert request["path"] == "/investigations" and request["type"] == "application/json"
    assert request["body"] == {
        "region": "icrs;circle(120,-10,0.1)",
        "profile": PROFILE,
        "seed": 7,
        "label": "viewer",
        "epoch_jyear": 2016.0,
    }
    assert result.label == "EXPLORATORY" and len(result.candidates) == 3
    assert result.rows()[0]["candidate"] == "gaia:DR3:0"


def test_a_refusal_carries_the_servers_message(api: str) -> None:
    _Api.status = 503
    with pytest.raises(InvestigateError, match="503: Investigate is not wired"):
        HttpInvestigateClient(api).investigate("icrs;circle(1,1,1)", PROFILE)


def test_an_unreachable_api_is_reported() -> None:
    with pytest.raises(InvestigateError, match="cannot reach"):
        HttpInvestigateClient("http://127.0.0.1:9", timeout_s=2).investigate("icrs;circle(1,1,1)", PROFILE)

"""T3: cutouts read by HTTP byte range, against a fake host that serves byte ranges of a synthetic
JWST-layout image (INT-004). The cut pixels are the source's pixels, unchanged, and only headers
and the rows the square covers cross the wire."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

import numpy as np
import pytest
from astropy.io import fits
from astropy.wcs import WCS

from sdp.adapters.archives import fits_range
from sdp.adapters.archives.fits_range import RangeUnsupported, range_cutout, range_cutout_cost, range_status
from sdp.adapters.archives.mast import MastObservationGateway, file_name, obs_id_of
from sdp.adapters.archives.transport import HttpRequest, HttpResponse, TransportError
from sdp.kernel.domain.errors import InvariantViolation
from sdp.kernel.domain.integration import CutoutMethod, CutoutSupport, cutout_support
from sdp.kernel.domain.investigation import ProductRef
from sdp.testing.jwst import JWST_CENTRE, PIXEL_ARCSEC, jwst_i2d

pytestmark = [pytest.mark.tier("T3"), pytest.mark.req("INT-004")]

URL = "https://archive.invalid/jw-synth_i2d.fits"


@dataclass
class RangeHost:
    """Serves byte ranges of one file (206), or ignores ranges like a host that cannot (200)."""

    data: bytes
    ranges: bool = True
    sent: int = 0
    requests: list[str] = field(default_factory=list)

    def send(self, request: HttpRequest) -> HttpResponse:
        header = request.headers.get("Range", "")
        self.requests.append(header)
        match = re.fullmatch(r"bytes=(\d+)-(\d+)", header)
        if not self.ranges or match is None:
            self.sent += len(self.data)
            return HttpResponse(200, self.data)
        start, end = int(match[1]), int(match[2])
        if start >= len(self.data):
            return HttpResponse(416, b"")
        body = self.data[start : end + 1]
        self.sent += len(body)
        return HttpResponse(206, body, {"Content-Range": f"bytes {start}-{start + len(body) - 1}/{len(self.data)}"})


def sci(data: bytes) -> tuple[np.ndarray, fits.Header]:
    with fits.open(io.BytesIO(data)) as hdus:
        return hdus["SCI"].data.copy(), hdus["SCI"].header.copy()


def test_a_cutout_is_the_source_pixels_around_the_position_and_reads_only_their_rows() -> None:
    source = jwst_i2d()
    host = RangeHost(source)
    ra, dec = JWST_CENTRE[0] + 2.0 / 3600, JWST_CENTRE[1] - 1.0 / 3600
    cut = range_cutout(host, URL, ra, dec, 4.0)
    full, full_header = sci(source)
    pixels, header = sci(cut.data)
    assert pixels.shape == (cut.height, cut.width)
    assert np.array_equal(pixels, full[cut.y0 : cut.y0 + cut.height, cut.x0 : cut.x0 + cut.width])
    assert abs(cut.width - 4.0 / PIXEL_ARCSEC) <= 2 and cut.pixel_arcsec == pytest.approx(PIXEL_ARCSEC)
    # The cut header's WCS puts every sky position on the same pixel as the source's did.
    x, y = WCS(header).world_to_pixel_values(ra, dec)
    fx, fy = WCS(full_header).world_to_pixel_values(ra, dec)
    assert (float(x) + cut.x0, float(y) + cut.y0) == pytest.approx((float(fx), float(fy)), abs=1e-6)
    assert header["SDPCUT"] == "range" and (header["SDPCUTX0"], header["SDPCUTY0"]) == (cut.x0, cut.y0)
    assert host.sent < len(source) / 2, "only headers and the covered rows are read"
    with fits.open(io.BytesIO(cut.data)) as hdus:
        assert hdus[0].header["INSTRUME"] == "NIRCAM", "the source's primary header is kept"


def test_a_host_that_ignores_ranges_or_a_position_off_the_image_cuts_nothing() -> None:
    with pytest.raises(RangeUnsupported, match="HTTP 200, not 206"):
        range_cutout(RangeHost(jwst_i2d(), ranges=False), URL, *JWST_CENTRE, 4.0)
    with pytest.raises(LookupError, match="off the image"):
        range_cutout(RangeHost(jwst_i2d()), URL, JWST_CENTRE[0] + 1.0, JWST_CENTRE[1], 4.0)


def test_a_cut_that_would_read_too_many_rows_is_refused() -> None:
    with pytest.raises(RangeUnsupported, match="bytes of rows"):
        range_cutout(RangeHost(jwst_i2d()), URL, *JWST_CENTRE, 10.0, max_read_bytes=1000)


def test_a_compressed_image_is_not_cut_by_range() -> None:
    data = np.zeros((16, 16), dtype=np.float32)
    hdus = fits.HDUList([fits.PrimaryHDU(), fits.CompImageHDU(data, name="SCI")])
    buffer = io.BytesIO()
    hdus.writeto(buffer)
    with pytest.raises(RangeUnsupported, match=r"tile-compressed|no uncompressed"):
        range_cutout(RangeHost(buffer.getvalue()), URL, 0.0, 0.0, 1.0)


def test_cutout_support_is_read_per_product() -> None:
    soda = cutout_support(
        [{"semantics": "#cutout", "service_def": "soda-sync", "access_url": "https://x.invalid/soda"}],
        "image/fits",
        None,
        URL,
    )
    assert (soda.method, soda.service_url) == (CutoutMethod.SODA, "https://x.invalid/soda")
    assert cutout_support((), "application/fits", range_status(RangeHost(jwst_i2d()), URL), URL).method is (
        CutoutMethod.RANGE
    )
    ignored = cutout_support((), "application/fits", range_status(RangeHost(jwst_i2d(), ranges=False), URL), URL)
    assert ignored.method is CutoutMethod.NONE and "HTTP 200" in ignored.evidence
    assert cutout_support((), "text/plain", 206, URL).method is CutoutMethod.NONE


def test_the_mast_gateway_cuts_a_listed_product_under_its_own_id() -> None:
    product = ProductRef("mast", "jw-synth-o001/jw-synth_i2d.fits", URL)
    got = MastObservationGateway(RangeHost(jwst_i2d())).cutout_product(product, *JWST_CENTRE, 4.0)
    assert got.product.product_id.startswith(product.product_id + "@cutout(")
    assert obs_id_of(got.product.product_id) == "jw-synth-o001" and file_name(got.read_from) == file_name(URL)
    assert got.media_type == "application/fits" and sci(got.data)[1]["SDPCUT"]


def test_cutout_support_states_its_evidence() -> None:
    with pytest.raises(InvariantViolation):
        CutoutSupport(CutoutMethod.RANGE, "")
    with pytest.raises(InvariantViolation):
        CutoutSupport(CutoutMethod.SODA, "DataLink #cutout row")
    assert not CutoutSupport(CutoutMethod.NONE, "no #cutout row").supported


@pytest.mark.req("INT-025")
def test_a_cutout_cost_is_what_the_cut_reads_from_the_headers_alone() -> None:
    source = jwst_i2d()
    ra, dec = JWST_CENTRE[0] + 2.0 / 3600, JWST_CENTRE[1] - 1.0 / 3600
    probe = RangeHost(source)
    cost = range_cutout_cost(probe, URL, ra, dec, 4.0)
    host = RangeHost(source)
    cut = range_cutout(host, URL, ra, dec, 4.0)
    assert probe.sent < 3 * 28_800, "the probe reads headers, not pixels"
    assert cost.read_bytes == host.sent, "a cutout is costed at every byte it reads, full-width rows included"
    assert cost.read_bytes > len(cut.data), "a range cutout reads more than it keeps"
    assert len(cut.data) <= cost.stored_bytes <= len(cut.data) + 3 * 2880
    assert cost.coverage == pytest.approx(1.0)


@pytest.mark.req("INT-025")
def test_a_square_running_off_the_image_says_how_much_of_it_the_image_holds() -> None:
    source = jwst_i2d()
    _, header = sci(source)
    ra, dec = (float(v) for v in WCS(header).pixel_to_world_values(0, header["NAXIS2"] / 2))
    cost = range_cutout_cost(RangeHost(source), URL, ra, dec, 4.0)
    assert 0.4 < cost.coverage < 0.6, "a square centred on the left edge is about half on the image"
    with pytest.raises(LookupError, match="off the image"):
        range_cutout_cost(RangeHost(source), URL, JWST_CENTRE[0] + 1.0, JWST_CENTRE[1], 4.0)


@pytest.mark.req("INT-025")
def test_the_mast_gateway_costs_a_cutout_before_cutting_it() -> None:
    product = ProductRef("mast", "jw-synth-o001/jw-synth_i2d.fits", URL)
    host = RangeHost(jwst_i2d())
    gateway = MastObservationGateway(host)
    cost = gateway.cutout_cost(product, *JWST_CENTRE, 4.0)
    probed = host.sent
    gateway.cutout_product(product, *JWST_CENTRE, 4.0)
    assert cost.read_bytes == host.sent - probed and cost.coverage == pytest.approx(1.0)
    with pytest.raises(ValueError, match="not a MAST one"):
        gateway.cutout_cost(ProductRef("dasch", "x", URL), *JWST_CENTRE, 4.0)


@pytest.mark.req("INT-025")
def test_a_failed_header_probe_or_a_broken_header_refuses_that_product_only(monkeypatch: pytest.MonkeyPatch) -> None:
    @dataclass
    class Down:
        def send(self, request: HttpRequest) -> HttpResponse:
            raise TransportError("timed out")

    with pytest.raises(RangeUnsupported, match=r"header probe .* failed: timed out"):
        range_cutout_cost(Down(), URL, *JWST_CENTRE, 4.0)

    def no_naxis(*_: object) -> None:
        raise KeyError("NAXIS1")

    # A missing keyword is a broken product, not a cutout that misses the image (a LookupError).
    monkeypatch.setattr(fits_range, "_window", no_naxis)
    with pytest.raises(RangeUnsupported, match="without 'NAXIS1'"):
        range_cutout_cost(RangeHost(jwst_i2d()), URL, *JWST_CENTRE, 4.0)

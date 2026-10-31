"""Reference catalogues (INV-024): SIMBAD and VSX against primed fakes of their services (T3), with
VizieR's mirrors standing in when CDS gives no answer; Gaia DR3, 2MASS, AllWISE and Pan-STARRS from
cones recorded live on 2026-10-31 around one field star near Barnard's star (T5)."""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pytest

from sdp.adapters.archives.references import (
    SIMBAD,
    TAP_CATALOGUES,
    VIZIER,
    TapConeCatalogue,
    VsxCatalogue,
)
from sdp.adapters.archives.transport import CassetteTransport, MirrorTransport, TransportError
from sdp.adapters.archives.votable import TapQueryError

from .vo_fakes import PrimedService

pytestmark = pytest.mark.req("INV-024")

CASSETTES = Path(__file__).resolve().parents[1] / "cassettes" / "references"
#: the recorded star: AllWISE J175747.70+044006.7, 2MASS 17574770+0440067
RECORDED = (269.4487909, 4.6685359, 10.0)
# Invented values, for exercising the adapters only.
HERE = (150.0, 2.0)


@pytest.mark.tier("T3")
def test_simbad_rows_come_back_under_canonical_names() -> None:
    fake = PrimedService()
    simbad = TapConeCatalogue(SIMBAD, fake)
    fake.table(
        simbad.request(*HERE, 5.0),
        {
            "main_id": ["SDP Fixture A"],
            "ra": [HERE[0]],
            "dec": [HERE[1]],
            "otype": ["RR*"],
            "sp_type": [""],
            "plx_value": [1.25],
            "pmra": [3.0],
            "pmdec": [-4.0],
            "nbref": [12],
        },
    )
    answer = simbad.cone(*HERE, 5.0)
    assert answer.rows == (
        {
            "source_key": "simbad:SDP Fixture A",
            "ra_deg": HERE[0],
            "dec_deg": HERE[1],
            "main_id": "SDP Fixture A",
            "object_type": "RR*",
            "parallax_mas": 1.25,
            "pm_ra_cosdec_masyr": 3.0,
            "pm_dec_masyr": -4.0,
            "references": 12,
            "epoch_jyear": 2000.0,
        },
    )
    assert answer.media_type == "application/x-votable+xml" and b"VOTABLE" in answer.raw
    assert "CIRCLE('ICRS', 150.0, 2.0, 0.001388" in fake_query(simbad, 5.0)


def fake_query(catalogue: TapConeCatalogue, radius: float) -> str:
    from urllib.parse import parse_qs

    return parse_qs(catalogue.request(*HERE, radius).body.decode())["QUERY"][0]


@pytest.mark.tier("T3")
def test_a_refused_query_or_an_http_error_raises_rather_than_answering_nothing() -> None:
    fake = PrimedService()
    simbad = TapConeCatalogue(SIMBAD, fake)
    fake.raw(simbad.request(*HERE, 5.0), b"busy", status=503)
    with pytest.raises(TransportError, match="SIMBAD: HTTP 503"):
        simbad.cone(*HERE, 5.0)
    error = (
        b'<?xml version="1.0"?><VOTABLE version="1.4" xmlns="http://www.ivoa.net/xml/VOTable/v1.3">'
        b'<RESOURCE type="results"><INFO name="QUERY_STATUS" value="ERROR">bad column</INFO></RESOURCE></VOTABLE>'
    )
    fake.raw(simbad.request(*HERE, 6.0), error)
    with pytest.raises(TapQueryError, match="bad column"):
        simbad.cone(*HERE, 6.0)


def vsx_rows() -> dict[str, object]:
    return {
        "OID": [7],
        "Name": ["SDP Fixture V"],
        "Type": ["RRAB"],
        "Period": [0.5668],
        "max": [7.06],
        "min": [8.12],
        "n_max": ["V"],
        "RAJ2000": [HERE[0]],
        "DEJ2000": [HERE[1]],
    }


@pytest.mark.tier("T3")
def test_vsx_reads_an_asu_cone_in_decimal_degrees() -> None:
    fake = PrimedService()
    vsx = VsxCatalogue(fake)
    fake.table(vsx.request(*HERE, 5.0), vsx_rows())
    request = vsx.request(*HERE, 5.0)
    assert request.url.startswith(f"{VIZIER}/viz-bin/votable?-source=B%2Fvsx%2Fvsx")
    assert "-c.rs=5.000" in request.url and "-oc.form=dec" in request.url
    row = vsx.cone(*HERE, 5.0).rows[0]
    assert row == {
        "source_key": "vsx:7",
        "ra_deg": HERE[0],
        "dec_deg": HERE[1],
        "name": "SDP Fixture V",
        "variable_type": "RRAB",
        "period_d": 0.5668,
        "mag_max": 7.06,
        "mag_min": 8.12,
        "passband": "V",
        "epoch_jyear": 2000.0,
    }


@pytest.mark.tier("T3")
def test_vsx_without_decimal_positions_is_an_error_not_an_empty_answer() -> None:
    fake = PrimedService()
    vsx = VsxCatalogue(fake)
    rows = {k: v for k, v in vsx_rows().items() if k not in ("RAJ2000", "DEJ2000")}
    fake.table(vsx.request(*HERE, 5.0), {**rows, "RA": ["10 00 00"], "DE": ["+02 00 00"]})
    with pytest.raises(TransportError, match="without decimal positions"):
        vsx.cone(*HERE, 5.0)


@pytest.mark.tier("T3")
def test_vsx_is_read_from_a_vizier_mirror_when_cds_gives_no_answer() -> None:
    class CdsDown(PrimedService):
        def send(self, request):  # type: ignore[no-untyped-def]
            if request.url.startswith(VIZIER):
                raise TransportError("connection refused")
            return super().send(request)

    mirror = "https://vizier.cfa.harvard.edu"
    fake = CdsDown()
    fake.table(VsxCatalogue(fake, base_url=mirror).request(*HERE, 5.0), vsx_rows())
    vsx = VsxCatalogue(MirrorTransport(fake, {VIZIER: (mirror,)}))
    assert vsx.cone(*HERE, 5.0).rows[0]["name"] == "SDP Fixture V"


@pytest.mark.tier("T3")
def test_missing_values_and_the_minus_999_placeholder_are_left_out() -> None:
    fake = PrimedService()
    ps1 = TapConeCatalogue(TAP_CATALOGUES["ps1"], fake)
    fake.table(
        ps1.request(*HERE, 5.0),
        {
            "objID": [1],
            "raMean": [HERE[0]],
            "decMean": [HERE[1]],
            "epochMean": [55197.0],
            "gMeanPSFMag": [-999.0],
            "rMeanPSFMag": [float("nan")],
            "iMeanPSFMag": [18.5],
            "zMeanPSFMag": pa.array([None], pa.float64()),
            "yMeanPSFMag": [18.1],
            "nDetections": [9],
        },
    )
    row = ps1.cone(*HERE, 5.0).rows[0]
    assert "ps_g_mag" not in row and "ps_r_mag" not in row and "ps_z_mag" not in row
    assert row["ps_i_mag"] == 18.5 and row["detections"] == 9
    assert row["epoch_jyear"] == pytest.approx(2010.0, abs=1e-6)


@pytest.mark.tier("T5")
@pytest.mark.parametrize("source_id", ["gaia", "2mass", "allwise", "ps1"])
def test_a_recorded_cone_replays_to_canonical_rows(source_id: str) -> None:
    catalogue = TapConeCatalogue(TAP_CATALOGUES[source_id], CassetteTransport(CASSETTES / source_id, "replay"))
    rows = catalogue.cone(*RECORDED).rows
    assert rows, f"the recorded {source_id} cone holds the star"
    nearest = min(rows, key=lambda r: (r["ra_deg"] - RECORDED[0]) ** 2 + (r["dec_deg"] - RECORDED[1]) ** 2)
    assert nearest["ra_deg"] == pytest.approx(RECORDED[0], abs=2 / 3600)
    assert nearest["dec_deg"] == pytest.approx(RECORDED[1], abs=2 / 3600)
    assert 1998 < nearest["epoch_jyear"] < 2017
    assert str(nearest["source_key"]).startswith({"gaia": "gaia:DR3:"}.get(source_id, f"{source_id}:"))
    expected = {"gaia": "g_mag", "2mass": "j_mag", "allwise": "w1_mag", "ps1": "ps_i_mag"}[source_id]
    assert 10 < nearest[expected] < 20

"""Reference catalogues behind the ReferenceCatalogue port (INV-023): one small cone each, through a
Transport, so CI replays recordings or primed fakes and never calls an archive (ADR-015).

SIMBAD, Gaia DR3, 2MASS, AllWISE and Pan-STARRS DR2 answer an ADQL cone over TAP; AAVSO's VSX is read
from VizieR's classic (ASU) interface, which every VizieR mirror serves, so it keeps working through a
CDS outage when the composition root puts it behind a MirrorTransport. Each catalogue's native columns
are renamed to the canonical names the dossier's catalogue rows use, so the comparison page lines
catalogues up by quantity, and the archive's answer is returned as it came for the kernel to store.
"""

from __future__ import annotations

import math
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import pyarrow as pa

from sdp.adapters.archives import votable
from sdp.adapters.archives.transport import HttpRequest, Transport, TransportError
from sdp.adapters.archives.untrusted import validate_votable
from sdp.adapters.archives.vo import sync_request
from sdp.kernel.ports.references import ReferenceAnswer

VOTABLE = "application/x-votable+xml"
#: rows asked for per cone; a 5" cone holds one or two, a 10' cone around a cluster a few dozen
MAXREC = 200

Convert = Callable[[Any], Any]


def _number(value: Any) -> float | None:
    """A finite number, or None for a null, NaN or the -999 some catalogues write for 'not measured'."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= -999:
        return None
    return number


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _integer(value: Any) -> int | None:
    number = _number(value)
    return None if number is None else int(number)


def _jd_to_jyear(value: Any) -> float | None:
    jd = _number(value)
    return None if jd is None else 2000.0 + (jd - 2451545.0) / 365.25


def _mjd_to_jyear(value: Any) -> float | None:
    mjd = _number(value)
    return None if mjd is None else 2000.0 + (mjd + 2400000.5 - 2451545.0) / 365.25


@dataclass(frozen=True)
class Column:
    native: str
    canonical: str
    convert: Convert = _number


@dataclass(frozen=True)
class ConeSpec:
    """One TAP catalogue: where it is, its position columns, and how its columns map to canonical names."""

    source_id: str
    title: str
    release_label: str
    citation: str
    description: str
    service_url: str
    table: str
    ra: str
    dec: str
    key: Column
    columns: tuple[Column, ...]
    #: values every row of this catalogue shares, e.g. the epoch its positions refer to
    fixed: Mapping[str, Any] | None = None
    #: what ``source_key`` starts with; the source id unless the catalogue has its own convention
    key_prefix: str | None = None


def canonical_rows(
    spec_key: Column, columns: tuple[Column, ...], fixed: Mapping[str, Any] | None, table: pa.Table, prefix: str
) -> tuple[dict[str, Any], ...]:
    """The table's rows under canonical names; ``source_key`` is ``<prefix>:<native key>``."""
    native = table.to_pylist()
    lowered = [{str(k).lower(): v for k, v in row.items()} for row in native]
    out: list[dict[str, Any]] = []
    for row in lowered:
        key = _text(row.get(spec_key.native.lower()))
        record: dict[str, Any] = {"source_key": f"{prefix}:{key}" if key else None}
        for column in columns:
            value = column.convert(row.get(column.native.lower()))
            if value is not None:
                record[column.canonical] = value
        for name, value in (fixed or {}).items():
            record.setdefault(name, value)
        out.append(record)
    return tuple(out)


@dataclass
class TapConeCatalogue:
    spec: ConeSpec
    transport: Transport
    maxrec: int = MAXREC

    @property
    def source_id(self) -> str:
        return self.spec.source_id

    @property
    def title(self) -> str:
        return self.spec.title

    @property
    def release_label(self) -> str:
        return self.spec.release_label

    @property
    def citation(self) -> str:
        return self.spec.citation

    @property
    def description(self) -> str:
        return self.spec.description

    def request(self, ra_deg: float, dec_deg: float, radius_arcsec: float) -> HttpRequest:
        s = self.spec
        names = [s.key.native, s.ra, s.dec, *(c.native for c in s.columns if c.native not in (s.ra, s.dec))]
        select = ", ".join(dict.fromkeys(names))
        adql = (
            f"SELECT TOP {self.maxrec} {select} FROM {s.table} WHERE 1 = CONTAINS(POINT('ICRS', {s.ra}, {s.dec}), "
            f"CIRCLE('ICRS', {float(ra_deg)!r}, {float(dec_deg)!r}, {radius_arcsec / 3600.0!r}))"
        )
        return sync_request(s.service_url, adql, self.maxrec)

    def cone(self, ra_deg: float, dec_deg: float, radius_arcsec: float) -> ReferenceAnswer:
        response = self.transport.send(self.request(ra_deg, dec_deg, radius_arcsec))
        if response.status != 200:
            raise TransportError(f"{self.spec.title}: HTTP {response.status}", response.status)
        validate_votable(response.body)
        table = votable.parse(response.body)
        s = self.spec
        position = (Column(s.ra, "ra_deg"), Column(s.dec, "dec_deg"))
        rows = canonical_rows(s.key, (*position, *s.columns), s.fixed, table, s.key_prefix or s.source_id)
        return ReferenceAnswer(rows, response.body, VOTABLE)


SIMBAD = ConeSpec(
    source_id="simbad",
    title="SIMBAD",
    release_label="simbad:live",
    citation="Wenger et al. 2000, A&AS 143, 9",
    description="Whether the object is already known, what type it is and how many papers mention it.",
    service_url="https://simbad.cds.unistra.fr/simbad/sim-tap",
    table="basic",
    ra="ra",
    dec="dec",
    key=Column("main_id", "main_id", _text),
    columns=(
        Column("main_id", "main_id", _text),
        Column("otype", "object_type", _text),
        Column("sp_type", "spectral_type", _text),
        Column("plx_value", "parallax_mas"),
        Column("pmra", "pm_ra_cosdec_masyr"),
        Column("pmdec", "pm_dec_masyr"),
        Column("nbref", "references", _integer),
    ),
    fixed={"epoch_jyear": 2000.0},
)

GAIA = ConeSpec(
    source_id="gaia",
    title="Gaia DR3",
    release_label="gaia:DR3",
    citation="Gaia Collaboration, Vallenari et al. 2023, A&A 674, A1",
    description="Position, parallax, proper motion and brightness of every Gaia source near the candidate.",
    service_url="https://gea.esac.esa.int/tap-server/tap",
    table="gaiadr3.gaia_source",
    ra="ra",
    dec="dec",
    key=Column("source_id", "source_id", _text),
    columns=(
        Column("ref_epoch", "epoch_jyear"),
        Column("parallax", "parallax_mas"),
        Column("parallax_error", "parallax_mas_err"),
        Column("pmra", "pm_ra_cosdec_masyr"),
        Column("pmdec", "pm_dec_masyr"),
        Column("phot_g_mean_mag", "g_mag"),
        Column("bp_rp", "bp_rp"),
        Column("ruwe", "ruwe"),
    ),
    # the same keys as the candidate's own Gaia rows, so the candidate is recognised among them
    key_prefix="gaia:DR3",
)

TWOMASS = ConeSpec(
    source_id="2mass",
    title="2MASS",
    release_label="2mass:psc",
    citation="Skrutskie et al. 2006, AJ 131, 1163",
    description="Near-infrared J, H and Ks brightness, measured about 1998-2001.",
    service_url="https://irsa.ipac.caltech.edu/TAP",
    table="fp_psc",
    ra="ra",
    dec="dec",
    key=Column("designation", "designation", _text),
    columns=(
        Column("jdate", "epoch_jyear", _jd_to_jyear),
        Column("j_m", "j_mag"),
        Column("h_m", "h_mag"),
        Column("k_m", "k_mag"),
        Column("ph_qual", "quality", _text),
    ),
)

ALLWISE = ConeSpec(
    source_id="allwise",
    title="AllWISE",
    release_label="irsa:allwise",
    citation="Cutri et al. 2013, AllWISE Explanatory Supplement",
    description="Mid-infrared W1 to W4 brightness and contamination flags, observed 2010-2011.",
    service_url="https://irsa.ipac.caltech.edu/TAP",
    table="allwise_p3as_psd",
    ra="ra",
    dec="dec",
    key=Column("designation", "designation", _text),
    columns=(
        Column("w1mpro", "w1_mag"),
        Column("w2mpro", "w2_mag"),
        Column("w3mpro", "w3_mag"),
        Column("w4mpro", "w4_mag"),
        Column("pmra", "pm_ra_cosdec_masyr"),
        Column("pmdec", "pm_dec_masyr"),
        Column("cc_flags", "quality", _text),
    ),
    # AllWISE positions refer to the mean epoch of its observations, about 2010.5
    fixed={"epoch_jyear": 2010.5},
)

PS1 = ConeSpec(
    source_id="ps1",
    title="Pan-STARRS DR2",
    release_label="ps1:DR2",
    citation="Chambers et al. 2016, arXiv:1612.05560; Flewelling et al. 2020, ApJS 251, 7",
    description="Optical g, r, i, z and y brightness from MAST, deeper than Gaia, north of Dec -30.",
    service_url="https://mast.stsci.edu/vo-tap/api/v0.1/ps1dr2",
    table="dbo.MeanObjectView",
    ra="raMean",
    dec="decMean",
    key=Column("objID", "objID", _text),
    columns=(
        Column("epochMean", "epoch_jyear", _mjd_to_jyear),
        Column("gMeanPSFMag", "ps_g_mag"),
        Column("rMeanPSFMag", "ps_r_mag"),
        Column("iMeanPSFMag", "ps_i_mag"),
        Column("zMeanPSFMag", "ps_z_mag"),
        Column("yMeanPSFMag", "ps_y_mag"),
        Column("nDetections", "detections", _integer),
    ),
)

TAP_CATALOGUES: dict[str, ConeSpec] = {s.source_id: s for s in (SIMBAD, GAIA, TWOMASS, ALLWISE, PS1)}

VIZIER = "https://vizier.cds.unistra.fr"


@dataclass
class VsxCatalogue:
    """AAVSO's International Variable Star Index (VSX), VizieR table B/vsx/vsx, read by an ASU cone.

    ASU rather than TAPVizieR because every VizieR mirror answers the same URL, so behind a
    MirrorTransport a CDS outage costs one failover. Positions come back in decimal degrees (J2000)."""

    transport: Transport
    base_url: str = VIZIER
    maxrec: int = MAXREC
    source_id: str = "vsx"
    title: str = "AAVSO VSX"
    release_label: str = "aavso:vsx"
    citation: str = "Watson, Henden & Price 2006, SASS 25, 47 (VizieR B/vsx)"
    description: str = "Whether a variable star is already catalogued here, with its type, period and range."

    def request(self, ra_deg: float, dec_deg: float, radius_arcsec: float) -> HttpRequest:
        query = urllib.parse.urlencode(
            {
                "-source": "B/vsx/vsx",
                "-c": f"{float(ra_deg):.7f} {float(dec_deg):+.7f}",
                "-c.rs": f"{float(radius_arcsec):.3f}",
                "-out.max": str(self.maxrec),
                "-oc.form": "dec",
                "-out.all": "1",
            }
        )
        return HttpRequest("GET", f"{self.base_url}/viz-bin/votable?{query}")

    def cone(self, ra_deg: float, dec_deg: float, radius_arcsec: float) -> ReferenceAnswer:
        response = self.transport.send(self.request(ra_deg, dec_deg, radius_arcsec))
        if response.status != 200:
            raise TransportError(f"VizieR B/vsx: HTTP {response.status}", response.status)
        validate_votable(response.body)
        table = votable.parse(response.body)
        names = {n.lower(): n for n in table.column_names}
        ra = next((names[n] for n in ("raj2000", "_raj2000") if n in names), None)
        dec = next((names[n] for n in ("dej2000", "_dej2000") if n in names), None)
        if table.num_rows and (ra is None or dec is None):
            raise TransportError(f"VizieR B/vsx answered without decimal positions: {sorted(table.column_names)}")
        columns = (
            Column(ra or "RAJ2000", "ra_deg"),
            Column(dec or "DEJ2000", "dec_deg"),
            Column("Name", "name", _text),
            Column("Type", "variable_type", _text),
            Column("Period", "period_d"),
            Column("max", "mag_max"),
            Column("min", "mag_min"),
            Column("n_max", "passband", _text),
        )
        rows = canonical_rows(Column("OID", "oid", _text), columns, {"epoch_jyear": 2000.0}, table, self.source_id)
        return ReferenceAnswer(rows, response.body, VOTABLE)

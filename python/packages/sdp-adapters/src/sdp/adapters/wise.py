"""WISE and NEOWISE: what their light curves are, where IRSA serves them and how they are named.

Shared by the gateway and inventory (``sdp.adapters.archives.irsa_wise``) and the product dialect
(``sdp.adapters.products.wise``), which may not import each other (§4).

IRSA serves every WISE exposure's photometry as TAP tables: ``allwise_p3as_mep`` (the 2010-2011
cryogenic and post-cryogenic survey, AllWISE multi-epoch photometry) and ``neowiser_p1bs_psd``
(NEOWISE reactivation, December 2013 to August 2024). A light curve is the answer to one cone
query on one of them, so its product URI is that query as a TAP ``sync`` GET, and fetching the URI
reproduces the bytes.
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass

IRSA_TAP = "https://irsa.ipac.caltech.edu/TAP"
SOURCE_TABLE = "allwise_p3as_psd"
MISSION = "WISE"
MEDIA_TYPE = "application/x-votable+xml"
LICENCE = "NASA/IPAC Infrared Science Archive; WISE and NEOWISE data are public (https://irsa.ipac.caltech.edu/data_use_terms.html)"
CITATION = "Wright et al. 2010, AJ 140, 1868 (WISE); Mainzer et al. 2014, ApJ 792, 30 (NEOWISE)"
ACKNOWLEDGEMENT = (
    "This publication makes use of data products from the Wide-field Infrared Survey Explorer, a joint project "
    "of the University of California, Los Angeles, and the Jet Propulsion Laboratory/California Institute of "
    "Technology, funded by NASA, and from NEOWISE, a project of the Jet Propulsion Laboratory/California "
    "Institute of Technology, funded by the Planetary Science Division of NASA."
)
#: A light curve of a few hundred exposures is tens of kilobytes; anything near this is not one.
MAX_PRODUCT_BYTES = 64 * 1024**2
#: WISE's W1 (3.4 um) and W2 (4.6 um) passbands together span about 2.8 to 5.3 um (Wright et al. 2010).
WISE_BAND_M = (2.8e-6, 5.3e-6)
#: The W1 point-spread function's full width at half maximum (Wright et al. 2010).
W1_RESOLUTION_ARCSEC = 6.1
#: How far from the AllWISE position an exposure's detection may sit: a third of the W1 beam.
LIGHTCURVE_RADIUS_ARCSEC = 2.5


@dataclass(frozen=True)
class WiseTable:
    """One IRSA table of per-exposure photometry, and the names of its columns."""

    key: str
    """The product id suffix."""
    table: str
    mission: str
    span_mjd: tuple[float, float]
    w1: str
    w1_error: str
    w2: str
    w2_error: str
    quality: tuple[str, ...]
    """Columns the curation reads to drop bad exposures; they are the light curve's quality flags."""
    position_errors: tuple[str, ...] = ()
    """The per-exposure positional sigmas in RA and Dec (arcsec). Empty for a table whose ``ra`` and
    ``dec`` are not measured per exposure: AllWISE's repeats the source's coadded position (checked
    live 2026-10-30), so its exposures say nothing about where the source was at each visit."""

    def columns(self) -> tuple[str, ...]:
        return (
            "ra",
            "dec",
            *self.position_errors,
            "mjd",
            self.w1,
            self.w1_error,
            self.w2,
            self.w2_error,
            *self.quality,
        )


#: NEOWISE first: its decade of visits is the baseline a variability test wants.
WISE_TABLES: dict[str, WiseTable] = {
    "NEOWISE": WiseTable(
        "NEOWISE",
        "neowiser_p1bs_psd",
        "NEOWISE",
        (56639.0, 60523.0),
        "w1mpro",
        "w1sigmpro",
        "w2mpro",
        "w2sigmpro",
        ("qual_frame", "qi_fact", "saa_sep", "moon_masked", "nb", "na", "cc_flags"),
        ("sigra", "sigdec"),
    ),
    "ALLWISE": WiseTable(
        "ALLWISE",
        "allwise_p3as_mep",
        "WISE",
        (55203.0, 55593.0),
        "w1mpro_ep",
        "w1sigmpro_ep",
        "w2mpro_ep",
        "w2sigmpro_ep",
        ("qi_fact", "saa_sep", "moon_masked", "nb", "na", "cc_flags"),
    ),
}
PRODUCT_ID = re.compile(r"^WISE (J\d{6}\.\d{2}[+-]\d{6}\.\d) (\d+\.\d+) ([+-]?\d+\.\d+)/([A-Z]+)$")


def product_id(designation: str, ra_deg: float, dec_deg: float, key: str) -> str:
    """One light curve per AllWISE source and table; the position is the cone's centre."""
    return f"WISE {designation} {ra_deg:.7f} {dec_deg:+.7f}/{key}"


def parse_product_id(value: str) -> tuple[str, float, float, WiseTable]:
    """The AllWISE designation, the cone's centre and the table a product id names."""
    match = PRODUCT_ID.match(value)
    if match is None or match.group(4) not in WISE_TABLES:
        raise ValueError(f"{value!r} is not a WISE light-curve product id")
    return match.group(1), float(match.group(2)), float(match.group(3)), WISE_TABLES[match.group(4)]


def lightcurve_query(
    table: WiseTable, ra_deg: float, dec_deg: float, radius_arcsec: float = LIGHTCURVE_RADIUS_ARCSEC
) -> str:
    return (
        f"SELECT {', '.join(table.columns())} FROM {table.table} WHERE 1 = CONTAINS(POINT('ICRS', ra, dec), "
        f"CIRCLE('ICRS', {ra_deg:.7f}, {dec_deg:+.7f}, {radius_arcsec / 3600.0!r})) ORDER BY mjd"
    )


def lightcurve_url(
    table: WiseTable, ra_deg: float, dec_deg: float, radius_arcsec: float = LIGHTCURVE_RADIUS_ARCSEC
) -> str:
    query = urllib.parse.urlencode(
        {"LANG": "ADQL", "FORMAT": "votable", "QUERY": lightcurve_query(table, ra_deg, dec_deg, radius_arcsec)}
    )
    return f"{IRSA_TAP}/sync?{query}"

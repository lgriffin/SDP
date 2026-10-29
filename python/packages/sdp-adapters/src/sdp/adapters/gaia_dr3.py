"""Gaia DR3's DataLink products: what they are, where they are served and how they are named.

Shared by the gateway and inventory (``sdp.adapters.archives.gaia_datalink``) and the product dialect
(``sdp.adapters.products.gaia``), which may not import each other (§4).
"""

from __future__ import annotations

import math
import re
import urllib.parse
from dataclasses import dataclass

from sdp.kernel.domain.integration import ProductType
from sdp.kernel.science import products

GAIA_TAP = "https://gea.esac.esa.int/tap-server/tap"
DATALINK = "https://gea.esac.esa.int/data-server/datalink/links"
DATA = "https://gea.esac.esa.int/data-server/data"
RELEASE = "Gaia DR3"
MISSION = "GAIA"
MEDIA_TYPE = "application/x-votable+xml"
LICENCE = "ESA/Gaia/DPAC, CC BY-SA 3.0 IGO; https://www.cosmos.esa.int/web/esdc/terms-and-conditions"
CITATION = "Gaia Collaboration, Vallenari et al. 2023, A&A 674, A1 (Gaia DR3)"
ACKNOWLEDGEMENT = (
    "This work has made use of data from the European Space Agency (ESA) mission Gaia "
    "(https://www.cosmos.esa.int/gaia), processed by the Gaia Data Processing and Analysis Consortium (DPAC)."
)


@dataclass(frozen=True)
class GaiaProduct:
    """One DataLink retrieval type and what SDP knows about it."""

    retrieval_type: str
    product_type: str
    """The Investigate product type a record offers it as."""
    kind: ProductType
    flag: str
    """The ``gaia_source`` column that says a source has it."""
    calibration_level: int
    """ObsCore: per-transit calibrated photometry is 2; a mean spectrum combined over transits is 3."""
    instrument: str
    bands: str
    data_column: str
    """The column whose unit is the product's: G-band flux for photometry, flux for a spectrum."""
    em_min_m: float
    em_max_m: float


#: DR3's epoch-photometry span (Gaia Collaboration 2023, §2): 2014-07-25 to 2017-05-28.
DR3_SPAN_MJD = (56863.0, 57901.0)
#: The G passband and the BP/RP spectra both run from about 330 to 1050 nm.
GAIA_BAND_M = (330e-9, 1050e-9)

GAIA_PRODUCTS: dict[str, GaiaProduct] = {
    products.LIGHTCURVE: GaiaProduct(
        "EPOCH_PHOTOMETRY",
        products.LIGHTCURVE,
        ProductType.TIMESERIES,
        "has_epoch_photometry",
        2,
        "Astro, BP, RP",
        "G, BP, RP",
        "G_TRANSIT_FLUX",
        *GAIA_BAND_M,
    ),
    "spectrum": GaiaProduct(
        "XP_SAMPLED", "spectrum", ProductType.SPECTRUM, "has_xp_sampled", 3, "BP, RP", "BP/RP", "FLUX", *GAIA_BAND_M
    ),
}
BY_RETRIEVAL = {p.retrieval_type: p for p in GAIA_PRODUCTS.values()}

#: Gaia's time origin: days since JD 2455197.5 (TCB, barycentric), so MJD = time + 55197.
GAIA_TIME_ORIGIN_MJD = 2455197.5 - 2400000.5
#: The magnitude error of a flux measurement: 2.5 / ln(10) times its relative error.
MAG_PER_RELATIVE_FLUX = 2.5 / math.log(10.0)
SOURCE_ID = re.compile(r"^Gaia DR3 (\d+)/([A-Z_]+)$")


def product_id(source_id: int | str, retrieval_type: str) -> str:
    return f"{RELEASE} {source_id}/{retrieval_type}"


def parse_product_id(value: str) -> tuple[str, GaiaProduct]:
    """The source id and the product a product id names."""
    match = SOURCE_ID.match(value)
    if match is None or match.group(2) not in BY_RETRIEVAL:
        raise ValueError(f"{value!r} is not a Gaia DataLink product id")
    return match.group(1), BY_RETRIEVAL[match.group(2)]


def data_url(source_id: int | str, retrieval_type: str) -> str:
    """The URL DataLink lists for the product (its ``#this`` row's ``access_url``)."""
    query = urllib.parse.urlencode(
        {"ID": f"{RELEASE} {source_id}", "RETRIEVAL_TYPE": retrieval_type, "LINKING_PARAMETER": "SOURCE_ID"}
    )
    return f"{DATA}?{query}"


def links_url(source_id: int | str) -> str:
    return f"{DATALINK}?{urllib.parse.urlencode({'ID': f'{RELEASE} {source_id}'})}"

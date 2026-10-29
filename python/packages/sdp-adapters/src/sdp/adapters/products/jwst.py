"""The JWST product dialect (Integration v0.4.0 §3, §7; ADR-018, ADR-022).

Fills the ObsCore-shaped envelope for a JWST pipeline product from three sources, in the order of
v0.4.0 §3: the FITS keywords (which win when present), the MAST product-inventory row and the ObsCore
row. JWST pipeline stages map onto ObsCore calibration levels: ``uncal`` is 1, ``rate``/``cal`` 2,
``i2d``/``s2d``/``s3d``/``x1d`` of a combined observation 3. ``SCI``, ``ERR``, ``DQ`` and ``VAR_*``
become the payload descriptor; the calibration reference files named in the header (``R_*``) are
recorded as provenance.

Headers are read with ``astropy.io.fits``; the ASDF extension is recorded but not parsed, because no
envelope field needs it (deviation D34).
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from astropy.time import Time

from sdp.adapters.products.fits import FITS_MEDIA_TYPES, HduInfo, effective_media_type, merged_header, read_hdus
from sdp.kernel.domain.integration import (
    AccessStatus,
    PayloadExtension,
    ProductMetadata,
    ProductRelationship,
    ProductType,
    RelationshipSemantics,
    Rights,
)
from sdp.kernel.domain.investigation import FetchedProduct
from sdp.kernel.ports.integration import RawProduct
from sdp.kernel.ports.investigate import CuratedProduct

SUFFIX_TYPE: dict[str, ProductType] = {
    "uncal": ProductType.IMAGE,
    "rate": ProductType.IMAGE,
    "rateints": ProductType.IMAGE,
    "cal": ProductType.IMAGE,
    "calints": ProductType.IMAGE,
    "crf": ProductType.IMAGE,
    "i2d": ProductType.IMAGE,
    "s2d": ProductType.SPECTRUM,
    "x1d": ProductType.SPECTRUM,
    "x1dints": ProductType.SPECTRUM,
    "c1d": ProductType.SPECTRUM,
    "s3d": ProductType.CUBE,
    "whtlt": ProductType.TIMESERIES,
    "cat": ProductType.CATALOGUE,
    "segm": ProductType.AUXILIARY,
    "asn": ProductType.AUXILIARY,
    "pool": ProductType.AUXILIARY,
    "thumb": ProductType.THUMBNAIL,
}
SUFFIX_LEVEL: dict[str, int] = {"uncal": 1, "rate": 2, "rateints": 2, "cal": 2, "calints": 2, "x1dints": 2}
LEVEL3_SUFFIXES = frozenset({"i2d", "s2d", "s3d", "x1d", "c1d", "crf", "whtlt", "cat", "segm"})
#: Level-3 products are named after the observation (``jw01345-o001_t001_...``), level-2 after the exposure.
LEVEL3_NAME = re.compile(r"^jw\d{5}-[oac]\d{3}_", re.IGNORECASE)
INVENTORY_KIND = {"PREVIEW": ProductType.PREVIEW, "AUXILIARY": ProductType.AUXILIARY, "INFO": ProductType.AUXILIARY}
EXTENSION_ROLE = {"SCI": "data", "ERR": "error", "DQ": "quality"}
COLUMN_ROLE = {
    "FLUX": "data",
    "SURF_BRIGHT": "data",
    "FLUX_ERROR": "error",
    "ERROR": "error",
    "SB_ERROR": "error",
    "DQ": "quality",
    "FLUX_VAR_POISSON": "variance",
    "FLUX_VAR_RNOISE": "variance",
    "FLUX_VAR_FLAT": "variance",
}
DATA_RIGHTS = {"PUBLIC": AccessStatus.PUBLIC, "EXCLUSIVE_ACCESS": AccessStatus.EXCLUSIVE_ACCESS}
CITATION = "Based on observations with the NASA/ESA/CSA James Webb Space Telescope, obtained from MAST at STScI"
ACKNOWLEDGEMENT = (
    "Some/all of the data presented in this paper were obtained from the Mikulski Archive for Space "
    "Telescopes (MAST) at the Space Telescope Science Institute."
)


def _str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def filename_of(raw: FetchedProduct, inventory: Mapping[str, Any]) -> str:
    name = _str(inventory.get("productFilename")) or raw.product.product_id
    return name.rsplit("/", 1)[-1]


def suffix_of(filename: str) -> str:
    stem = filename.rsplit(".", 1)[0]
    return stem.rsplit("_", 1)[-1].lower()


def extension_roles(hdus: tuple[HduInfo, ...]) -> tuple[PayloadExtension, ...]:
    out: list[PayloadExtension] = []
    for h in hdus[1:]:
        if h.kind == "table" and h.columns:
            for col in h.columns:
                role = COLUMN_ROLE.get(col)
                if role:
                    out.append(PayloadExtension(f"{h.name}:{col}", role, h.column_units.get(col)))
            continue
        if h.name in EXTENSION_ROLE:
            role = EXTENSION_ROLE[h.name]
        elif h.name.startswith("VAR_"):
            role = "variance"
        else:
            role = "other"
        unit = _str(h.header.get("BUNIT")) if role in ("data", "error", "variance") else None
        out.append(PayloadExtension(h.name, role, unit))
    return tuple(out)


def _times(header: Mapping[str, Any], obscore: Mapping[str, Any]) -> tuple[float | None, float | None, str]:
    scale = (_str(header.get("TIMESYS")) or "").lower()
    beg, end = _str(header.get("DATE-BEG")), _str(header.get("DATE-END"))
    if beg and end and scale:
        return float(Time(beg, scale=scale).mjd), float(Time(end, scale=scale).mjd), scale.upper()
    start, stop = _float(header.get("EXPSTART")), _float(header.get("EXPEND"))
    if start is not None and stop is not None:
        return start, stop, (scale or "utc").upper()
    return _float(obscore.get("t_min")), _float(obscore.get("t_max")), "UNKNOWN"


def calibration_level_from_name(filename: str) -> int | None:
    """ObsCore level from the pipeline suffix, and for x1d and the like from the naming scheme."""
    suffix = suffix_of(filename)
    if suffix in SUFFIX_LEVEL:
        return SUFFIX_LEVEL[suffix]
    if suffix in LEVEL3_SUFFIXES:
        return 3 if LEVEL3_NAME.match(filename) else 2
    return None


@dataclass(frozen=True)
class JwstDialect:
    """The Investigate ``ProductDialect`` for JWST products, and their ``ProductNormalizer``."""

    dialect_id: str = "mast-jwst"
    dialect_version: str = "1.0.0"

    def curate(self, product_type: str, raw: FetchedProduct) -> CuratedProduct | None:
        """JWST images, spectra and cubes are read as served; there is no canonical rewrite."""
        return None

    def normalize(self, product: RawProduct) -> ProductMetadata:
        raw, inv, obs = product.fetched, product.inventory_row, product.obscore_row
        filename = filename_of(raw, inv)
        suffix = suffix_of(filename)
        media_type = effective_media_type(raw.data, raw.media_type)
        hdus = read_hdus(raw.data) if media_type in FITS_MEDIA_TYPES else ()
        header = merged_header(hdus)
        kind = INVENTORY_KIND.get(str(inv.get("productType", "")).upper())
        if kind is None:
            kind = SUFFIX_TYPE.get(suffix) or ProductType(str(obs.get("dataproduct_type") or "image"))
        level = inv.get("calib_level", obs.get("calib_level"))
        if level is None:
            level = calibration_level_from_name(filename)
        t0, t1, scale = _times(header, obs)
        ra = _float(obs.get("s_ra")) if obs.get("s_ra") is not None else _float(header.get("TARG_RA"))
        dec = _float(obs.get("s_dec")) if obs.get("s_dec") is not None else _float(header.get("TARG_DEC"))
        sci = next((h for h in hdus if h.name == "SCI"), None)
        dims = sci.shape if sci else ()
        exts = extension_roles(hdus)
        data_units = next((e.unit for e in exts if e.role == "data" and e.unit), None)
        rights = DATA_RIGHTS.get(
            str(inv.get("dataRights") or obs.get("data_rights") or "PUBLIC").upper(), AccessStatus.EXCLUSIVE_ACCESS
        )
        links = tuple(
            ProductRelationship(
                RelationshipSemantics(str(row["semantics"])),
                str(row.get("ID") or row.get("access_url")),
                str(row.get("description") or ""),
            )
            for row in product.links
            if str(row.get("semantics")) in {s.value for s in RelationshipSemantics}
            and str(row.get("semantics")) != RelationshipSemantics.THIS.value
        )
        references = tuple(
            sorted((k, str(v)) for k, v in header.items() if k.startswith("R_") and v not in (None, "N/A"))
        )
        product_id = f"mast:JWST/product/{filename}"
        _, at, cut = raw.product.product_id.partition("@")
        if at:
            # A cutout is its own product; the envelope says what it was cut from (INT-004).
            links = (*links, ProductRelationship(RelationshipSemantics.CUTOUT, product_id, raw.read_from))
            product_id = f"{product_id}@{cut}"
        return ProductMetadata(
            product_id=product_id,
            product_type=kind,
            mission=_str(header.get("TELESCOP")) or _str(obs.get("obs_collection")) or "JWST",
            instrument=_str(header.get("INSTRUME")) or _str(obs.get("instrument_name")),
            detector=_str(header.get("DETECTOR")),
            filter_or_grating=_str(header.get("GRATING"))
            if _str(header.get("GRATING")) not in (None, "N/A")
            else _str(header.get("FILTER")) or _str(inv.get("filters")),
            observation_id=_str(inv.get("obs_id")) or _str(obs.get("obs_id")) or filename,
            programme_id=_str(header.get("PROGRAM")) or _str(inv.get("proposal_id")),
            ra_deg=ra,
            dec_deg=dec,
            frame=(_str(header.get("RADESYS")) or "ICRS").upper(),
            s_region=_str(obs.get("s_region")),
            time_start_mjd=t0,
            time_end_mjd=t1,
            time_scale=scale,
            em_min_m=_float(obs.get("em_min")),
            em_max_m=_float(obs.get("em_max")),
            spatial_resolution_arcsec=_float(obs.get("s_resolution")),
            field_of_view_deg=_float(obs.get("s_fov")),
            calibration_level=int(level) if level is not None else None,
            pipeline_version=_str(header.get("CAL_VER")) or _str(inv.get("prvversion")),
            media_type=media_type,
            dimensions=dims,
            units=data_units,
            extensions=exts,
            has_wcs=bool(sci and sci.has_celestial_wcs()),
            access_status=rights,
            exclusive_access_ends=None,
            rights=Rights(raw.licence, CITATION, ACKNOWLEDGEMENT),
            source_id=product.source_id,
            rendered_query=product.rendered_query,
            checksum="sha256:" + hashlib.sha256(raw.data).hexdigest(),
            size_bytes=len(raw.data),
            relationships=links,
            calibration_references=references,
        )

"""Synthetic JWST-layout products for the product model's code tests (v0.4.0 §3, §6).

These files are built here with astropy and say so in every id: they follow the JWST pipeline's
extension layout and keywords so the dialect and validator can be exercised in CI, but they are
not archive products and their reference records have status ``synthetic``, which never admits an
adapter to a gate. Real fixtures live in ``fixtures/reference-products/`` and are recorded live.
"""

from __future__ import annotations

import hashlib
import io
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from astropy.io import fits

from sdp.kernel.domain.integration import ProductType, ReferenceRecord, ReferenceStatus
from sdp.kernel.domain.investigation import FetchedProduct, ProductRef
from sdp.kernel.ports.integration import RawProduct

DOC = "https://jwst-pipeline.readthedocs.io/en/latest/jwst/data_products/science_products.html"
REFERENCE_ROOT = Path(__file__).resolve().parents[3] / "fixtures" / "reference-products"


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _wcs(h: fits.Header, ra: float = 53.16, dec: float = -27.78) -> None:
    h.update(
        {
            "CTYPE1": "RA---TAN",
            "CTYPE2": "DEC--TAN",
            "CRVAL1": ra,
            "CRVAL2": dec,
            "CRPIX1": 16.0,
            "CRPIX2": 16.0,
            "CDELT1": -8.7e-6,
            "CDELT2": 8.7e-6,
            "CUNIT1": "deg",
            "CUNIT2": "deg",
            "RADESYS": "ICRS",
        }
    )


def _primary(instrument: str, detector: str, spectral: Mapping[str, str]) -> fits.PrimaryHDU:
    p = fits.PrimaryHDU()
    p.header.update(
        {
            "TELESCOP": "JWST",
            "INSTRUME": instrument,
            "DETECTOR": detector,
            "PROGRAM": "99999",
            "DATE-BEG": "2023-01-01T00:00:00.000",
            "DATE-END": "2023-01-01T01:00:00.000",
            "TIMESYS": "UTC",
            "CAL_VER": "1.12.5",
            "TARG_RA": 53.16,
            "TARG_DEC": -27.78,
            "R_FLAT": "crds://jwst_nircam_flat_0001.fits",
            **spectral,
        }
    )
    return p


def _bytes(hdul: fits.HDUList) -> bytes:
    buf = io.BytesIO()
    hdul.writeto(buf)
    return buf.getvalue()


def image(
    *,
    filename: str = "jw99999-o001_t001_nircam_clear-f200w_i2d.fits",
    wcs: bool = True,
    bunit: str = "MJy/sr",
    with_err: bool = True,
) -> bytes:
    sci = fits.ImageHDU(np.zeros((32, 32), dtype="f4"), name="SCI")
    sci.header["BUNIT"] = bunit
    if wcs:
        _wcs(sci.header)
    hdus = [_primary("NIRCAM", "MULTIPLE", {"FILTER": "F200W", "PUPIL": "CLEAR"}), sci]
    if with_err:
        err = fits.ImageHDU(np.zeros((32, 32), dtype="f4"), name="ERR")
        err.header["BUNIT"] = bunit
        hdus.append(err)
    hdus.append(fits.ImageHDU(np.zeros((32, 32), dtype="u4"), name="DQ"))
    hdus.append(fits.ImageHDU(np.zeros((32, 32), dtype="f4"), name="VAR_POISSON"))
    hdus.append(fits.ImageHDU(np.zeros((32, 32), dtype="f4"), name="WHT"))
    return _bytes(fits.HDUList(hdus))


def spectrum() -> bytes:
    cols = [
        fits.Column(name="WAVELENGTH", format="D", unit="um", array=np.linspace(1, 5, 8)),
        fits.Column(name="FLUX", format="D", unit="Jy", array=np.zeros(8)),
        fits.Column(name="FLUX_ERROR", format="D", unit="Jy", array=np.zeros(8)),
        fits.Column(name="DQ", format="J", array=np.zeros(8, dtype="i4")),
    ]
    table = fits.BinTableHDU.from_columns(cols, name="EXTRACT1D")
    return _bytes(fits.HDUList([_primary("NIRSPEC", "NRS1", {"GRATING": "PRISM", "FILTER": "CLEAR"}), table]))


@dataclass(frozen=True)
class SyntheticProduct:
    raw: RawProduct
    reference: ReferenceRecord


def raw_product(
    data: bytes,
    filename: str,
    *,
    product_type: str = "SCIENCE",
    calib_level: int | None = 3,
    obscore: Mapping[str, Any] | None = None,
    media_type: str = "application/fits",
    links: tuple[Mapping[str, Any], ...] = (),
) -> RawProduct:
    ref = ProductRef("mast", f"synthetic/{filename}", f"https://example.invalid/synthetic/{filename}")
    fetched = FetchedProduct(ref, data, media_type, "mast:caom", "public domain (synthetic)", "synthetic", ref.uri)
    inventory: dict[str, Any] = {
        "productFilename": filename,
        "productType": product_type,
        "dataRights": "PUBLIC",
        "size": len(data),
        "obs_id": filename.rsplit("_", 1)[0],
        "proposal_id": "99999",
    }
    if calib_level is not None:
        inventory["calib_level"] = calib_level
    return RawProduct(
        fetched,
        _sha(data),
        "synthetic",
        inventory,
        dict(obscore or {"obs_collection": "JWST", "s_ra": 53.16, "s_dec": -27.78}),
        links,
    )


def synthetic_i2d() -> SyntheticProduct:
    name = "jw99999-o001_t001_nircam_clear-f200w_i2d.fits"
    links = (
        {"ID": "mast:JWST/product/jw99999001001_02101_00001_nrca1_cal.fits", "semantics": "#progenitor"},
        {"ID": "mast:JWST/product/jw99999001001_02101_00002_nrca1_cal.fits", "semantics": "#progenitor"},
    )
    raw = raw_product(
        image(),
        name,
        obscore={"obs_collection": "JWST", "instrument_name": "NIRCAM", "s_ra": 53.16, "s_dec": -27.78},
        links=links,
    )
    ref = ReferenceRecord(
        product_id=f"mast:JWST/product/{name}",
        product_type=ProductType.IMAGE,
        mission="JWST",
        instrument="NIRCAM",
        detector="MULTIPLE",
        filter_or_grating="F200W",
        frame="ICRS",
        time_scale="UTC",
        time_start_mjd=59945.0,
        time_end_mjd=59945.0 + 1 / 24,
        units="MJy/sr",
        calibration_level=3,
        extensions={"SCI": "data", "ERR": "error", "DQ": "quality", "VAR_POISSON": "variance", "WHT": "other"},
        has_wcs=True,
        progenitors=tuple(str(link["ID"]) for link in links),
        documentation=DOC,
        status=ReferenceStatus.SYNTHETIC,
    )
    return SyntheticProduct(raw, ref)


def synthetic_x1d() -> SyntheticProduct:
    name = "jw99999-o002_s00001_nirspec_clear-prism_x1d.fits"
    raw = raw_product(
        spectrum(), name, calib_level=None, obscore={"obs_collection": "JWST", "instrument_name": "NIRSPEC/MSA"}
    )
    ref = ReferenceRecord(
        product_id=f"mast:JWST/product/{name}",
        product_type=ProductType.SPECTRUM,
        mission="JWST",
        instrument="NIRSPEC",
        detector="NRS1",
        filter_or_grating="PRISM",
        frame="ICRS",
        time_scale="UTC",
        units="Jy",
        calibration_level=3,
        extensions={"EXTRACT1D:FLUX": "data", "EXTRACT1D:FLUX_ERROR": "error", "EXTRACT1D:DQ": "quality"},
        has_wcs=False,
        documentation=DOC,
        status=ReferenceStatus.SYNTHETIC,
    )
    return SyntheticProduct(raw, ref)


def load_reference_products(root: Path = REFERENCE_ROOT) -> list[tuple[str, RawProduct, ReferenceRecord]]:
    """Recorded reference products: one directory each with the raw file, ``reference.json``,
    ``obscore.json``, ``inventory.json`` and, when present, ``datalink.json``."""
    out: list[tuple[str, RawProduct, ReferenceRecord]] = []
    if not root.is_dir():
        return out
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        ref = ReferenceRecord.from_document(json.loads((d / "reference.json").read_text()))
        inventory = json.loads((d / "inventory.json").read_text())
        obscore = json.loads((d / "obscore.json").read_text())
        links = tuple(json.loads((d / "datalink.json").read_text())) if (d / "datalink.json").exists() else ()
        filename = inventory["productFilename"]
        data = (d / filename).read_bytes()
        pref = ProductRef("mast", str(inventory.get("dataURI", filename)), str(inventory.get("dataURL", filename)))
        fetched = FetchedProduct(
            pref, data, "application/fits", "mast:caom", "MAST data use policy", "Data from MAST", pref.uri
        )
        out.append(
            (
                d.name,
                RawProduct(fetched, _sha(data), str(inventory.get("rendered_query", "")), inventory, obscore, links),
                ref,
            )
        )
    return out

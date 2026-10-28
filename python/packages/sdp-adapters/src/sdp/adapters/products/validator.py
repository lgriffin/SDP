"""EnvelopeValidator: the semantic checks a filled envelope must pass before curation (v0.4.0 §3).

Each check is one ``SemanticCheck`` named after the envelope field it protects, so a failure names
the field (INT-006, INT-007). The validator re-reads the raw bytes itself; it trusts neither the
dialect nor the archive's metadata rows.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass

import astropy.units as u

from sdp.adapters.products.fits import HduInfo, merged_header, read_hdus
from sdp.kernel.domain.integration import (
    NON_SCIENCE,
    AccessStatus,
    ProductMetadata,
    ProductType,
    SemanticCheck,
    SemanticValidationReport,
)
from sdp.kernel.ports.integration import RawProduct
from sdp.kernel.ports.investigate import CuratedProduct

FITS_TYPES = ("application/fits", "image/fits")
#: FITS keyword -> (envelope attribute, ObsCore column). Keyword wins; any disagreement fails (v0.4.0 §3).
KEYWORD_FIELDS = {
    "TELESCOP": ("mission", "obs_collection"),
    "INSTRUME": ("instrument", "instrument_name"),
    "DETECTOR": ("detector", None),
}
WCS_TYPES = frozenset({ProductType.IMAGE, ProductType.CUBE})


def _same(a: object, b: object) -> bool:
    return str(a).strip().upper() == str(b).strip().upper()


@dataclass(frozen=True)
class EnvelopeValidator:
    validator_id: str = "envelope-validator"
    validator_version: str = "1.0.0"

    def validate(self, metadata: ProductMetadata, product: RawProduct) -> SemanticValidationReport:
        checks: list[SemanticCheck] = []

        def check(field: str, ok: bool, detail: str) -> None:
            checks.append(SemanticCheck(field, ok, detail))

        data = product.data
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        check(
            "checksum",
            digest == metadata.checksum == product.raw_artifact_id,
            "SHA-256 of the bytes read matches the envelope and the raw artifact"
            if digest == metadata.checksum == product.raw_artifact_id
            else f"bytes hash to {digest}; envelope says {metadata.checksum}",
        )
        inv_size = product.inventory_row.get("size")
        size_ok = metadata.size_bytes == len(data) and (inv_size is None or int(inv_size) == len(data))
        check("size_bytes", size_ok, f"{len(data)} bytes read; envelope {metadata.size_bytes}; inventory {inv_size}")

        inv_kind = str(product.inventory_row.get("productType", "")).upper()
        if inv_kind in ("PREVIEW", "AUXILIARY", "INFO"):
            check(
                "product_type",
                metadata.product_type in NON_SCIENCE,
                f"inventory says {inv_kind}; envelope says {metadata.product_type.value}",
            )
        else:
            check("product_type", True, metadata.product_type.value)

        check("frame", bool(metadata.frame), metadata.frame or "missing")
        check(
            "time_scale",
            metadata.time_scale not in ("", "UNKNOWN"),
            metadata.time_scale if metadata.time_scale != "UNKNOWN" else "time scale not stated by the product",
        )
        check("calibration_level", metadata.calibration_level is not None, str(metadata.calibration_level))
        check("access_status", metadata.access_status is AccessStatus.PUBLIC, metadata.access_status.value)
        check("rights.citation", bool(metadata.rights.citation and metadata.rights.licence), metadata.rights.citation)

        if metadata.media_type not in FITS_TYPES:
            check("format", metadata.product_type in NON_SCIENCE, f"{metadata.media_type} is not FITS")
            return self._report(metadata, checks)
        try:
            hdus = read_hdus(data)
        except ValueError as exc:
            check("format", False, str(exc))
            return self._report(metadata, checks)
        check("format", True, f"FITS with {len(hdus)} HDUs")
        self._keywords(metadata, product, merged_header(hdus), check)
        self._units(metadata, check)
        self._extensions(metadata, hdus, check)
        return self._report(metadata, checks)

    def validate_curation(
        self, metadata: ProductMetadata, product: RawProduct, curated: CuratedProduct
    ) -> SemanticValidationReport:
        """Refuse a curated representation that loses an extension, a WCS, an uncertainty array or a
        quality mask present in the raw product (INT-007)."""
        checks: list[SemanticCheck] = []
        if metadata.product_type in NON_SCIENCE:
            checks.append(
                SemanticCheck("product_type", False, f"{metadata.product_type.value} is never curated as science")
            )
            return self._report(metadata, checks)
        if curated.media_type not in FITS_TYPES:
            ok = metadata.product_type not in (ProductType.IMAGE, ProductType.SPECTRUM, ProductType.CUBE)
            checks.append(SemanticCheck("format", ok, f"curated as {curated.media_type}"))
            return self._report(metadata, checks)
        raw_hdus = {h.name: h for h in read_hdus(product.data)}
        cur = read_hdus(curated.data)
        names = {h.name for h in cur}
        for ext in metadata.extensions:
            hdu = ext.name.split(":", 1)[0]
            if ext.role in ("data", "error", "quality", "variance"):
                checks.append(
                    SemanticCheck(
                        f"extensions.{ext.name}", hdu in names, "kept" if hdu in names else f"{ext.role} extension lost"
                    )
                )
        if metadata.has_wcs:
            sci = next((h for h in cur if h.name == "SCI"), None)
            ok = bool(sci and sci.has_celestial_wcs())
            checks.append(SemanticCheck("has_wcs", ok, "WCS kept" if ok else "WCS lost"))
        if not checks:
            checks.append(SemanticCheck("extensions", bool(raw_hdus), "nothing to preserve"))
        return self._report(metadata, checks)

    def _keywords(
        self,
        metadata: ProductMetadata,
        product: RawProduct,
        header: dict[str, object],
        check: Callable[[str, bool, str], None],
    ) -> None:
        for kw, (attr, obscore_col) in KEYWORD_FIELDS.items():
            value = getattr(metadata, attr)
            if kw in header:
                check(attr, _same(header[kw], value), f"{kw}={header[kw]}, envelope {value}")
            if obscore_col and product.obscore_row.get(obscore_col) not in (None, "") and value is not None:
                col = product.obscore_row[obscore_col]
                # MAST's instrument_name carries the mode after a slash (NIRSPEC/MSA, NIRCAM/IMAGE).
                base = str(col).split("/", 1)[0]
                check(f"{attr}.obscore", _same(base, value), f"ObsCore {obscore_col}={col}, envelope {value}")
        spectral = header.get("GRATING") if header.get("GRATING") not in (None, "N/A") else header.get("FILTER")
        if spectral is not None:
            check(
                "filter_or_grating",
                _same(spectral, metadata.filter_or_grating),
                f"keyword {spectral}, envelope {metadata.filter_or_grating}",
            )

    def _units(self, metadata: ProductMetadata, check: Callable[[str, bool, str], None]) -> None:
        if metadata.product_type in NON_SCIENCE:
            return
        if not metadata.units:
            check("units", False, "no BUNIT or unit on the data")
            return
        try:
            u.Unit(metadata.units)
            check("units", True, metadata.units)
        except ValueError:
            check("units", False, f"unparseable unit {metadata.units!r}")

    def _extensions(
        self, metadata: ProductMetadata, hdus: tuple[HduInfo, ...], check: Callable[[str, bool, str], None]
    ) -> None:
        by_name = {h.name: h for h in hdus}
        listed = {e.name.split(":", 1)[0] for e in metadata.extensions}
        for ext in metadata.extensions:
            name, _, column = ext.name.partition(":")
            h = by_name.get(name)
            ok = h is not None and (not column or column in h.columns)
            check(f"extensions.{ext.name}", ok, "present" if ok else "named in the envelope but absent from the file")
        for h in hdus[1:]:
            if h.name in ("ERR", "DQ") or h.name.startswith("VAR_"):
                check(
                    f"extensions.{h.name}",
                    h.name in listed,
                    "recorded" if h.name in listed else "in the file, missing from the envelope",
                )
        if metadata.product_type in WCS_TYPES:
            sci = by_name.get("SCI")
            has = bool(sci and sci.has_celestial_wcs())
            check("has_wcs", has == metadata.has_wcs, f"file WCS {has}, envelope {metadata.has_wcs}")
        if metadata.product_type in (ProductType.IMAGE, ProductType.CUBE, ProductType.SPECTRUM):
            # Presence is recorded and absence is recorded as absence; neither fails (v0.4.0 §3).
            check("uncertainty", True, "present" if metadata.has_uncertainty else "absent, recorded as absent")

    def _report(self, metadata: ProductMetadata, checks: list[SemanticCheck]) -> SemanticValidationReport:
        return SemanticValidationReport(metadata.product_id, self.validator_id, self.validator_version, tuple(checks))

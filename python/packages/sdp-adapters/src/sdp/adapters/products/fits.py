"""Read the structure of a FITS file from bytes: per HDU its name, kind, header cards, shape and
columns. No memory mapping and no data arrays are kept; nothing is written back (ADR-018)."""

from __future__ import annotations

import io
import warnings
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from astropy.io import fits
from astropy.wcs import WCS, FITSFixedWarning


@dataclass(frozen=True)
class HduInfo:
    index: int
    name: str
    kind: str
    """``primary``, ``image``, ``table`` or ``other``."""
    header: Mapping[str, Any]
    shape: tuple[int, ...] = ()
    columns: tuple[str, ...] = ()
    column_units: Mapping[str, str] = field(default_factory=dict)
    column_ucds: Mapping[str, str] = field(default_factory=dict)
    """A table column's UCD where the format carries one (VOTable does; FITS tables do not)."""

    @property
    def has_data(self) -> bool:
        return bool(self.shape) or bool(self.columns)

    def has_celestial_wcs(self) -> bool:
        if not any(str(k).startswith("CTYPE") for k in self.header):
            return False
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FITSFixedWarning)
            try:
                return bool(WCS(fits.Header(dict(self.header))).has_celestial)
            except Exception:
                return False


def _plain(value: Any) -> Any:
    if isinstance(value, bool | int | float | str) or value is None:
        return value
    return str(value)


def read_hdus(data: bytes) -> tuple[HduInfo, ...]:
    """Every HDU's structure. Raises ``ValueError`` for bytes that are not FITS, or are FITS too
    malformed to read (a truncated file, an unparseable card, a non-integer NAXIS)."""
    try:
        return _read_hdus(data)
    except (OSError, KeyError, TypeError, IndexError, fits.VerifyError) as exc:
        raise ValueError(f"not a readable FITS file: {exc}") from exc


def _read_hdus(data: bytes) -> tuple[HduInfo, ...]:
    hdul = fits.open(io.BytesIO(data), memmap=False, lazy_load_hdus=False)
    out = []
    with hdul:
        for i, hdu in enumerate(hdul):
            header = {str(k): _plain(v) for k, v in hdu.header.items() if k not in ("", "COMMENT", "HISTORY")}
            name = (hdu.name or ("PRIMARY" if i == 0 else f"HDU{i}")).upper()
            if isinstance(hdu, fits.BinTableHDU | fits.TableHDU):
                cols = tuple(c.name.upper() for c in hdu.columns)
                units = {c.name.upper(): str(c.unit) for c in hdu.columns if c.unit}
                out.append(HduInfo(i, name, "table", header, (), cols, units))
            elif isinstance(hdu, fits.PrimaryHDU | fits.ImageHDU | fits.CompImageHDU):
                shape = tuple(int(header[f"NAXIS{k}"]) for k in range(int(header.get("NAXIS", 0)), 0, -1))
                if "ZNAXIS" in header:
                    shape = tuple(int(header[f"ZNAXIS{k}"]) for k in range(int(header["ZNAXIS"]), 0, -1))
                out.append(HduInfo(i, name, "primary" if i == 0 else "image", header, shape))
            else:
                out.append(HduInfo(i, name, "other", header))
    return tuple(out)


def merged_header(hdus: tuple[HduInfo, ...]) -> dict[str, Any]:
    """Primary keywords overlaid by the science extension's (the science extension wins)."""
    merged = dict(hdus[0].header) if hdus else {}
    sci = next((h for h in hdus if h.name == "SCI"), None)
    if sci is not None:
        merged.update(sci.header)
    return merged


FITS_MEDIA_TYPES = ("application/fits", "image/fits")
GENERIC_MEDIA_TYPES = ("", "application/octet-stream", "binary/octet-stream")
FITS_SIGNATURE = b"SIMPLE  ="


def effective_media_type(data: bytes, declared: str) -> str:
    """The declared media type, or ``application/fits`` when a server labels FITS bytes generically
    (archives often serve downloads as ``application/octet-stream``); never overrides a specific type."""
    declared = (declared or "").split(";")[0].strip().lower()
    if declared in GENERIC_MEDIA_TYPES and data[:80].startswith(FITS_SIGNATURE):
        return "application/fits"
    return declared or "application/octet-stream"

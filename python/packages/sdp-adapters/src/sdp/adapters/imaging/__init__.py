"""Image adapters for Investigate's image-first route: photutils behind SourceDetector and tweakwcs
behind WcsRefiner (Investigate §3, §7). Frames arrive as FITS bytes from the artifact store, never as
paths, and WCS travels as FITS header text."""

from __future__ import annotations

from astropy.io import fits
from astropy.wcs import WCS

FITS_MEDIA_TYPES = frozenset({"application/fits", "image/fits"})


def header_text(wcs: WCS) -> str:
    """The WCS as newline-separated FITS cards, the form DetectionList and RefinedWcs carry, with the
    image size as NAXISn so a refiner can check the WCS over the whole frame."""
    header = wcs.to_header(relax=True)
    if wcs.pixel_shape is not None:
        for axis, n in enumerate(wcs.pixel_shape, start=1):
            header[f"NAXIS{axis}"] = int(n)
    return str(header.tostring(sep="\n").strip())


def wcs_from_text(text: str) -> WCS:
    return WCS(fits.Header.fromstring(text, sep="\n"))

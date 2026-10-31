"""SourceDetector over photutils: a 2-D background, then DAOStarFinder on the subtracted image."""

from __future__ import annotations

import io
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
import photutils
from astropy.io import fits
from astropy.stats import SigmaClip
from astropy.time import Time
from astropy.wcs import WCS
from photutils.background import Background2D, MedianBackground
from photutils.detection import DAOStarFinder

from sdp.adapters.imaging import FITS_MEDIA_TYPES, header_text, wcs_from_text
from sdp.kernel.domain.errors import DomainError
from sdp.kernel.ports.investigate import Detection, DetectionList, ImageFrame

DEFAULTS: Mapping[str, Any] = {
    "fwhm": 3.0,
    "nsigma": 5.0,
    "box_size": 64,
    "filter_size": 3,
    "exclude_percentile": 10.0,
    "hdu": None,
}


#: Header keywords for the mid-exposure time, best first; DATE-OBS is the fallback.
EPOCH_KEYWORDS = ("MJD-AVG", "MJD-MID")
#: Start-of-exposure keywords, read only when the header gives no midpoint and no start and end.
START_KEYWORDS = ("MJD-BEG", "MJD-OBS")
#: FITS TIMESYS values astropy does not take as a scale, and the scale each one means (GPS is TAI
#: less 19 s, below anything an epoch comparison resolves).
TIME_SCALES = {"ut": "utc", "gmt": "utc", "tdt": "tt", "et": "tt", "gps": "tai", "local": "utc"}


def header_epoch_mjd(*headers: fits.Header) -> float | None:
    """The observation time a FITS image names, as MJD (mid-exposure where it can tell), or None."""
    for key in EPOCH_KEYWORDS:
        for h in headers:
            if h.get(key) is not None:
                return float(h[key])
    # the middle of the exposure where the header gives its start and end: HST's EXPSTART/EXPEND (MJD),
    # or the ISO DATE-BEG/DATE-END in the header's TIMESYS (UTC when it names none)
    for h in headers:
        if h.get("EXPSTART") is not None and h.get("EXPEND") is not None:
            return (float(h["EXPSTART"]) + float(h["EXPEND"])) / 2.0
    for h in headers:
        if h.get("DATE-BEG") and h.get("DATE-END"):
            scale = str(h.get("TIMESYS") or "utc").strip().lower()
            scale = TIME_SCALES.get(scale, scale if scale in Time.SCALES else "utc")
            beg, end = Time(str(h["DATE-BEG"]), scale=scale), Time(str(h["DATE-END"]), scale=scale)
            return float((beg.utc.mjd + end.utc.mjd) / 2.0)
    for key in START_KEYWORDS:
        for h in headers:
            if h.get(key) is not None:
                return float(h[key])
    for h in headers:
        if h.get("DATE-OBS"):
            stamp = str(h["DATE-OBS"])
            if h.get("TIME-OBS") and "T" not in stamp:
                stamp = f"{stamp}T{h['TIME-OBS']}"
            return float(Time(stamp, format="isot", scale="utc").mjd)
    return None


def image_headers(frame: ImageFrame, hdu: int | None = None) -> tuple[fits.Header, fits.Header]:
    """The primary header and the header of the image read_image reads: the HDU named, else the first 2-D one."""
    with fits.open(io.BytesIO(frame.data), memmap=False) as hdus:
        if hdu is not None:
            image = hdus[hdu]
        else:
            image = next((h for h in hdus if h.data is not None and getattr(h.data, "ndim", 0) == 2), hdus[0])
        return hdus[0].header.copy(), image.header.copy()


def read_image(frame: ImageFrame, hdu: int | None = None) -> tuple[np.ndarray, WCS]:
    """The first 2-D image HDU (or the one named) and its WCS; ``frame.wcs`` overrides the header's."""
    if frame.media_type not in FITS_MEDIA_TYPES:
        raise DomainError(f"photutils detector reads FITS; {frame.artifact_id} is {frame.media_type}")
    with fits.open(io.BytesIO(frame.data), memmap=False) as hdus:
        candidates = [hdus[hdu]] if hdu is not None else [h for h in hdus if h.data is not None and h.data.ndim == 2]
        if not candidates:
            raise DomainError(f"{frame.artifact_id} has no 2-D image")
        image = candidates[0]
        data = np.asarray(image.data, dtype=float)
        wcs = wcs_from_text(frame.wcs.decode()) if frame.wcs else WCS(image.header)
    if not wcs.has_celestial:
        raise DomainError(f"{frame.artifact_id} has no celestial WCS")
    celestial = wcs.celestial
    celestial.pixel_shape = (data.shape[1], data.shape[0])
    return data, celestial


@dataclass(frozen=True)
class PhotutilsDetector:
    """Parameters (all optional): ``fwhm`` in pixels, ``nsigma`` above the background RMS,
    ``box_size`` and ``filter_size`` of the background mesh, ``exclude_percentile`` (the share of a
    mesh box that may be masked or clipped before the box is left out: raise it for a crowded star
    field, where stars fill more than a tenth of every box), and ``hdu``."""

    @property
    def detector_id(self) -> str:
        return f"photutils-{photutils.__version__}"

    def detect(self, frame: ImageFrame, params: Mapping[str, Any]) -> DetectionList:
        p = {**DEFAULTS, **params}
        data, wcs = read_image(frame, p["hdu"])
        primary, sci = image_headers(frame, p["hdu"])
        finite = np.isfinite(data)
        box = int(min(p["box_size"], *data.shape))
        background = Background2D(
            data,
            box,
            filter_size=int(p["filter_size"]),
            sigma_clip=SigmaClip(sigma=3.0),
            bkg_estimator=MedianBackground(),
            mask=~finite,
            exclude_percentile=float(p["exclude_percentile"]),
        )
        subtracted = np.where(finite, data - background.background, 0.0)
        rms = float(background.background_rms_median)
        finder = DAOStarFinder(fwhm=float(p["fwhm"]), threshold=float(p["nsigma"]) * rms)
        # photutils fills a magnitude column from every flux, and a faint source on a real image can
        # have a negative one; that log10 is photutils' own bookkeeping, not a result SDP reads, so it
        # is not raised under the kernel's floating-point policy.
        with np.errstate(divide="ignore", invalid="ignore"):
            table = finder(subtracted, mask=~finite)
        detections: list[Detection] = []
        if table is not None:
            x = np.asarray(table["x_centroid"], dtype=float)
            y = np.asarray(table["y_centroid"], dtype=float)
            ra, dec = wcs.pixel_to_world_values(x, y)
            for i in np.lexsort((x, y)):
                detections.append(
                    Detection(
                        float(x[i]),
                        float(y[i]),
                        float(ra[i]) % 360.0,
                        float(dec[i]),
                        float(table["flux"][i]),
                        float(table["peak"][i]),
                    )
                )
        return DetectionList(
            detections=tuple(detections),
            wcs_header=header_text(wcs),
            background=float(background.background_median),
            background_rms=rms,
            detector_id=self.detector_id,
            params={k: v for k, v in p.items() if v is not None},
            epoch_mjd=header_epoch_mjd(sci, primary),
            instrument=str(primary.get("INSTRUME") or sci.get("INSTRUME") or ""),
        )

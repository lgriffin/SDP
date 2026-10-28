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
from astropy.wcs import WCS
from photutils.background import Background2D, MedianBackground
from photutils.detection import DAOStarFinder

from sdp.adapters.imaging import FITS_MEDIA_TYPES, header_text, wcs_from_text
from sdp.kernel.domain.errors import DomainError
from sdp.kernel.ports.investigate import Detection, DetectionList, ImageFrame

DEFAULTS: Mapping[str, Any] = {"fwhm": 3.0, "nsigma": 5.0, "box_size": 64, "filter_size": 3, "hdu": None}


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
    ``box_size`` and ``filter_size`` of the background mesh, and ``hdu``."""

    @property
    def detector_id(self) -> str:
        return f"photutils-{photutils.__version__}"

    def detect(self, frame: ImageFrame, params: Mapping[str, Any]) -> DetectionList:
        p = {**DEFAULTS, **params}
        data, wcs = read_image(frame, p["hdu"])
        finite = np.isfinite(data)
        box = int(min(p["box_size"], *data.shape))
        background = Background2D(
            data,
            box,
            filter_size=int(p["filter_size"]),
            sigma_clip=SigmaClip(sigma=3.0),
            bkg_estimator=MedianBackground(),
            mask=~finite,
        )
        subtracted = np.where(finite, data - background.background, 0.0)
        rms = float(background.background_rms_median)
        finder = DAOStarFinder(fwhm=float(p["fwhm"]), threshold=float(p["nsigma"]) * rms)
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
        )

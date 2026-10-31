"""ForcedPhotometer over photutils: circular sky apertures at given positions, with a local
background from an annulus around each, for multi-epoch change detection (INT-025).

The aperture is given in arcseconds and turned into pixels with each image's own scale, so one patch
of sky is measured on every image of a field whatever its resolution. The error is what the pixels
show: the annulus scatter over the aperture's area, the error of the annulus median, and, when the
profile gives the image's ``gain`` (electrons per data unit), the source's photon noise.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import photutils
from astropy.io import fits
from astropy.stats import SigmaClip
from astropy.wcs import WCS
from astropy.wcs.utils import proj_plane_pixel_scales
from photutils.aperture import ApertureStats, CircularAnnulus, CircularAperture

from sdp.adapters.imaging.photutils_detector import header_epoch_mjd, image_headers, read_image
from sdp.kernel.ports.investigate import (
    MASKED,
    MEASURED,
    OFF_IMAGE,
    SATURATED,
    ForcedMeasurement,
    ForcedPhotometry,
    FrameDescription,
    ImageFrame,
    SkyPoint,
)

#: Header keywords naming the filter, best first. ACS names two wheels; WFPC2 names FILTNAM1/2; NIRCam
#: and NIRISS can carry the filter in the light path in the pupil wheel (F444W with F405N in PUPIL;
#: NIRISS's FILTER=CLEAR with the filter in PUPIL).
FILTER_KEYWORDS = ("FILTER", "FILTER1", "FILTER2", "FILTNAM1", "FILTNAM2", "PUPIL")
#: Wheel positions that are not filters: the clear positions, masks and grisms.
NOT_A_FILTER = ("CLEAR", "GRISM", "MASK", "NRM")
#: The S/N a source needs before its width counts towards the image's FWHM.
FWHM_MIN_SNR = 20.0
#: The error of a median over n samples of a normal distribution is sqrt(pi/2) times that of the mean.
MEDIAN_VARIANCE = math.pi / 2


def header_band(*headers: fits.Header) -> str:
    """The filter the headers name, upper case, with the clear and empty wheel positions dropped; two
    filters in the beam (ACS's two wheels, or NIRCam's filter and pupil wheels) are joined with '+'.
    "" when the headers name none."""
    seen: list[str] = []
    for key in FILTER_KEYWORDS:
        for h in headers:
            value = str(h.get(key) or "").strip().upper()
            if key == "PUPIL" and not (value.startswith("F") and value[1:2].isdigit()):
                continue
            if (
                value
                and not value.startswith(NOT_A_FILTER)
                and value not in {"N/A", "NONE", "OPEN"}
                and value not in seen
            ):
                seen.append(value)
    return "+".join(seen)


def pixel_arcsec(wcs: WCS) -> float:
    return float(np.mean(proj_plane_pixel_scales(wcs.celestial))) * 3600.0


def saturation_level(params: Mapping[str, Any], *headers: fits.Header) -> float | None:
    """The profile's ``saturation`` when it gives one, else the header's SATURATE, else none."""
    if params.get("saturation") is not None:
        return float(params["saturation"])
    for h in headers:
        if h.get("SATURATE") is not None:
            return float(h["SATURATE"])
    return None


@dataclass(frozen=True)
class PhotutilsPhotometer:
    """Parameters (all optional): ``hdu``; ``fwhm_px``, the expected FWHM in pixels, which sizes the
    aperture ``describe`` measures widths in; ``saturation``, the level at or above which a pixel is
    saturated (else the header's SATURATE); ``gain`` in electrons per data unit, for photon noise."""

    @property
    def photometer_id(self) -> str:
        return f"photutils-aperture-{photutils.__version__}"

    def describe(self, frame: ImageFrame, positions: Sequence[SkyPoint], params: Mapping[str, Any]) -> FrameDescription:
        hdu = params.get("hdu")
        data, wcs = read_image(frame, hdu)
        primary, sci = image_headers(frame, hdu)
        scale = pixel_arcsec(wcs)
        saturation = saturation_level(params, sci, primary)
        fwhm = None
        if positions:
            r = max(2.5 * float(params.get("fwhm_px", 3.0)), 2.0)
            got = self._stats(data, wcs, positions, r, (2.0 * r, 3.0 * r), saturation, None)
            widths = [w for _, flux, err, status, w in got if status == MEASURED and w and flux > FWHM_MIN_SNR * err]
            fwhm = float(np.median(widths)) * scale if widths else None
        return FrameDescription(
            band=header_band(sci, primary),
            epoch_mjd=header_epoch_mjd(sci, primary),
            instrument=str(primary.get("INSTRUME") or sci.get("INSTRUME") or ""),
            pixel_arcsec=scale,
            fwhm_arcsec=fwhm,
            saturation=saturation,
        )

    def measure(
        self,
        frame: ImageFrame,
        positions: Sequence[SkyPoint],
        radius_arcsec: float,
        annulus_arcsec: tuple[float, float],
        params: Mapping[str, Any],
    ) -> ForcedPhotometry:
        hdu = params.get("hdu")
        data, wcs = read_image(frame, hdu)
        primary, sci = image_headers(frame, hdu)
        scale = pixel_arcsec(wcs)
        gain = float(params["gain"]) if params.get("gain") else None
        saturation = saturation_level(params, sci, primary)
        # one pass over the pixels per distinct aperture, the image parsed once for all of them
        by_radius: dict[float, list[int]] = {}
        for i, p in enumerate(positions):
            by_radius.setdefault(p.radius_arcsec or radius_arcsec, []).append(i)
        got: list[tuple[str, float, float, str, float | None]] = [("", 0.0, 0.0, "", None)] * len(positions)
        for r, idx in sorted(by_radius.items()):
            wider = r - radius_arcsec
            for i, row in zip(
                idx,
                self._stats(
                    data,
                    wcs,
                    [positions[i] for i in idx],
                    r / scale,
                    ((annulus_arcsec[0] + wider) / scale, (annulus_arcsec[1] + wider) / scale),
                    saturation,
                    gain,
                ),
                strict=True,
            ):
                got[i] = row
        return ForcedPhotometry(
            measurements=tuple(
                ForcedMeasurement(
                    p.key,
                    flux,
                    err,
                    status,
                    None if width is None else width * scale,
                )
                for p, (_, flux, err, status, width) in zip(positions, got, strict=True)
            ),
            radius_arcsec=float(radius_arcsec),
            annulus_arcsec=(float(annulus_arcsec[0]), float(annulus_arcsec[1])),
            photometer_id=self.photometer_id,
            params={k: v for k, v in params.items() if v is not None},
        )

    def _stats(
        self,
        data: np.ndarray,
        wcs: WCS,
        positions: Sequence[SkyPoint],
        r_px: float,
        annulus_px: tuple[float, float],
        saturation: float | None,
        gain: float | None,
    ) -> list[tuple[str, float, float, str, float | None]]:
        """(key, flux, error, status, FWHM in pixels) per position, in the order given."""
        if not positions:
            return []
        x, y = wcs.world_to_pixel_values(
            np.array([p.ra_deg for p in positions], dtype=float), np.array([p.dec_deg for p in positions], dtype=float)
        )
        xy = np.column_stack([np.atleast_1d(x), np.atleast_1d(y)])
        # Drizzled HST mosaics fill sky no exposure covered with exactly 0, not NaN: blank, not dark sky
        bad = ~np.isfinite(data) | (data == 0.0)
        filled = np.where(bad, 0.0, data)
        ny, nx = data.shape
        aperture = CircularAperture(xy, r=r_px)
        annulus = CircularAnnulus(xy, r_in=annulus_px[0], r_out=annulus_px[1])
        background = ApertureStats(filled, annulus, mask=bad, sigma_clip=SigmaClip(sigma=3.0))
        bkg_median = np.atleast_1d(np.asarray(background.median, dtype=float))
        bkg_std = np.atleast_1d(np.asarray(background.std, dtype=float))
        bkg_n = np.atleast_1d(np.asarray(background.sum_aper_area.value, dtype=float))
        stats = ApertureStats(filled, aperture, mask=bad, local_bkg=np.nan_to_num(bkg_median))
        sums = np.atleast_1d(np.asarray(stats.sum, dtype=float))
        areas = np.atleast_1d(np.asarray(stats.sum_aper_area.value, dtype=float))
        widths = np.atleast_1d(np.asarray(stats.fwhm.value, dtype=float))
        out: list[tuple[str, float, float, str, float | None]] = []
        for i, p in enumerate(positions):
            xi, yi = float(xy[i, 0]), float(xy[i, 1])
            # the background annulus too must lie on the image, or an edge star's background would come
            # from a part of the annulus that differs from image to image
            reach = max(r_px, annulus_px[1])
            inside = math.isfinite(xi) and math.isfinite(yi)
            inside = (
                inside
                and xi - reach >= -0.5
                and xi + reach <= nx - 0.5
                and yi - reach >= -0.5
                and yi + reach <= ny - 0.5
            )
            if not inside:
                out.append((p.key, 0.0, 0.0, OFF_IMAGE, None))
                continue
            cut = aperture[i].to_mask(method="center").cutout(data, fill_value=np.nan)
            weights = aperture[i].to_mask(method="center").data > 0
            pixels = cut[weights] if cut is not None else np.array([np.nan])
            usable = math.isfinite(bkg_median[i]) and math.isfinite(bkg_std[i]) and bkg_std[i] > 0
            if not np.all(np.isfinite(pixels)) or np.any(pixels == 0.0) or not usable:
                out.append((p.key, 0.0, 0.0, MASKED, None))
                continue
            if saturation is not None and float(np.max(pixels)) >= saturation:
                out.append((p.key, float(sums[i]), 0.0, SATURATED, None))
                continue
            flux, area = float(sums[i]), float(areas[i])
            variance = area * bkg_std[i] ** 2 + area**2 * bkg_std[i] ** 2 * MEDIAN_VARIANCE / max(bkg_n[i], 1.0)
            if gain:
                variance += max(flux, 0.0) / gain
            width = float(widths[i]) if math.isfinite(widths[i]) and widths[i] > 0 else None
            out.append((p.key, flux, float(math.sqrt(variance)), MEASURED, width))
        return out

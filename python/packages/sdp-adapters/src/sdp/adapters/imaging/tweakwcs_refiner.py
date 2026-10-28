"""WcsRefiner over tweakwcs: match detections to a reference catalogue in the tangent plane and fit
a shift (or rshift, rscale, general) correction to the WCS."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
import tweakwcs
from astropy.table import Table
from astropy.wcs import WCS
from astropy.wcs.utils import proj_plane_pixel_scales
from tweakwcs.correctors import FITSWCSCorrector
from tweakwcs.matchutils import XYXYMatch

from sdp.adapters.imaging import header_text, wcs_from_text
from sdp.kernel.ports.investigate import Detection, DetectionList, RefinedWcs

DEFAULTS: Mapping[str, Any] = {"fitgeom": "shift", "searchrad": 5.0, "separation": 0.5, "tolerance": 2.0, "minobj": 3}


@dataclass(frozen=True)
class TweakwcsRefiner:
    """Parameters (all optional): ``fitgeom``; ``searchrad``, ``separation`` and ``tolerance`` in
    arcseconds for the matcher; ``minobj``, the fewest matches a fit needs. Too few matches leaves
    the WCS as it was and reports zero matches."""

    @property
    def refiner_id(self) -> str:
        return f"tweakwcs-{tweakwcs.__version__}"

    def refine(
        self,
        detections: DetectionList,
        reference_ra: np.ndarray,
        reference_dec: np.ndarray,
        params: Mapping[str, Any],
    ) -> RefinedWcs:
        p = {**DEFAULTS, **params}
        wcs = wcs_from_text(detections.wcs_header)
        dets = detections.detections
        unchanged = RefinedWcs(detections.wcs_header, 0.0, 0, self.refiner_id, dets)
        if len(dets) < int(p["minobj"]) or len(reference_ra) < int(p["minobj"]):
            return unchanged
        imcat = Table({"x": [d.x for d in dets], "y": [d.y for d in dets]})
        corrector = FITSWCSCorrector(wcs, meta={"catalog": imcat, "name": "frame"})
        refcat = Table({"RA": np.asarray(reference_ra, float), "DEC": np.asarray(reference_dec, float)})
        match = XYXYMatch(
            searchrad=float(p["searchrad"]),
            separation=float(p["separation"]),
            tolerance=float(p["tolerance"]),
            use2dhist=True,
        )
        try:
            tweakwcs.align_wcs(
                corrector, refcat=refcat, fitgeom=str(p["fitgeom"]), match=match, minobj=int(p["minobj"])
            )
        except (ValueError, RuntimeError):
            return unchanged
        info = corrector.meta.get("fit_info", {})
        if info.get("status") != "SUCCESS":
            return unchanged
        refined = corrector.wcs
        ra, dec = refined.pixel_to_world_values([d.x for d in dets], [d.y for d in dets])
        moved = tuple(
            Detection(d.x, d.y, float(r) % 360.0, float(de), d.flux, d.peak)
            for d, r, de in zip(dets, np.atleast_1d(ra), np.atleast_1d(dec), strict=True)
        )
        return RefinedWcs(
            wcs_header=header_text(refined),
            rms_arcsec=_rms_arcsec(info, refined),
            matches=int(info.get("nmatches", 0)),
            refiner_id=self.refiner_id,
            detections=moved,
        )


def _rms_arcsec(info: Mapping[str, Any], wcs: WCS) -> float:
    """tweakwcs reports the fit's RMSE in the image's pixels; scale it by the mean pixel size."""
    rmse = info.get("rmse")
    if rmse is None:
        return 0.0
    scale = float(np.mean(proj_plane_pixel_scales(wcs))) * 3600.0
    return float(np.max(np.atleast_1d(rmse))) * scale

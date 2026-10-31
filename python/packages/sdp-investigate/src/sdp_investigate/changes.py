"""Multi-epoch change detection: one field's images, years or decades apart, compared with each other
(INT-025).

Every Gaia source is measured on every image of the field by forced photometry, at its position
propagated to that image's date, so nothing depends on what a detector happened to find. Three things
differ from image to image, and each is handled before two images are compared:

- **alignment**: each image is first shifted onto Gaia at its own date (``align_wcs``, the same single
  shift the image-counterpart test fits), so a frame's archival astrometry cannot put the aperture
  beside its star;
- **resolution**: every image in a comparison is measured in one aperture, in arcseconds, sized to the
  coarsest image's FWHM, and sources closer than two apertures are measured together as a blend, so a
  pair one telescope splits and another merges is summed the same way on both;
- **calibration**: each image's zero point is fitted against the field's own stars, relative to the
  deepest image in its band, and its error goes into every comparison. With the aperture fixed in
  arcseconds, the share of a star's light it holds is a constant per image, which the zero point
  absorbs.

Only images in the same band are compared, unless the profile declares two bands equivalent and gives
the systematic that costs (``bands``, ``band_systematic_mag``).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from astropy.time import Time
from scipy import stats

from sdp.kernel.domain.association import CatalogueSource
from sdp.kernel.domain.errors import PlanInvalid
from sdp.kernel.domain.investigation import EvidenceBundle, Subject
from sdp.kernel.ports.investigate import (
    MEASURED,
    ForcedMeasurement,
    ForcedPhotometer,
    FrameDescription,
    ImageFrame,
    SkyPoint,
    SourceDetector,
)
from sdp.kernel.science.association import propagate
from sdp.kernel.science.investigation import product_bytes
from sdp.kernel.science.plan import TaskContext
from sdp_investigate.base import Features, FieldTest, Scored, catalogue_row, finite, robust_sigma
from sdp_investigate.imaging import DETECTOR_MESH, IMAGE, align, anchor
from sdp_investigate.photometry import MIN_FIELD_CURVES, error_scale

#: d(flux)/flux per magnitude: 0.4 ln 10.
FRACTION_PER_MAG = 0.4 * math.log(10.0)
FWHM_PER_SIGMA = 2.0 * math.sqrt(2.0 * math.log(2.0))
#: Below this signal-to-noise on an image, a source counts as not seen there.
ABSENT_SNR = 2.0
#: The standard error of a median is this times sigma / sqrt(n), for normal samples.
MEDIAN_ERROR = math.sqrt(math.pi / 2.0)
STEADY, CHANGED, APPEARED, VANISHED = "steady", "changed", "appeared", "vanished"
UNFILTERED = "unfiltered"
#: The G magnitude a source without one is weighted as in a blend: fainter than Gaia's limit.
UNKNOWN_G = 21.5


@dataclass(frozen=True)
class Frame:
    """One image as the test measures it: aligned, described and dated."""

    product_id: str
    artifact_id: str
    frame: ImageFrame
    description: FrameDescription
    epoch_jyear: float
    band: str
    fwhm_arcsec: float
    alignment: Mapping[str, Any] | None


@dataclass(frozen=True)
class Series:
    """One source's (or blend's) calibrated fluxes in one band, image by image, in time order."""

    key: str
    band: str
    images: tuple[str, ...]
    epoch_mjd: np.ndarray
    flux: np.ndarray
    err: np.ndarray
    zp_frac: np.ndarray
    band_frac: float
    blend_of: tuple[str, ...] = ()
    left_out: Mapping[str, str] = field(default_factory=dict)


def band_of(description: FrameDescription, bands: Mapping[str, str]) -> str:
    """The band an image is compared in: the profile's name for its filter when ``bands`` gives one."""
    raw = description.band or UNFILTERED
    return str(bands.get(raw, raw))


def aperture(fwhm_max: float, factor: float) -> tuple[float, tuple[float, float]]:
    """The aperture radius and the background annulus for a comparison whose coarsest image has this
    FWHM: the annulus starts 1.5 FWHM outside the aperture, where a Gaussian star has no light left,
    and is 2 FWHM wide."""
    r = factor * fwhm_max
    inner = r + 1.5 * fwhm_max
    return r, (inner, inner + 2.0 * fwhm_max)


def enclosed(radius: float, fwhm: float) -> float:
    """The share of a Gaussian star's light within ``radius`` of its centre."""
    sigma = fwhm / FWHM_PER_SIGMA
    return 1.0 - math.exp(-(radius**2) / (2.0 * sigma**2))


def enclosed_offset(radius: float, offset: float, fwhm: float) -> float:
    """The share of a Gaussian star's light within ``radius`` of a point ``offset`` from its centre:
    the non-central chi-squared law with two degrees of freedom (Marcum's Q)."""
    sigma = fwhm / FWHM_PER_SIGMA
    return (
        float(stats.ncx2.cdf((radius / sigma) ** 2, 2, (offset / sigma) ** 2)) if offset > 0 else enclosed(radius, fwhm)
    )


def offset_arcsec(ra1: float, dec1: float, ra2: float, dec2: float) -> tuple[float, float]:
    cosd = math.cos(math.radians((dec1 + dec2) / 2.0))
    return (((ra2 - ra1 + 180.0) % 360.0 - 180.0) * cosd * 3600.0, (dec2 - dec1) * 3600.0)


def blend_groups(positions: Sequence[Mapping[str, tuple[float, float]]], limit_arcsec: float) -> list[list[str]]:
    """Sources closer than ``limit_arcsec`` on any image, joined into groups (each sorted, groups
    sorted): a source alone is a group of one."""
    keys = sorted({k for p in positions for k in p})
    parent = {k: k for k in keys}

    def root(k: str) -> str:
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    for p in positions:
        ordered = sorted(p.items())
        for i, (a, (ra_a, dec_a)) in enumerate(ordered):
            for b, (ra_b, dec_b) in ordered[i + 1 :]:
                if math.hypot(*offset_arcsec(ra_a, dec_a, ra_b, dec_b)) < limit_arcsec:
                    parent[root(a)] = root(b)
    groups: dict[str, list[str]] = {}
    for k in keys:
        groups.setdefault(root(k), []).append(k)
    return sorted(sorted(g) for g in groups.values())


def zero_point(delta_mag: np.ndarray, clip: float = 3.0) -> tuple[float, float, int]:
    """The clipped median of ``m_image - m_reference`` over the calibrators, its standard error and
    how many survived the clipping: a variable star among them is clipped, not averaged in."""
    kept = np.asarray(delta_mag, dtype=float)
    for _ in range(10):
        centre, spread = float(np.median(kept)), robust_sigma(kept)
        survivors = kept[np.abs(kept - centre) <= clip * spread]
        if len(survivors) == len(kept) or len(survivors) < 3:
            break
        kept = survivors
    centre, spread = float(np.median(kept)), robust_sigma(kept)
    return centre, MEDIAN_ERROR * spread / math.sqrt(len(kept)), len(kept)


class EpochChange(FieldTest):
    """Has a source changed between images of the field taken years or decades apart?

    Each image is aligned to Gaia at its own date, described by the ``photometer`` service (band,
    pixel scale, FWHM), and every Gaia source is measured on it by forced photometry in one aperture
    per band, sized to the band's coarsest image. Each image is calibrated against the band's deepest
    image by the clipped median magnitude difference over unblended stars above ``calibrator_snr``.
    The statistic is the χ² of a source's calibrated fluxes about their weighted mean, with each
    image's own error, its zero-point error and any declared band systematic, scaled by the field's
    scatter (``error_scale``); p from χ² with n − 1 degrees of freedom. The details give every image's
    flux, the largest change and its z, and a verdict: steady, changed, appeared or vanished.
    """

    name = "epoch-change"
    question = "Has the source changed between images taken years or decades apart?"
    null_model = "constant sources with each image's own noise and zero-point error, scaled as the field is"
    inputs = ("catalogue:gaia", "product:image")
    needs_products = (IMAGE,)

    def features(
        self, bundles: Sequence[EvidenceBundle], thresholds: Mapping[str, Any], context: TaskContext
    ) -> Features:
        photometer: ForcedPhotometer = context.service("photometer")
        anchors: dict[str, CatalogueSource] = {}
        brightness: dict[str, float] = {}
        images: dict[str, str] = {}
        for b in bundles:
            row = catalogue_row(b, thresholds.get("source_id"), "ra_deg")
            if row is not None and finite(row.get("ra_deg"), row.get("dec_deg")):
                anchors[b.object_key] = anchor(b, row, float(thresholds.get("anchor_sigma_arcsec", 0.002)))
                g = row.get("g_mag")
                # a source with no G magnitude counts as faint, so it never pulls a blend's centre
                g = g if g is not None and finite(g) else UNKNOWN_G
                brightness[b.object_key] = 10 ** (-0.4 * float(g))
            for p in b.products:
                if p.product_type == IMAGE:
                    images.setdefault(p.artifact_id.value, p.product.product_id)
        empty = Features((), {"series": [], "frames": [], "left_out": {}})
        if not anchors or len(images) < 2:
            return empty
        left_out: dict[str, str] = {}
        frames = [
            f
            for f in (
                self._frame(artifact_id, product_id, anchors, thresholds, context, photometer, left_out)
                for artifact_id, product_id in sorted(images.items())
            )
            if f is not None
        ]
        by_band: dict[str, list[Frame]] = {}
        for f in frames:
            by_band.setdefault(f.band, []).append(f)
        series: list[Series] = []
        summaries: list[dict[str, Any]] = []
        for band, members in sorted(by_band.items()):
            members.sort(key=lambda f: (f.epoch_jyear, f.product_id))
            if len(members) < int(thresholds.get("min_epochs", 2)):
                for f in members:
                    left_out[f.product_id] = f"the only image in band {band}"
                continue
            found, summary = self._compare(band, members, anchors, brightness, thresholds, photometer, left_out)
            series.extend(found)
            summaries.append(summary)
        if not series:
            return Features((), {"series": [], "frames": summaries, "left_out": left_out})
        return Features(
            tuple(s.key for s in series), {"series": series, "frames": summaries, "left_out": dict(left_out)}
        )

    def _frame(
        self,
        artifact_id: str,
        product_id: str,
        anchors: Mapping[str, CatalogueSource],
        thresholds: Mapping[str, Any],
        context: TaskContext,
        photometer: ForcedPhotometer,
        left_out: dict[str, str],
    ) -> Frame | None:
        data = product_bytes(context, artifact_id)
        frame = ImageFrame(artifact_id, data, "application/fits")
        alignment: dict[str, Any] | None = None
        epoch_mjd: float | None = None
        if thresholds.get("align_wcs"):
            detector: SourceDetector = context.service("detector")
            params = {
                "fwhm": float(thresholds.get("fwhm_px", 3.0)),
                "nsigma": float(thresholds.get("nsigma", 5.0)),
                **{k: thresholds[k] for k in DETECTOR_MESH if k in thresholds},
            }
            found = detector.detect(frame, params)
            epoch_mjd = found.epoch_mjd
            if epoch_mjd is None:
                left_out[product_id] = "names no observation time"
                return None
            epoch = float(Time(epoch_mjd, format="mjd").jyear)
            aligned, alignment = align(found, anchors.values(), epoch, thresholds, context)
            if not alignment["matches"]:
                # an image whose WCS could not be checked against Gaia would put its apertures beside
                # its stars by as much as its archival error, which reads as a change
                left_out[product_id] = "could not be aligned to Gaia"
                return None
            frame = ImageFrame(artifact_id, data, "application/fits", wcs=aligned.wcs_header.encode())
        guess = {"fwhm_px": float(thresholds.get("fwhm_px", 3.0)), **_photometer_params(thresholds)}
        if epoch_mjd is None:
            described = photometer.describe(frame, (), guess)
            epoch_mjd = described.epoch_mjd
            if epoch_mjd is None:
                left_out[product_id] = "names no observation time"
                return None
        epoch = float(Time(epoch_mjd, format="mjd").jyear)
        description = photometer.describe(frame, _points(anchors, epoch), guess)
        if description.epoch_mjd is None:
            description = FrameDescription(
                description.band,
                epoch_mjd,
                description.instrument,
                description.pixel_arcsec,
                description.fwhm_arcsec,
                description.saturation,
            )
        fwhm = _fwhm(description, thresholds)
        if fwhm is None:
            left_out[product_id] = "no star bright enough to measure its FWHM, and the profile gives none"
            return None
        return Frame(
            product_id,
            artifact_id,
            frame,
            description,
            epoch,
            band_of(description, thresholds.get("bands") or {}),
            fwhm,
            alignment,
        )

    def _compare(
        self,
        band: str,
        members: Sequence[Frame],
        anchors: Mapping[str, CatalogueSource],
        brightness: Mapping[str, float],
        thresholds: Mapping[str, Any],
        photometer: ForcedPhotometer,
        left_out: dict[str, str],
    ) -> tuple[list[Series], dict[str, Any]]:
        fwhm_max = max(f.fwhm_arcsec for f in members)
        r, annulus = aperture(fwhm_max, float(thresholds.get("aperture_fwhm", 1.0)))
        placed = [
            {k: (m.ra_deg, m.dec_deg) for k, m in ((k, propagate(a, f.epoch_jyear)) for k, a in anchors.items()) if m}
            for f in members
        ]
        groups = blend_groups(placed, 2.0 * r)
        params = _photometer_params(thresholds)
        # measurements[image][group key] = (measurement, aperture correction)
        measured: list[dict[str, tuple[ForcedMeasurement, float]]] = []
        for f, where in zip(members, placed, strict=True):
            out: dict[str, tuple[ForcedMeasurement, float]] = {}
            points = [SkyPoint(g[0], *where[g[0]]) for g in groups if len(g) == 1 and g[0] in where]
            corrections: dict[str, float] = {}
            for g in groups:
                if len(g) == 1 or not all(k in where for k in g):
                    continue
                weights = np.array([brightness[k] for k in g])
                ra0, dec0 = where[g[0]]
                offsets = np.array([offset_arcsec(ra0, dec0, *where[k]) for k in g])
                centre = offsets.T @ weights / weights.sum()
                spread = np.hypot(*(offsets - centre).T)
                rg = r + float(np.max(spread))
                cosd = math.cos(math.radians(dec0))
                key = _lead(g, brightness)
                points.append(SkyPoint(key, ra0 + centre[0] / 3600 / cosd, dec0 + centre[1] / 3600, rg))
                # the blend's wider aperture, off its members' centres, holds a share of their light that
                # depends on the image's sharpness; scale it to the share r holds of a centred star, as
                # the zero point assumes
                held = float(np.sum(weights * [enclosed_offset(rg, d, f.fwhm_arcsec) for d in spread]) / weights.sum())
                corrections[key] = enclosed(r, f.fwhm_arcsec) / held
            for m in photometer.measure(f.frame, points, r, annulus, params).measurements:
                out[m.key] = (m, corrections.get(m.key, 1.0))
            measured.append(out)
        lead_of = {(_lead(g, brightness) if len(g) > 1 else g[0]): g for g in groups}
        isolated = {g[0] for g in groups if len(g) == 1}
        snr_min = float(thresholds.get("calibrator_snr", 20.0))

        def snr(m: ForcedMeasurement) -> float:
            return m.flux / m.flux_err if m.status == MEASURED and m.flux_err > 0 else 0.0

        depth = [float(np.median([snr(m) for k, (m, _) in o.items() if k in isolated] or [0.0])) for o in measured]
        ref = int(np.argmax(depth))
        zps: list[tuple[float, float, int] | None] = []
        for i, o in enumerate(measured):
            if i == ref:
                zps.append((0.0, 0.0, -1))
                continue
            pairs = [
                (o[k][0].flux, measured[ref][k][0].flux)
                for k in sorted(isolated)
                if k in o and k in measured[ref] and snr(o[k][0]) >= snr_min and snr(measured[ref][k][0]) >= snr_min
            ]
            if len(pairs) < int(thresholds.get("min_calibrators", 5)):
                zps.append(None)
                left_out[members[i].product_id] = (
                    f"{len(pairs)} calibrator stars above S/N {snr_min:g} shared with the reference image"
                )
                continue
            delta = np.array([-2.5 * math.log10(a / b) for a, b in pairs])
            zps.append(zero_point(delta))
        raw_bands = {f.description.band for f in members}
        band_frac = FRACTION_PER_MAG * float(thresholds.get("band_systematic_mag", 0.0)) if len(raw_bands) > 1 else 0.0
        if len(raw_bands) > 1 and "band_systematic_mag" not in thresholds:
            raise PlanInvalid(
                f"band {band} joins filters {sorted(raw_bands)}; the profile must give band_systematic_mag"
            )
        series: list[Series] = []
        for key, members_of in sorted(lead_of.items()):
            rows = []
            skipped: dict[str, str] = {}
            for i, (f, o) in enumerate(zip(members, measured, strict=True)):
                zp = zps[i]
                got = o.get(key)
                if zp is None:
                    continue
                if got is None:
                    skipped[f.product_id] = "not placed on this image (no proper motion for the gap)"
                    continue
                m, correction = got
                if m.status != MEASURED:
                    skipped[f.product_id] = m.status
                    continue
                if not m.flux_err > 0:
                    skipped[f.product_id] = "no noise estimate (blank background)"
                    continue
                scale = 10 ** (0.4 * zp[0]) * correction
                rows.append((f.product_id, f.description.epoch_mjd, m.flux * scale, m.flux_err * scale, zp[1]))
            if len(rows) < 2:
                continue
            series.append(
                Series(
                    key=key,
                    band=band,
                    images=tuple(r[0] for r in rows),
                    epoch_mjd=np.array([float(r[1] or 0.0) for r in rows]),
                    flux=np.array([r[2] for r in rows]),
                    err=np.array([r[3] for r in rows]),
                    zp_frac=np.array([FRACTION_PER_MAG * r[4] for r in rows]),
                    band_frac=band_frac,
                    blend_of=tuple(members_of) if len(members_of) > 1 else (),
                    left_out=skipped,
                )
            )
        summary = {
            "band": band,
            "filters": sorted(raw_bands),
            "aperture_arcsec": r,
            "annulus_arcsec": list(annulus),
            "reference": members[ref].product_id,
            "blends": [g for g in groups if len(g) > 1],
            "images": [
                {
                    "image": f.product_id,
                    "epoch_mjd": f.description.epoch_mjd,
                    "instrument": f.description.instrument,
                    "filter": f.description.band,
                    "pixel_arcsec": f.description.pixel_arcsec,
                    "fwhm_arcsec": f.fwhm_arcsec,
                    **({"alignment": dict(f.alignment)} if f.alignment is not None else {}),
                    **(
                        {"zero_point_mag": zp[0], "zero_point_err_mag": zp[1], "calibrators": zp[2]}
                        if zp is not None
                        else {"left_out": left_out.get(f.product_id)}
                    ),
                }
                for f, zp in zip(members, zps, strict=True)
            ],
        }
        return series, summary

    def evaluate(self, features: Features, thresholds: Mapping[str, Any], rng: np.random.Generator) -> list[Scored]:
        series: list[Series] = features.arrays["series"]
        if not series:
            return []
        totals = [_total_err(s) for s in series]
        scale = field_scale(series, totals, thresholds.get("error_scale", "field"))
        alpha = float(thresholds.get("alpha", 0.01))
        nsigma = float(thresholds.get("nsigma", 5.0))
        comparisons = {c["band"]: c for c in features.arrays.get("frames", [])}
        left_out: Mapping[str, str] = features.arrays.get("left_out", {})
        per_key: dict[str, list[tuple[float, float, dict[str, Any]]]] = {}
        for s, err in zip(series, totals, strict=True):
            sigma = err * scale
            w = 1.0 / sigma**2
            mean = float(np.sum(w * s.flux) / np.sum(w))
            chi2 = float(np.sum(((s.flux - mean) / sigma) ** 2))
            dof = len(s.flux) - 1
            p = float(stats.chi2.sf(chi2, dof))
            # whether an image saw the source, and its upper limit, rest on that image's own noise;
            # the zero-point and band terms, shares of the mean flux, belong to the comparison
            own = s.err * scale
            snr = s.flux / own
            hi, lo = int(np.argmax(s.flux)), int(np.argmin(s.flux))
            z = float((s.flux[hi] - s.flux[lo]) / math.hypot(sigma[hi], sigma[lo]))
            first, last = (lo, hi) if lo < hi else (hi, lo)
            delta = _delta_mag(s.flux[first], s.flux[last])
            details: dict[str, Any] = {
                "band": s.band,
                "chi2": chi2,
                "dof": dof,
                "verdict": verdict(p, alpha, snr, nsigma),
                "largest_change": {
                    "from": s.images[first],
                    "to": s.images[last],
                    "z": z,
                    "delta_mag": delta,
                },
                "baseline_years": float((s.epoch_mjd[-1] - s.epoch_mjd[0]) / 365.25),
                "epochs": [
                    {
                        "image": image,
                        "epoch_mjd": float(t),
                        "flux": float(f),
                        "flux_err": float(e),
                        "snr": float(q),
                        **({"upper_limit_5sigma": float(5.0 * o)} if q < ABSENT_SNR else {}),
                    }
                    for image, t, f, e, o, q in zip(s.images, s.epoch_mjd, s.flux, sigma, own, snr, strict=True)
                ],
                "comparison": comparisons.get(s.band, {}),
                **({"images_left_out": dict(left_out)} if left_out else {}),
                **({"error_scale": scale} if scale != 1.0 else {}),
                **({"blend_of": list(s.blend_of)} if s.blend_of else {}),
                **({"left_out": dict(s.left_out)} if s.left_out else {}),
            }
            per_key.setdefault(s.key, []).append((p, chi2, details))
        out = []
        for key, found in sorted(per_key.items()):
            p, chi2, worst = min(found, key=lambda r: (r[0], -r[1]))
            delta = worst["largest_change"]["delta_mag"]
            # the least likely band of several: Bonferroni over the bands, so a source compared in two
            # filters is no likelier to be flagged by chance than one compared in one
            p = min(1.0, p * len(found))
            if len(found) > 1:
                worst = {**worst, "verdict": STEADY if p >= alpha else worst["verdict"]}
            out.append(
                Scored(
                    Subject.of_object(key),
                    statistic=chi2,
                    p_value=p,
                    effect_size=None if delta is None else abs(delta),
                    details={
                        **worst,
                        "bands": len(found),
                        **({"per_band": [d for _, _, d in found]} if len(found) > 1 else {}),
                    },
                )
            )
        return out

    def null_features(self, features: Features, thresholds: Mapping[str, Any], rng: np.random.Generator) -> Features:
        """Every source constant at its weighted mean; each image's noise, and one zero-point and band
        offset per image shared by all its sources, drawn at the field's scale."""
        series: list[Series] = features.arrays["series"]
        if not series:
            return features
        totals = [_total_err(s) for s in series]
        scale = field_scale(series, totals, thresholds.get("error_scale", "field"))
        images = sorted({i for s in series for i in s.images})
        zp_draw = dict(zip(images, rng.standard_normal(len(images)), strict=True))
        band_draw = dict(zip(images, rng.standard_normal(len(images)), strict=True))
        drawn = []
        for s, err in zip(series, totals, strict=True):
            w = 1.0 / err**2
            mean = float(np.sum(w * s.flux) / np.sum(w))
            shared = np.array([s.zp_frac[j] * zp_draw[i] + s.band_frac * band_draw[i] for j, i in enumerate(s.images)])
            flux = mean * (1.0 + scale * shared) + rng.normal(0.0, s.err * scale)
            drawn.append(
                Series(
                    s.key, s.band, s.images, s.epoch_mjd, flux, s.err, s.zp_frac, s.band_frac, s.blend_of, s.left_out
                )
            )
        return Features(features.keys, {**features.arrays, "series": drawn})


def verdict(p: float, alpha: float, snr: np.ndarray, nsigma: float) -> str:
    """steady when the change is not significant; appeared when the source was not seen (S/N below 2)
    before it was seen (S/N of ``nsigma`` or more), vanished for the reverse, changed otherwise."""
    if p >= alpha:
        return STEADY
    seen = snr >= nsigma
    absent = snr < ABSENT_SNR
    idx = np.arange(len(snr))
    before = any(absent[i] and np.any(seen[idx > i]) for i in idx)
    after = any(seen[i] and np.any(absent[idx > i]) for i in idx)
    if before and not after:
        return APPEARED
    if after and not before:
        return VANISHED
    return CHANGED


def field_scale(series: Sequence[Series], totals: Sequence[np.ndarray], setting: Any) -> float:
    """The factor every error is multiplied by: a number as given, or ``"field"``, measured from the
    field's own sources. A source compared on few images has few degrees of freedom, and the median of
    χ² over n - 1 is well below 1 for small n (0.46 for one), so each source's χ² is divided by the
    median of its own χ² law: on a field whose errors are right the scale comes out 1, never below."""
    if setting != "field":
        return error_scale([], setting)
    reduced = []
    for s, err in zip(series, totals, strict=True):
        w = 1.0 / err**2
        mean = float(np.sum(w * s.flux) / np.sum(w))
        reduced.append(float(np.sum(((s.flux - mean) / err) ** 2)) / float(stats.chi2.median(len(s.flux) - 1)))
    if len(reduced) < MIN_FIELD_CURVES:
        return 1.0
    return max(1.0, math.sqrt(float(np.median(reduced))))


def _total_err(s: Series) -> np.ndarray:
    """Each image's error with its zero-point error and the band systematic, as fractions of the
    source's mean flux (weighted by the images' own errors)."""
    w = 1.0 / s.err**2
    mean = abs(float(np.sum(w * s.flux) / np.sum(w)))
    return np.sqrt(s.err**2 + (mean * s.zp_frac) ** 2 + (mean * s.band_frac) ** 2)


def _delta_mag(first: float, last: float) -> float | None:
    """m_last - m_first, or None when either flux is not positive (a source not seen has no magnitude)."""
    if first <= 0 or last <= 0:
        return None
    return float(-2.5 * math.log10(last / first))


def _lead(group: Sequence[str], brightness: Mapping[str, float]) -> str:
    """The blend's brightest member names it."""
    return min(group, key=lambda k: (-brightness.get(k, 1.0), k))


def _points(anchors: Mapping[str, CatalogueSource], epoch: float) -> list[SkyPoint]:
    points = []
    for key, a in sorted(anchors.items()):
        moved = propagate(a, epoch)
        if moved is not None:
            points.append(SkyPoint(key, moved.ra_deg, moved.dec_deg))
    return points


def _fwhm(description: FrameDescription, thresholds: Mapping[str, Any]) -> float | None:
    """The profile's FWHM for the instrument when it gives one (``fwhm_arcsec``: a number, or a map
    by instrument), else what the photometer measured."""
    given = thresholds.get("fwhm_arcsec")
    if isinstance(given, Mapping):
        given = given.get(description.instrument)
    if given is not None:
        return float(given)
    return description.fwhm_arcsec


def _photometer_params(thresholds: Mapping[str, Any]) -> dict[str, Any]:
    return {k: thresholds[k] for k in ("hdu", "saturation", "gain") if thresholds.get(k) is not None}

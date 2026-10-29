"""Image tests: Gaia sources against what a calibrated image detects (Integration v0.4.0 §7).

The JWST vertical slice adds no science algorithm. Detection is the ``detector`` service (photutils
in production), and association runs through both ``associators`` (ADR-019). The test only asks
whether each Gaia source, propagated to the image epoch, has a counterpart where it should be.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from typing import Any

import numpy as np
from astropy.time import Time
from astropy.wcs import WCS
from astropy.wcs.utils import proj_plane_pixel_scales
from scipy import stats

from sdp.kernel.domain.association import (
    AssociationGraph,
    AssociationPolicy,
    AssociationRecord,
    CatalogueSource,
    Detection,
    Motion,
    PositionCovariance,
)
from sdp.kernel.domain.errors import PlanInvalid
from sdp.kernel.domain.investigation import EvidenceBundle, Subject
from sdp.kernel.ports.integration import AssociationService
from sdp.kernel.ports.investigate import DetectionList, ImageFrame, SourceDetector, WcsRefiner
from sdp.kernel.science.association import propagate
from sdp.kernel.science.investigation import product_bytes
from sdp.kernel.science.plan import TaskContext
from sdp_investigate.base import Features, FieldTest, Scored, catalogue_row, finite

IMAGE = "image"
ANCHORS = "gaia"
FWHM_PER_SIGMA = 2.0 * math.sqrt(2.0 * math.log(2.0))
#: The statistic of a Gaia source with no counterpart: larger than any finite d² a test reports,
#: and finite, because results are canonical JSON.
MISSING_STATISTIC = 1e9
#: Detector settings a profile may set for the background mesh.
DETECTOR_MESH = ("box_size", "filter_size", "exclude_percentile")


def epoch_jyear(detections: DetectionList, artifact_id: str) -> float:
    if detections.epoch_mjd is None:
        raise PlanInvalid(f"image {artifact_id} names no observation time; it cannot be compared with Gaia")
    return float(Time(detections.epoch_mjd, format="mjd").jyear)


def detection_records(
    detections: DetectionList, product_id: str, epoch: float, instrument: str, fwhm_px: float, min_sigma: float
) -> tuple[list[Detection], float, tuple[int, int] | None, WCS]:
    """Association records for one image's detections, its pixel scale, its size and its WCS.

    Each centroid's sigma is the PSF sigma over the square root of its peak signal-to-noise; the
    instrument resolution is the PSF FWHM, so anchors closer than that form a blend option.
    """
    wcs = WCS(_header(detections.wcs_header))
    scale = float(np.mean(proj_plane_pixel_scales(wcs.celestial))) * 3600.0
    fwhm = fwhm_px * scale
    rms = detections.background_rms if detections.background_rms > 0 else 1.0
    records = []
    for i, d in enumerate(detections.detections):
        snr = max((d.peak if d.peak is not None else d.flux) / rms, 1.0)
        sigma = max(min_sigma, fwhm / FWHM_PER_SIGMA / math.sqrt(snr))
        records.append(
            Detection(
                f"{product_id}#{i:04d}",
                product_id,
                "SCI",
                d.ra_deg % 360.0,
                d.dec_deg,
                PositionCovariance(sigma, sigma),
                epoch,
                fwhm,
                instrument,
            )
        )
    shape = wcs.pixel_shape
    return records, scale, (int(shape[0]), int(shape[1])) if shape else None, wcs


def anchor(bundle: EvidenceBundle, row: Mapping[str, Any], sigma_arcsec: float) -> CatalogueSource:
    """The bundle's Gaia row as an association anchor, with its proper motion when it has one."""
    motion = None
    if finite(row.get("pm_ra_cosdec_masyr"), row.get("pm_dec_masyr")):
        motion = Motion(
            float(row["pm_ra_cosdec_masyr"]),
            float(row["pm_dec_masyr"]),
            float(row["parallax_mas"]) if finite(row.get("parallax_mas")) else None,
            float(row.get("pm_ra_cosdec_masyr_err") or 0.0),
            float(row.get("pm_dec_masyr_err") or 0.0),
        )
    return CatalogueSource(
        bundle.object_key,
        ANCHORS,
        str(row.get("release_label") or ""),
        str(row.get("source_key") or bundle.object_key),
        float(row["ra_deg"]) % 360.0,
        float(row["dec_deg"]),
        PositionCovariance(sigma_arcsec, sigma_arcsec),
        float(row.get("epoch_jyear") or 2016.0),
        motion,
    )


def align(
    found: DetectionList,
    anchors: Iterable[CatalogueSource],
    epoch: float,
    thresholds: Mapping[str, Any],
    context: TaskContext,
) -> tuple[DetectionList, dict[str, Any]]:
    """The image's WCS shifted onto the Gaia sources propagated to its epoch, by the ``refiner``.

    An archived image's absolute astrometry can be off by arcseconds (its guide stars, not Gaia,
    set it), which would leave every source without a counterpart. A single shift fitted to every
    match moves the frame, not a star, so a star displaced from the rest still stands out. The
    shift's fit is returned for the details; with too few matches the frame is left as it was.
    """
    refiner: WcsRefiner = context.service("refiner")
    moved = [m for m in (propagate(a, epoch) for a in anchors) if m is not None]
    scale = float(np.mean(proj_plane_pixel_scales(WCS(_header(found.wcs_header)).celestial))) * 3600.0
    fwhm_arcsec = float(thresholds.get("fwhm_px", 3.0)) * scale
    refined = refiner.refine(
        found,
        np.array([m.ra_deg for m in moved], dtype=float),
        np.array([m.dec_deg for m in moved], dtype=float),
        {
            "fitgeom": "shift",
            "searchrad": float(thresholds.get("align_search_arcsec", 5.0)),
            # Sources closer than two PSF widths are confusable, so the matcher sets them aside.
            "separation": 2.0 * fwhm_arcsec,
            # Tight, because a crowded field offers a random detection within an arcsecond of
            # every star: after the 2-D histogram's first guess, pairs must agree to 0.3 arcsec.
            "tolerance": float(thresholds.get("align_tolerance_arcsec", 0.3)),
        },
    )
    fit = {"refiner": refined.refiner_id, "matches": refined.matches, "rms_arcsec": refined.rms_arcsec}
    if not refined.matches:
        return found, fit
    return replace(found, detections=refined.detections, wcs_header=refined.wcs_header), fit


def counterpart_rows(graph: AssociationGraph, anchor_id: str) -> list[Mapping[str, Any]]:
    """Every retained association of a record to this anchor's hypothesis, best first.

    A blend ranks by its whole option's posterior, not the share the graph gives each member, and
    each row carries the posterior that its detection has no counterpart at all."""
    hypothesis = f"obj:{anchor_id}"
    rows = [a for a in graph.associations if a.hypothesis_id == hypothesis and a.record_id != anchor_id]
    alone = {a.record_id: a.posterior for a in graph.associations if a.hypothesis_id == f"obj:{a.record_id}"}
    return [
        {
            "record_id": a.record_id,
            "posterior": a.posterior,
            "option_posterior": float(a.evidence.get("option_posterior", a.posterior)),
            "no_counterpart": alone.get(a.record_id, 0.0),
            "role": a.evidence.get("role"),
            "blend_of": list(a.evidence.get("blend_of", [])),
            "competing": [h for h in a.competing if h != f"obj:{a.record_id}"],
        }
        for a in sorted(rows, key=lambda a: (-float(a.evidence.get("option_posterior", a.posterior)), a.record_id))
    ]


def best_counterpart(rows: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """The most probable row whose detection is not more probably unrelated to every anchor."""
    return next((r for r in rows if r["option_posterior"] >= r["no_counterpart"]), None)


def mahalanobis2(sources: Sequence[CatalogueSource], record: Detection) -> float:
    """Squared normalised offset of the detection from the sources propagated to its epoch: one
    source for a counterpart, the mean of a blend's members for a blended detection."""
    moved = [propagate(s, record.epoch_jyear) for s in sources]
    if not moved or any(m is None for m in moved):
        return math.inf
    cosd = math.cos(math.radians(record.dec_deg))
    offsets = [
        (((record.ra_deg - m.ra_deg + 180.0) % 360.0 - 180.0) * cosd * 3600.0, (record.dec_deg - m.dec_deg) * 3600.0)
        for m in moved
        if m is not None
    ]
    offset = np.mean(np.array(offsets), axis=0)
    a = np.mean([np.array(m.covariance.matrix()) for m in moved if m is not None], axis=0)
    b = np.array(record.covariance.matrix())
    return float(offset @ np.linalg.solve(a + b, offset))


class ImageCounterpart(FieldTest):
    """Is each Gaia source on the image where its proper motion puts it?

    Every image product is run through the ``detector`` service. Its detections and the field's Gaia
    sources go through each of the ``associators``. For a Gaia source inside the image footprint,
    the statistic is the squared normalised offset d² to its most probable counterpart under the
    first associator, with p = χ²₂ survival. A source with no counterpart scores p = 0, because a
    Gaia star missing from a deeper image is the anomaly. The details keep every retained hypothesis,
    the second method's posterior for the same pairing, and whether the counterpart is ambiguous.
    """

    name = "image-counterpart"
    question = "Is each Gaia source on the image where its proper motion puts it?"
    null_model = "counterparts at the propagated positions, scattered by their combined covariance"
    inputs = ("catalogue:gaia", "product:image")
    needs_products = (IMAGE,)

    def features(
        self, bundles: Sequence[EvidenceBundle], thresholds: Mapping[str, Any], context: TaskContext
    ) -> Features:
        detector: SourceDetector = context.service("detector")
        associators: Sequence[AssociationService] = context.service("associators")
        fwhm_px = float(thresholds.get("fwhm_px", 3.0))
        params = {
            "fwhm": fwhm_px,
            "nsigma": float(thresholds.get("nsigma", 5.0)),
            # The background mesh, passed on only when the profile sets it (a crowded field needs it).
            **{k: thresholds[k] for k in DETECTOR_MESH if k in thresholds},
        }
        anchors: dict[str, CatalogueSource] = {}
        images: dict[str, str] = {}
        for b in bundles:
            row = catalogue_row(b, thresholds.get("source_id"), "ra_deg")
            if row is not None and finite(row.get("ra_deg"), row.get("dec_deg")):
                anchors[b.object_key] = anchor(b, row, float(thresholds.get("anchor_sigma_arcsec", 0.002)))
            for p in b.products:
                if p.product_type == IMAGE:
                    images.setdefault(p.artifact_id.value, p.product.product_id)
        frames: list[tuple[str, list[Detection], WCS, tuple[int, int] | None, float]] = []
        alignments: dict[str, dict[str, Any]] = {}
        for artifact_id, product_id in sorted(images.items()):
            data = product_bytes(context, artifact_id)
            found = detector.detect(ImageFrame(artifact_id, data, "application/fits"), params)
            epoch = epoch_jyear(found, artifact_id)
            if thresholds.get("align_wcs"):
                found, alignments[product_id] = align(found, anchors.values(), epoch, thresholds, context)
            dets, _, shape, wcs = detection_records(
                found,
                product_id,
                epoch,
                found.instrument or "unknown",
                fwhm_px,
                float(thresholds.get("min_sigma_arcsec", 0.005)),
            )
            frames.append((product_id, dets, wcs, shape, epoch))
        if not anchors or not frames:
            return Features((), {"d2": [], "details": []})
        # Each image is associated on its own, so a counterpart on one image cannot stand in for a
        # star missing from another that covers it. A source's result is its worst covering image.
        per_source: dict[str, list[tuple[float | None, dict[str, Any]]]] = {}
        for product_id, dets, wcs, shape, epoch in frames:
            inside = {k: a for k, a in anchors.items() if _inside(a, wcs, shape, epoch)}
            if not inside:
                continue
            records: list[AssociationRecord] = [*inside.values(), *dets]
            policy = AssociationPolicy(
                "image-counterpart",
                "1.0.0",
                common_epoch_jyear=epoch,
                search_radius_arcsec=float(thresholds.get("search_radius_arcsec", 1.0)),
                anchor_catalogue=ANCHORS,
            )
            graphs = [a.associate(records, policy) for a in associators]
            by_id = {d.record_id: d for d in dets}
            for key, source in inside.items():
                rows = counterpart_rows(graphs[0], key)
                others = [{r["record_id"]: r["posterior"] for r in counterpart_rows(g, key)} for g in graphs[1:]]
                best = best_counterpart(rows)
                record = by_id.get(best["record_id"]) if best else None
                members = [anchors[m] for m in (best["blend_of"] if best else []) if m in anchors] or [source]
                d2 = mahalanobis2(members, record) if record is not None else None
                per_source.setdefault(key, []).append(
                    (
                        d2,
                        {
                            "image": product_id,
                            **({"alignment": alignments[product_id]} if product_id in alignments else {}),
                            "counterparts": rows,
                            "method": graphs[0].method,
                            "other_methods": {
                                g.method: o.get(best["record_id"]) if best else None
                                for g, o in zip(graphs[1:], others, strict=True)
                            },
                            "ambiguous": any(r["competing"] for r in rows),
                            "graph_ids": [g.graph_id for g in graphs],
                        },
                    )
                )
        keys, d2s, details = [], [], []
        for key, results in sorted(per_source.items()):
            d2, worst = max(results, key=lambda r: math.inf if r[0] is None or not math.isfinite(r[0]) else r[0])
            keys.append(key)
            d2s.append(d2)
            details.append({**worst, "images": len(results)})
        return Features(tuple(keys), {"d2": d2s, "details": details})

    def evaluate(self, features: Features, thresholds: Mapping[str, Any], rng: np.random.Generator) -> list[Scored]:
        out = []
        for key, d2, details in zip(features.keys, features.arrays["d2"], features.arrays["details"], strict=True):
            missing = d2 is None or not math.isfinite(d2)
            out.append(
                Scored(
                    Subject.of_object(key),
                    statistic=MISSING_STATISTIC if missing else d2,
                    p_value=0.0 if missing else float(stats.chi2.sf(d2, 2)),
                    effect_size=None if missing else math.sqrt(d2),
                    details={**details, "counterpart_found": not missing},
                )
            )
        return out

    def null_features(self, features: Features, thresholds: Mapping[str, Any], rng: np.random.Generator) -> Features:
        d2 = [float(rng.chisquare(2)) for _ in features.keys]
        return Features(features.keys, {"d2": d2, "details": features.arrays["details"]})


def _inside(source: CatalogueSource, wcs: WCS, shape: tuple[int, int] | None, epoch: float) -> bool:
    moved = propagate(source, epoch)
    if moved is None:
        return False
    if shape is None:
        return True
    x, y = wcs.celestial.world_to_pixel_values(moved.ra_deg, moved.dec_deg)
    return bool(0 <= float(x) < shape[0] and 0 <= float(y) < shape[1])


def _header(text: str) -> str:
    """Newline-separated FITS cards as the 80-character card string astropy.wcs parses."""
    return "".join(line.ljust(80)[:80] for line in text.splitlines())

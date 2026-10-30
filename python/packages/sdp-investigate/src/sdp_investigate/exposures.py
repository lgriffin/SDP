"""Exposure tests: Gaia sources against where a time-domain archive saw them at every visit (INT-024).

A WISE or NEOWISE light curve says, for each exposure, where the source it measured was. Each
exposure becomes an association record at its own epoch with the instrument's beam as its
resolution, so the association service (both methods, ADR-019) propagates the field's Gaia stars to
that epoch and offers stars closer than the beam as one blend. The test only asks whether the
exposures, on average, sit where Gaia's star (or the blend it belongs to) should be.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
from scipy import stats

from sdp.kernel.domain.association import (
    AssociationGraph,
    AssociationPolicy,
    AssociationRecord,
    CatalogueSource,
    Detection,
    PositionCovariance,
)
from sdp.kernel.domain.investigation import EvidenceBundle, Subject
from sdp.kernel.ports.integration import AssociationService
from sdp.kernel.science.association import candidate_options, offset_arcsec, propagate, separation_arcsec
from sdp.kernel.science.investigation import product_bytes
from sdp.kernel.science.plan import TaskContext
from sdp_investigate.base import Features, FieldTest, Scored, catalogue_row, finite
from sdp_investigate.formats import LIGHTCURVE, parse_exposures
from sdp_investigate.imaging import ANCHORS, MISSING_STATISTIC, anchor

J2000_MJD = 51544.5
#: The fewest matched exposures the mean offset's scatter can be estimated from (F has n - 2 dof).
MIN_MATCHED = 3


def chosen_option(graph: AssociationGraph, record_id: str) -> tuple[str, ...]:
    """The anchors of the record's most probable option: one star, a blend's members, or none."""
    ranked: list[tuple[float, tuple[str, ...]]] = []
    for a in graph.for_record(record_id):
        role = a.evidence.get("role")
        if role == "blend":
            ranked.append((float(a.evidence["option_posterior"]), tuple(sorted(a.evidence["blend_of"]))))
        elif role == "counterpart":
            ranked.append((a.posterior, (a.hypothesis_id.removeprefix("obj:"),)))
        else:
            ranked.append((a.posterior, ()))
    return min(ranked, key=lambda r: (-r[0], r[1]))[1] if ranked else ()


def exposure_records(bundle: EvidenceBundle, context: TaskContext) -> list[Detection]:
    """Every exposure of the object's light curves that says where it saw the source."""
    out = []
    for p in bundle.products:
        if p.product_type != LIGHTCURVE:
            continue
        exposures = parse_exposures(product_bytes(context, p.artifact_id.value))
        if exposures is None:
            continue
        pid = p.product.product_id
        for i in range(len(exposures.mjd)):
            out.append(
                Detection(
                    f"{pid}#{i:04d}",
                    pid,
                    LIGHTCURVE,
                    float(exposures.ra_deg[i]) % 360.0,
                    float(exposures.dec_deg[i]),
                    PositionCovariance(float(exposures.ra_err_arcsec[i]), float(exposures.dec_err_arcsec[i])),
                    2000.0 + (float(exposures.mjd[i]) - J2000_MJD) / 365.25,
                    float(exposures.beam_arcsec[i]),
                    p.product.archive,
                )
            )
    return out


def nearby(
    own: CatalogueSource, anchors: Mapping[str, CatalogueSource], epoch: float, reach: float
) -> list[CatalogueSource]:
    """The anchors within ``reach`` arcsec of the object's own star at ``epoch``: the only ones an
    exposure of it could be matched or blended with."""
    centre = propagate(own, epoch)
    if centre is None:
        return []
    out = []
    for a in anchors.values():
        moved = propagate(a, epoch)
        if moved is not None and separation_arcsec(centre.ra_deg, centre.dec_deg, moved.ra_deg, moved.dec_deg) <= reach:
            out.append(a)
    return out


def mean_offset_f(
    offsets: np.ndarray, option_covariances: np.ndarray, systematic_arcsec: float = 0.0
) -> tuple[float, np.ndarray, np.ndarray]:
    """Hotelling's statistic for a mean offset of zero, as F(2, n - 2), with the mean and the scatter.

    The mean's covariance is the exposures' own scatter over n, so an archive whose quoted position
    errors are too small cannot fake an offset, plus the expected position's covariance (a blend's
    is wide, since its light-weighted centroid could fall anywhere between its members), which is
    the same for every exposure and so is not divided by n, plus the archive's systematic error
    against Gaia on each axis, which no number of exposures averages away."""
    n = len(offsets)
    mean = offsets.mean(axis=0)
    scatter = np.cov(offsets.T, ddof=1)
    floor = systematic_arcsec**2 * np.eye(2)
    t2 = float(mean @ np.linalg.solve(scatter / n + option_covariances.mean(axis=0) + floor, mean))
    return t2 * (n - 2) / (2 * (n - 1)), mean, scatter


class ExposureCounterpart(FieldTest):
    """Does the source each exposure saw sit where Gaia puts the star?

    Every exposure of the object's light curves that carries a position goes, with the Gaia stars near
    the object, through each of the ``associators``. An exposure whose most probable option under the
    first associator is the object's star, alone or in a blend, is matched; its offset is taken from
    that option's expected position at the exposure's epoch. The statistic is Hotelling's T² for a
    zero mean offset as F(2, n − 2), the p-value its survival. When fewer than ``min_matched_fraction``
    of the exposures are matched (or fewer than three), the star has no counterpart and p = 0. The
    details keep how each exposure was explained, the blend, and how often the second method agrees.
    """

    name = "exposure-counterpart"
    question = "Does the source each exposure saw sit where Gaia puts the star?"
    null_model = "exposures centred on the expected position, scattered as the real ones are"
    inputs = ("catalogue:gaia", "product:lightcurve")
    needs_products = (LIGHTCURVE,)

    def features(
        self, bundles: Sequence[EvidenceBundle], thresholds: Mapping[str, Any], context: TaskContext
    ) -> Features:
        associators: Sequence[AssociationService] = context.service("associators")
        min_exposures = int(thresholds.get("min_exposures", 5))
        min_fraction = float(thresholds.get("min_matched_fraction", 0.5))
        anchors: dict[str, CatalogueSource] = {}
        for b in bundles:
            row = catalogue_row(b, thresholds.get("source_id"), "ra_deg")
            if row is not None and finite(row.get("ra_deg"), row.get("dec_deg")):
                anchors[b.object_key] = anchor(b, row, float(thresholds.get("anchor_sigma_arcsec", 0.002)))
        keys: list[str] = []
        fs: list[float | None] = []
        ns: list[int] = []
        details: list[dict[str, Any]] = []
        for b in bundles:
            own = anchors.get(b.object_key)
            dets = exposure_records(b, context) if own is not None else []
            if own is None or len(dets) < min_exposures:
                continue
            epoch = float(np.median([d.epoch_jyear for d in dets]))
            radius = float(thresholds.get("search_radius_arcsec", max(d.resolution_arcsec for d in dets)))
            near = nearby(own, anchors, epoch, 2.0 * radius + max(d.resolution_arcsec for d in dets))
            if own not in near:
                continue  # no motion and too far from Gaia's epoch to be compared
            records: list[AssociationRecord] = [*near, *dets]
            policy = AssociationPolicy(self.name, self.version, epoch, radius, anchor_catalogue=ANCHORS)
            graphs = [a.associate(records, policy) for a in associators]
            options = {ro.record.record_id: ro.options for ro in candidate_options(records, policy)}
            roles: Counter[str] = Counter()
            blends: Counter[tuple[str, ...]] = Counter()
            agree = [0 for _ in graphs[1:]]
            offsets, covariances = [], []
            for d in dets:
                chosen = chosen_option(graphs[0], d.record_id)
                for i, g in enumerate(graphs[1:]):
                    agree[i] += chosen_option(g, d.record_id) == chosen
                if b.object_key not in chosen:
                    roles["other-star" if chosen else "no-counterpart"] += 1
                    continue
                roles["blend" if len(chosen) > 1 else "alone"] += 1
                if len(chosen) > 1:
                    blends[chosen] += 1
                option = next(o for o in options[d.record_id] if tuple(sorted(o.members)) == chosen)
                offsets.append(offset_arcsec(option.position.ra_deg, option.position.dec_deg, d.ra_deg, d.dec_deg))
                covariances.append(option.position.covariance.matrix())
            n = len(offsets)
            detail: dict[str, Any] = {
                "exposures": len(dets),
                "matched": n,
                "roles": dict(sorted(roles.items())),
                "blend_of": list(blends.most_common(1)[0][0]) if blends else [],
                "products": sorted({d.product_id for d in dets}),
                "search_radius_arcsec": radius,
                "method": graphs[0].method,
                "other_methods": {g.method: a / len(dets) for g, a in zip(graphs[1:], agree, strict=True)},
                "graph_ids": [g.graph_id for g in graphs],
            }
            f = None
            if n >= max(MIN_MATCHED, min_fraction * len(dets)):
                f, mean, scatter = mean_offset_f(
                    np.array(offsets), np.array(covariances), float(thresholds.get("systematic_arcsec", 0.0))
                )
                detail["mean_offset_arcsec"] = [float(mean[0]), float(mean[1])]
                detail["scatter_arcsec"] = [float(math.sqrt(scatter[0, 0])), float(math.sqrt(scatter[1, 1]))]
            keys.append(b.object_key)
            fs.append(f)
            ns.append(max(n, MIN_MATCHED))
            details.append(detail)
        return Features(tuple(keys), {"f": fs, "n": ns, "details": details})

    def evaluate(self, features: Features, thresholds: Mapping[str, Any], rng: np.random.Generator) -> list[Scored]:
        out = []
        arrays = features.arrays
        for key, f, n, details in zip(features.keys, arrays["f"], arrays["n"], arrays["details"], strict=True):
            missing = f is None
            offset = details.get("mean_offset_arcsec")
            out.append(
                Scored(
                    Subject.of_object(key),
                    statistic=MISSING_STATISTIC if missing else f,
                    p_value=0.0 if missing else float(stats.f.sf(f, 2, n - 2)),
                    effect_size=None if offset is None else math.hypot(*offset),
                    details={**details, "counterpart_found": not missing},
                )
            )
        return out

    def null_features(self, features: Features, thresholds: Mapping[str, Any], rng: np.random.Generator) -> Features:
        f = [float(rng.f(2, n - 2)) for n in features.arrays["n"]]
        return Features(features.keys, {**features.arrays, "f": f})

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


def usable(ra: float, dec: float, ra_err: float, dec_err: float, beam: float) -> bool:
    """Whether an exposure's position can be an association record: on the sky, with positive
    finite sigmas and beam (NaN fails every comparison). Anything else is counted as an exposure without a
    usable position."""
    positive = all(math.isfinite(v) and v > 0 for v in (ra_err, dec_err, beam))
    return positive and 0.0 <= ra < 360.0 and -90.0 <= dec <= 90.0


def exposure_records(bundle: EvidenceBundle, context: TaskContext) -> tuple[list[Detection], int]:
    """Every exposure of the object's light curves that says where it saw the source, and how many
    epochs of those light curves have no usable position. A product listed twice is read once."""
    out: list[Detection] = []
    unusable = 0
    seen: set[tuple[str, str]] = set()
    for p in bundle.products:
        ref = (p.product.archive, p.product.product_id)
        if p.product_type != LIGHTCURVE or ref in seen:
            continue
        exposures = parse_exposures(product_bytes(context, p.artifact_id.value))
        if exposures is None:
            continue
        seen.add(ref)
        for i in range(len(exposures.mjd)):
            ra, dec = float(exposures.ra_deg[i]), float(exposures.dec_deg[i])
            sigmas = (float(exposures.ra_err_arcsec[i]), float(exposures.dec_err_arcsec[i]))
            beam = float(exposures.beam_arcsec[i])
            if not usable(ra, dec, *sigmas, beam):
                unusable += 1
                continue
            out.append(
                Detection(
                    f"{ref[0]}/{ref[1]}#{i:04d}",
                    ref[1],
                    LIGHTCURVE,
                    ra,
                    dec,
                    PositionCovariance(*sigmas),
                    2000.0 + (float(exposures.mjd[i]) - J2000_MJD) / 365.25,
                    beam,
                    ref[0],
                )
            )
    return out, unusable


def nearby(
    own: CatalogueSource, anchors: Mapping[str, CatalogueSource], epochs: Sequence[float], reach: float
) -> list[CatalogueSource]:
    """The anchors within ``reach`` arcsec of the object's own star at any of ``epochs`` (the first,
    middle and last exposure): the only ones an exposure of it could be matched or blended with. A
    neighbour moves along a straight line relative to the star, so one that crosses the beam between
    two of those epochs is within reach of one of them unless it moves several arcsec a year relative
    to the star (``reach`` is twice the radius plus the beam). With no motion and Gaia's epoch too far
    away, the star itself is out of reach."""
    out = []
    for a in anchors.values():
        for epoch in epochs:
            centre, moved = propagate(own, epoch), propagate(a, epoch)
            if centre is None or moved is None:
                continue
            if separation_arcsec(centre.ra_deg, centre.dec_deg, moved.ra_deg, moved.dec_deg) <= reach:
                out.append(a)
                break
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
    zero mean offset as F(2, n − 2), the p-value its survival: exact with no expected-position or
    systematic covariance and conservative with them, which the null fields draw (``null_features``),
    so the false-positive rate is the statistic's own. When fewer than ``min_matched_fraction`` of
    the exposures (those without a usable position included) are matched, or fewer than three, the
    star has no counterpart and p = 0. The
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
        samples: list[tuple[np.ndarray, np.ndarray] | None] = []
        details: list[dict[str, Any]] = []
        systematic = float(thresholds.get("systematic_arcsec", 0.0))
        for b in bundles:
            own = anchors.get(b.object_key)
            dets, unusable = exposure_records(b, context) if own is not None else ([], 0)
            if own is None or len(dets) < min_exposures:
                continue
            epochs = sorted(d.epoch_jyear for d in dets)
            epoch = float(np.median(epochs))
            radius = float(thresholds.get("search_radius_arcsec", max(d.resolution_arcsec for d in dets)))
            reach = 2.0 * radius + max(d.resolution_arcsec for d in dets)
            near = nearby(own, anchors, (epochs[0], epoch, epochs[-1]), reach)
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
                "without_position": unusable,
                "matched": n,
                "roles": dict(sorted(roles.items())),
                "blend_of": list(blends.most_common(1)[0][0]) if blends else [],
                "products": sorted({f"{d.instrument}/{d.product_id}" for d in dets}),
                "search_radius_arcsec": radius,
                "method": graphs[0].method,
                "other_methods": {g.method: a / len(dets) for g, a in zip(graphs[1:], agree, strict=True)},
                "graph_ids": [g.graph_id for g in graphs],
            }
            f, sample = None, None
            # an epoch without a usable position is an exposure that did not see the star here
            if n >= max(MIN_MATCHED, min_fraction * (len(dets) + unusable)):
                sample = (np.array(offsets), np.array(covariances))
                f, mean, scatter = mean_offset_f(*sample, systematic)
                detail["mean_offset_arcsec"] = [float(mean[0]), float(mean[1])]
                detail["scatter_arcsec"] = [float(math.sqrt(scatter[0, 0])), float(math.sqrt(scatter[1, 1]))]
            keys.append(b.object_key)
            fs.append(f)
            ns.append(max(n, MIN_MATCHED))
            samples.append(sample)
            details.append(detail)
        return Features(tuple(keys), {"f": fs, "n": ns, "samples": samples, "details": details})

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
        """The same statistic on exposures drawn about the expected position: each star's matched
        exposures scattered as its real ones are, all shifted together by one draw of the expected
        position's covariance plus the systematic floor. A star with too few matched exposures draws
        from F(2, 1), the law of the fewest it could be judged on."""
        systematic = float(thresholds.get("systematic_arcsec", 0.0))
        f = []
        for n, sample in zip(features.arrays["n"], features.arrays["samples"], strict=True):
            if sample is None:
                f.append(float(rng.f(2, n - 2)))
                continue
            offsets, covariances = sample
            shared = covariances.mean(axis=0) + systematic**2 * np.eye(2)
            drawn = rng.multivariate_normal(np.zeros(2), np.cov(offsets.T, ddof=1), size=len(offsets))
            drawn += rng.multivariate_normal(np.zeros(2), shared)
            f.append(mean_offset_f(drawn, covariances, systematic)[0])
        return Features(features.keys, {**features.arrays, "f": f})

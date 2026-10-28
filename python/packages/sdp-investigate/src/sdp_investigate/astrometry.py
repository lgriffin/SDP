"""Astrometric tests: parallax consistency, long-baseline motion and astrometric coherence."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
from scipy import stats

from sdp.kernel.domain.investigation import EvidenceBundle, Subject
from sdp.kernel.ports.compute import ClusterSearchAlgorithm
from sdp.kernel.science.investigation import product_bytes
from sdp.kernel.science.plan import TaskContext
from sdp_investigate.base import Features, FieldTest, Scored, catalogue_row, finite, robust_sigma
from sdp_investigate.formats import ASTROMETRY, parse_positions

MAS_PER_DEG = 3.6e6


class ParallaxConsistency(FieldTest):
    """Does the object's parallax stand out from the field's, given its own error?

    z = (parallax - field median) / sqrt(error² + field robust spread²); two-sided normal p-value.
    """

    name = "parallax-consistency"
    question = "Do members share a distance within their errors?"
    null_model = "field parallax distribution, resampled with each object's own error"
    inputs = ("catalogue:gaia",)

    def features(
        self, bundles: Sequence[EvidenceBundle], thresholds: Mapping[str, Any], context: TaskContext
    ) -> Features:
        keys, plx, err = [], [], []
        for b in bundles:
            row = catalogue_row(b, thresholds.get("source_id"), "parallax_mas")
            if row and finite(row.get("parallax_mas"), row.get("parallax_mas_err")):
                keys.append(b.object_key)
                plx.append(float(row["parallax_mas"]))
                err.append(float(row["parallax_mas_err"]))
        return Features(tuple(keys), {"plx": np.array(plx), "err": np.array(err)})

    def evaluate(self, features: Features, thresholds: Mapping[str, Any], rng: np.random.Generator) -> list[Scored]:
        plx, err = features.arrays["plx"], features.arrays["err"]
        if len(plx) < 3:
            return []
        centre, spread = float(np.median(plx)), robust_sigma(plx)
        out = []
        for key, p, e in zip(features.keys, plx, err, strict=True):
            z = (p - centre) / math.sqrt(e**2 + spread**2)
            out.append(
                Scored(
                    Subject.of_object(key),
                    statistic=abs(z),
                    p_value=float(2 * stats.norm.sf(abs(z))),
                    effect_size=float(p - centre),
                    uncertainty=float(e),
                    details={"z": z, "field_median_mas": centre, "field_spread_mas": spread},
                )
            )
        return out

    def null_features(self, features: Features, thresholds: Mapping[str, Any], rng: np.random.Generator) -> Features:
        plx, err = features.arrays["plx"], features.arrays["err"]
        drawn = rng.choice(plx, size=len(plx), replace=True) + rng.normal(0.0, err)
        return Features(features.keys, {"plx": drawn, "err": err})


def predict(row: Mapping[str, Any], epoch: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Linear propagation of a Gaia position to other epochs, in the tangent plane."""
    dt = epoch - float(row["epoch_jyear"])
    dec = float(row["dec_deg"]) + float(row["pm_dec_masyr"]) * dt / MAS_PER_DEG
    ra = float(row["ra_deg"]) + float(row["pm_ra_cosdec_masyr"]) * dt / MAS_PER_DEG / math.cos(
        math.radians(float(row["dec_deg"]))
    )
    return ra % 360.0, dec


def ra_offset_deg(ra: np.ndarray, reference: np.ndarray) -> np.ndarray:
    """The signed right-ascension difference the shortest way round: 359.9999 and 0.0001 are close."""
    return np.asarray((np.asarray(ra) - reference + 180.0) % 360.0 - 180.0)


class LongBaselineMotion(FieldTest):
    """Does the position history (DASCH plates, DSS) agree with the Gaia proper motion?

    χ² of the measured positions about the Gaia prediction, with the plate errors and the
    proper-motion errors propagated over each epoch's baseline; dof = 2 × epochs.
    """

    name = "long-baseline-motion"
    question = "Does the 1880-2020 position history agree with the Gaia proper motion?"
    null_model = "Gaia prediction with propagated errors"
    inputs = ("catalogue:gaia", "product:astrometry")
    needs_products = (ASTROMETRY,)

    def features(
        self, bundles: Sequence[EvidenceBundle], thresholds: Mapping[str, Any], context: TaskContext
    ) -> Features:
        keys, series = [], []
        for b in bundles:
            row = catalogue_row(b, thresholds.get("source_id"), "pm_ra_cosdec_masyr")
            products = [p for p in b.products if p.product_type == ASTROMETRY]
            if not row or not products or not finite(row.get("pm_ra_cosdec_masyr"), row.get("pm_dec_masyr")):
                continue
            history = parse_positions(product_bytes(context, products[0].artifact_id.value))
            if len(history.epoch_jyear) == 0:
                continue
            pra, pdec = predict(row, history.epoch_jyear)
            dt = np.abs(history.epoch_jyear - float(row["epoch_jyear"]))
            pm_err = math.hypot(
                float(row.get("pm_ra_cosdec_masyr_err") or 0.0), float(row.get("pm_dec_masyr_err") or 0.0)
            )
            sigma = np.sqrt(history.pos_err_mas**2 + (pm_err * dt / math.sqrt(2)) ** 2)
            cosd = math.cos(math.radians(float(row["dec_deg"])))
            dra = ra_offset_deg(history.ra_deg, pra) * cosd * MAS_PER_DEG
            ddec = (history.dec_deg - pdec) * MAS_PER_DEG
            keys.append(b.object_key)
            series.append((dra, ddec, sigma, history.epoch_jyear))
        return Features(tuple(keys), {"series": series})

    def evaluate(self, features: Features, thresholds: Mapping[str, Any], rng: np.random.Generator) -> list[Scored]:
        out = []
        for key, (dra, ddec, sigma, epochs) in zip(features.keys, features.arrays["series"], strict=True):
            chi2 = float(np.sum((dra / sigma) ** 2 + (ddec / sigma) ** 2))
            dof = 2 * len(dra)
            rms = float(np.sqrt(np.mean(dra**2 + ddec**2)))
            out.append(
                Scored(
                    Subject.of_object(key),
                    statistic=chi2,
                    p_value=float(stats.chi2.sf(chi2, dof)),
                    effect_size=rms,
                    details={
                        "dof": dof,
                        "epochs": len(dra),
                        "baseline_years": float(np.max(epochs) - np.min(epochs)) if len(epochs) else 0.0,
                        "rms_residual_mas": rms,
                    },
                )
            )
        return out

    def null_features(self, features: Features, thresholds: Mapping[str, Any], rng: np.random.Generator) -> Features:
        series = [
            (rng.normal(0.0, sigma), rng.normal(0.0, sigma), sigma, epochs)
            for _, _, sigma, epochs in features.arrays["series"]
        ]
        return Features(features.keys, {"series": series})


class AstrometricCoherence(FieldTest):
    """Is there a co-moving group here? Clusters in (pm_ra, pm_dec, parallax), robustly scaled,
    found by the ClusterSearchAlgorithm the kernel provides as the ``clusterer`` service.

    A cluster's statistic is its contrast: the field's median distance from the cluster centre over
    the members' median distance from it, in the scaled space, so a tight group far from the field
    outranks a loose overdensity at the field's own centre. Its p-value is the
    fraction of null fields (shuffled within magnitude bins) whose most coherent cluster is at least
    as coherent: (1 + count) / (1 + inner trials).
    """

    name = "astrometric-coherence"
    question = "Is there a co-moving group here?"
    null_model = "proper motions and parallaxes shuffled within magnitude bins"
    inputs = ("catalogue:gaia",)

    def features(
        self, bundles: Sequence[EvidenceBundle], thresholds: Mapping[str, Any], context: TaskContext
    ) -> Features:
        keys, rows = [], []
        for b in bundles:
            row = catalogue_row(b, thresholds.get("source_id"), "pm_ra_cosdec_masyr")
            if row and finite(row.get("pm_ra_cosdec_masyr"), row.get("pm_dec_masyr"), row.get("parallax_mas")):
                keys.append(b.object_key)
                rows.append(
                    (
                        float(row["pm_ra_cosdec_masyr"]),
                        float(row["pm_dec_masyr"]),
                        float(row["parallax_mas"]),
                        float(row.get("g_mag") or 0.0),
                    )
                )
        data = np.array(rows, dtype=float).reshape(-1, 4)
        clusterer: ClusterSearchAlgorithm = context.service("clusterer")
        return Features(tuple(keys), {"x": data[:, :3], "g": data[:, 3], "clusterer": clusterer})

    @staticmethod
    def _scaled(x: np.ndarray) -> np.ndarray:
        if len(x) == 0:
            return x
        centre = np.median(x, axis=0)
        spread = np.array([robust_sigma(x[:, i]) for i in range(x.shape[1])])
        return np.asarray((x - centre) / spread)

    def _labels(self, features: Features, thresholds: Mapping[str, Any], seed: int) -> tuple[np.ndarray, np.ndarray]:
        clusterer: ClusterSearchAlgorithm = features.arrays["clusterer"]
        params = dict(thresholds.get("clusterer_params", {"eps": 0.5, "min_cluster_size": 4}))
        found = clusterer.search(self._scaled(features.arrays["x"]), params, seed)
        return np.asarray(found.labels), np.asarray(found.probabilities)

    @staticmethod
    def contrast(field: np.ndarray, members: np.ndarray) -> float:
        """Field median distance from the members' centre over the members' own (scaled units)."""
        if len(members) < 2:
            return 0.0
        centre = np.median(members, axis=0)
        inner = float(np.median(np.linalg.norm(members - centre, axis=1)))
        outer = float(np.median(np.linalg.norm(field - centre, axis=1)))
        return outer / (inner + 1e-3)

    def _best(self, features: Features, thresholds: Mapping[str, Any], seed: int) -> float:
        labels, _ = self._labels(features, thresholds, seed)
        scaled = self._scaled(features.arrays["x"])
        return max((self.contrast(scaled, scaled[labels == k]) for k in set(labels.tolist()) - {-1}), default=0.0)

    def evaluate(self, features: Features, thresholds: Mapping[str, Any], rng: np.random.Generator) -> list[Scored]:
        if len(features) < 4:
            return []
        labels, probs = self._labels(features, thresholds, int(rng.integers(2**31 - 1)))
        clusters = sorted(set(labels.tolist()) - {-1})
        if not clusters:
            return []
        inner = int(thresholds.get("inner_trials", 20))
        null_best = [
            self._best(self.null_features(features, thresholds, rng), thresholds, int(rng.integers(2**31 - 1)))
            for _ in range(inner)
        ]
        scaled = self._scaled(features.arrays["x"])
        out = []
        for k in clusters:
            idx = np.nonzero(labels == k)[0]
            size = len(idx)
            members = tuple(features.keys[i] for i in idx)
            x = features.arrays["x"][idx]
            score = self.contrast(scaled, scaled[idx])
            p = (1 + sum(1 for s in null_best if s >= score)) / (1 + inner)
            out.append(
                Scored(
                    Subject(
                        structure_id=f"{self.name}-{members[0]}",
                        members=members,
                        membership=tuple(float(min(1.0, max(0.0, probs[i]))) for i in idx),
                    ),
                    statistic=score,
                    p_value=float(p),
                    effect_size=float(score / max(1e-9, float(np.median(null_best)) if null_best else 1.0)),
                    details={
                        "members": size,
                        "mean_pm_masyr": [float(x[:, 0].mean()), float(x[:, 1].mean())],
                        "mean_parallax_mas": float(x[:, 2].mean()),
                        "null_best_contrast": null_best,
                        "algorithm": features.arrays["clusterer"].algorithm_id,
                    },
                )
            )
        return out

    def null_features(self, features: Features, thresholds: Mapping[str, Any], rng: np.random.Generator) -> Features:
        x = features.arrays["x"].copy()
        g = features.arrays["g"]
        bins = int(thresholds.get("magnitude_bins", 4))
        if len(g):
            edges = np.quantile(g, np.linspace(0, 1, bins + 1))
            which = np.clip(np.searchsorted(edges, g, side="right") - 1, 0, bins - 1)
            for b in range(bins):
                idx = np.nonzero(which == b)[0]
                for col in range(x.shape[1]):
                    x[idx, col] = x[rng.permutation(idx), col]
        return Features(features.keys, {**features.arrays, "x": x})

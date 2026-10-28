"""Likelihood-ratio counterpart identification (Sutherland and Saunders 1992), the second method.

On the tangent plane in arcseconds: ``LR_k = f(r_k) / n`` where f is the bivariate normal density of
the offset under the combined covariance and n the anchor background density per square arcsecond.
The reliability of candidate k, with Q the prior that a counterpart exists, is
``Q LR_k / (Σ_j Q LR_j + 1 − Q)``.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from sdp.adapters.association.graph import Scored, build_graph
from sdp.kernel.domain.association import AssociationGraph, AssociationPolicy, AssociationRecord, PositionCovariance
from sdp.kernel.science.association import RecordOptions, offset_arcsec


def _matrix(c: PositionCovariance) -> np.ndarray:
    off = c.rho * c.sigma_ra_arcsec * c.sigma_dec_arcsec
    return np.array([[c.sigma_ra_arcsec**2, off], [off, c.sigma_dec_arcsec**2]])


def positional_density(offset: np.ndarray, cov: np.ndarray) -> float:
    """Bivariate normal density (per square arcsecond) of an offset in arcsec."""
    inv = np.linalg.inv(cov)
    chi2 = float(offset @ inv @ offset)
    return float(np.exp(-0.5 * chi2) / (2 * np.pi * np.sqrt(np.linalg.det(cov))))


class LikelihoodRatioAssociation:
    method = "likelihood-ratio"
    method_version = "1.0.0"

    def associate(self, records: Sequence[AssociationRecord], policy: AssociationPolicy) -> AssociationGraph:
        return build_graph(records, policy, method=self.method, method_version=self.method_version, score=self._score)

    @staticmethod
    def _score(ro: RecordOptions, background: float, policy: AssociationPolicy) -> list[Scored]:
        rec = ro.record
        q = policy.counterpart_prior
        lrs = []
        for opt in ro.options:
            p = opt.position
            off = np.array(offset_arcsec(rec.ra_deg, rec.dec_deg, p.ra_deg, p.dec_deg))
            lrs.append(positional_density(off, _matrix(rec.covariance) + _matrix(p.covariance)) / background)
        denom = q * sum(lrs) + (1 - q)
        out = [Scored(opt, q, lr, None, q * lr / denom) for opt, lr in zip(ro.options, lrs, strict=True)]
        out.append(Scored(None, 1 - q, 1.0, None, (1 - q) / denom))
        return out

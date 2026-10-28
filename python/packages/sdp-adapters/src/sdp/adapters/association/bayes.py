"""Bayes-factor cross-identification, the CPU reference (Budavári and Szalay 2008).

For a record and one candidate, the Bayes factor of "same object" against "distinct objects" with
Gaussian positional errors is ``B = 4π · N(ψ; 0, Σ₁ + Σ₂)`` with ψ the angular offset in radians and
N the bivariate normal density in sr⁻¹. The prior that a given anchor is the counterpart is
``P₀ = 1 / (4π ρ)`` for an anchor density ρ per steradian; with the policy's counterpart prior Q,
the posterior of option k among the record's options and "no counterpart" is
``Q B_k P₀ / (Σ_j Q B_j P₀ + 1 − Q)``. Computed in log space on unit vectors.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from sdp.adapters.association.graph import Scored, build_graph
from sdp.kernel.domain.association import AssociationGraph, AssociationPolicy, AssociationRecord, PositionCovariance
from sdp.kernel.science.association import RecordOptions

ARCSEC_RAD = math.pi / (180.0 * 3600.0)


def _unit(ra_deg: float, dec_deg: float) -> tuple[float, float, float]:
    r, d = math.radians(ra_deg), math.radians(dec_deg)
    return (math.cos(d) * math.cos(r), math.cos(d) * math.sin(r), math.sin(d))


def _local_offset_rad(ra0: float, dec0: float, ra: float, dec: float) -> tuple[float, float]:
    """Offset of (ra, dec) from (ra0, dec0) projected on the local east and north unit vectors."""
    v = _unit(ra, dec)
    r0, d0 = math.radians(ra0), math.radians(dec0)
    east = (-math.sin(r0), math.cos(r0), 0.0)
    north = (-math.sin(d0) * math.cos(r0), -math.sin(d0) * math.sin(r0), math.cos(d0))
    e = sum(a * b for a, b in zip(v, east, strict=True))
    n = sum(a * b for a, b in zip(v, north, strict=True))
    return e, n


def _cov_rad(c: PositionCovariance) -> tuple[float, float, float]:
    sa, sd = c.sigma_ra_arcsec * ARCSEC_RAD, c.sigma_dec_arcsec * ARCSEC_RAD
    return sa * sa, c.rho * sa * sd, sd * sd


def log_bayes_factor(
    ra1: float, dec1: float, c1: PositionCovariance, ra2: float, dec2: float, c2: PositionCovariance
) -> float:
    e, n = _local_offset_rad(ra1, dec1, ra2, dec2)
    a1, b1, d1 = _cov_rad(c1)
    a2, b2, d2 = _cov_rad(c2)
    a, b, d = a1 + a2, b1 + b2, d1 + d2
    det = a * d - b * b
    chi2 = (d * e * e - 2 * b * e * n + a * n * n) / det
    return math.log(4 * math.pi) - math.log(2 * math.pi) - 0.5 * math.log(det) - 0.5 * chi2


def _logsumexp(xs: Sequence[float]) -> float:
    m = max(xs)
    return m + math.log(sum(math.exp(x - m) for x in xs))


class BayesFactorAssociation:
    method = "bayes-factor"
    method_version = "1.0.0"

    def associate(self, records: Sequence[AssociationRecord], policy: AssociationPolicy) -> AssociationGraph:
        return build_graph(records, policy, method=self.method, method_version=self.method_version, score=self._score)

    @staticmethod
    def _score(ro: RecordOptions, density_arcsec2: float, policy: AssociationPolicy) -> list[Scored]:
        rec = ro.record
        q = policy.counterpart_prior
        density_sr = density_arcsec2 / (ARCSEC_RAD * ARCSEC_RAD)
        log_p0 = -math.log(4 * math.pi * density_sr)
        logs: list[float] = []
        lbfs: list[float] = []
        for opt in ro.options:
            p = opt.position
            lbf = log_bayes_factor(rec.ra_deg, rec.dec_deg, rec.covariance, p.ra_deg, p.dec_deg, p.covariance)
            lbfs.append(lbf)
            logs.append(math.log(q) + lbf + log_p0)
        none = math.log(1 - q)
        norm = _logsumexp([*logs, none])
        out = [
            Scored(opt, min(1.0, math.exp(log_p0)), math.exp(lbf + log_p0), math.exp(lbf), math.exp(lg - norm))
            for opt, lbf, lg in zip(ro.options, lbfs, logs, strict=True)
        ]
        out.append(Scored(None, 1 - q, 1.0, None, math.exp(none - norm)))
        return out

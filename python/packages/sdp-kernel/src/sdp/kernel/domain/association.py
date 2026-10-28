"""Cross-observatory association records (Integration Architecture v0.4.0 §4, ADR-019).

A ``Detection`` is one measurement on one product at one epoch; a ``CatalogueSource`` is one row of
a published catalogue; an ``ObjectHypothesis`` is a proposed astrophysical object; an
``Association`` claims that a record belongs to a hypothesis, with its method, prior, likelihood
and posterior. Every candidate with a non-negligible posterior is kept: the set is ranked, never
pruned to a nearest neighbour (INT-009). Only a reviewer's ledger entry makes an association
``accepted`` (INT-011).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

from sdp.kernel.domain.canonical import hash_document
from sdp.kernel.domain.errors import InvalidTransition, InvariantViolation
from sdp.kernel.domain.ids import check_semver

SCHEMA_VERSION = "1.0.0"


def _finite(value: float, what: str) -> float:
    value = float(value)
    if not math.isfinite(value):
        raise InvariantViolation(f"{what} must be finite")
    return value


@dataclass(frozen=True)
class PositionCovariance:
    """Positional uncertainty in arcseconds: sigma along RA*cos(dec), along Dec, and their correlation."""

    sigma_ra_arcsec: float
    sigma_dec_arcsec: float
    rho: float = 0.0

    def __post_init__(self) -> None:
        if not (self.sigma_ra_arcsec > 0 and self.sigma_dec_arcsec > 0):
            raise InvariantViolation("positional sigmas are positive; a record without them cannot be associated")
        if not -1 < self.rho < 1:
            raise InvariantViolation("correlation lies strictly between -1 and 1")

    def matrix(self) -> tuple[tuple[float, float], tuple[float, float]]:
        c = self.rho * self.sigma_ra_arcsec * self.sigma_dec_arcsec
        return ((self.sigma_ra_arcsec**2, c), (c, self.sigma_dec_arcsec**2))

    def to_document(self) -> dict[str, Any]:
        return {"sigma_ra_arcsec": self.sigma_ra_arcsec, "sigma_dec_arcsec": self.sigma_dec_arcsec, "rho": self.rho}


@dataclass(frozen=True)
class Motion:
    """Proper motion (mas/yr, pmra includes cos dec) and parallax (mas) with their sigmas."""

    pmra_mas_yr: float
    pmdec_mas_yr: float
    parallax_mas: float | None = None
    sigma_pmra_mas_yr: float = 0.0
    sigma_pmdec_mas_yr: float = 0.0
    sigma_parallax_mas: float | None = None

    def to_document(self) -> dict[str, Any]:
        return {
            "pmra_mas_yr": self.pmra_mas_yr,
            "pmdec_mas_yr": self.pmdec_mas_yr,
            "parallax_mas": self.parallax_mas,
            "sigma_pmra_mas_yr": self.sigma_pmra_mas_yr,
            "sigma_pmdec_mas_yr": self.sigma_pmdec_mas_yr,
            "sigma_parallax_mas": self.sigma_parallax_mas,
        }


@dataclass(frozen=True)
class Photometry:
    band: str
    magnitude: float
    sigma: float

    def to_document(self) -> dict[str, Any]:
        return {"band": self.band, "magnitude": self.magnitude, "sigma": self.sigma}


@dataclass(frozen=True)
class Detection:
    """One measurement of something on one product at one epoch (v0.4.0 §4)."""

    record_id: str
    product_id: str
    extension: str
    ra_deg: float
    dec_deg: float
    covariance: PositionCovariance
    epoch_jyear: float
    resolution_arcsec: float
    """The instrument's angular resolution: a detection may blend sources closer than this."""
    instrument: str
    photometry: tuple[Photometry, ...] = ()

    def __post_init__(self) -> None:
        _finite(self.ra_deg, "ra")
        _finite(self.dec_deg, "dec")
        if not (0 <= self.ra_deg < 360 and -90 <= self.dec_deg <= 90):
            raise InvariantViolation("detection position out of range")
        if not self.resolution_arcsec > 0:
            raise InvariantViolation("a detection carries its instrument's resolution")

    @property
    def kind(self) -> str:
        return "detection"

    def to_document(self) -> dict[str, Any]:
        return {
            "kind": "detection",
            "record_id": self.record_id,
            "product_id": self.product_id,
            "extension": self.extension,
            "ra_deg": self.ra_deg,
            "dec_deg": self.dec_deg,
            "covariance": self.covariance.to_document(),
            "epoch_jyear": self.epoch_jyear,
            "resolution_arcsec": self.resolution_arcsec,
            "instrument": self.instrument,
            "photometry": [p.to_document() for p in self.photometry],
        }


@dataclass(frozen=True)
class CatalogueSource:
    """One row of a published catalogue, with its release label (v0.4.0 §4)."""

    record_id: str
    catalogue: str
    release_label: str
    source_key: str
    ra_deg: float
    dec_deg: float
    covariance: PositionCovariance
    epoch_jyear: float
    motion: Motion | None = None
    resolution_arcsec: float = 0.0
    photometry: tuple[Photometry, ...] = ()

    def __post_init__(self) -> None:
        if not (0 <= self.ra_deg < 360 and -90 <= self.dec_deg <= 90):
            raise InvariantViolation("catalogue position out of range")

    @property
    def kind(self) -> str:
        return "catalogue_source"

    def to_document(self) -> dict[str, Any]:
        return {
            "kind": "catalogue_source",
            "record_id": self.record_id,
            "catalogue": self.catalogue,
            "release_label": self.release_label,
            "source_key": self.source_key,
            "ra_deg": self.ra_deg,
            "dec_deg": self.dec_deg,
            "covariance": self.covariance.to_document(),
            "epoch_jyear": self.epoch_jyear,
            "motion": self.motion.to_document() if self.motion else None,
            "resolution_arcsec": self.resolution_arcsec,
            "photometry": [p.to_document() for p in self.photometry],
        }


AssociationRecord = Detection | CatalogueSource


class ReviewState(StrEnum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


_ALLOWED: dict[ReviewState, frozenset[ReviewState]] = {
    ReviewState.PROPOSED: frozenset({ReviewState.ACCEPTED, ReviewState.REJECTED, ReviewState.SUPERSEDED}),
    ReviewState.ACCEPTED: frozenset({ReviewState.SUPERSEDED}),
    ReviewState.REJECTED: frozenset({ReviewState.SUPERSEDED}),
    ReviewState.SUPERSEDED: frozenset(),
}


@dataclass(frozen=True)
class ObjectHypothesis:
    """A proposed astrophysical object. It has no position of its own until associations to it
    are accepted (v0.4.0 §4); ``members`` lists the record ids the hypothesis groups."""

    hypothesis_id: str
    members: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.members:
            raise InvariantViolation("an object hypothesis groups at least one record")
        if len(set(self.members)) != len(self.members):
            raise InvariantViolation("hypothesis members are distinct")

    def to_document(self) -> dict[str, Any]:
        return {"hypothesis_id": self.hypothesis_id, "members": list(self.members)}


@dataclass(frozen=True)
class Association:
    """A claim that ``record_id`` belongs to ``hypothesis_id``, and the evidence for it."""

    record_id: str
    hypothesis_id: str
    method: str
    method_version: str
    prior: float
    likelihood: float
    posterior: float
    bayes_factor: float | None
    competing: tuple[str, ...]
    """The other hypotheses this record could belong to, ranked, all retained."""
    evidence: Mapping[str, Any] = field(default_factory=dict)
    state: ReviewState = ReviewState.PROPOSED
    ledger_entry_id: str | None = None
    reviewer: str | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        check_semver(self.method_version)
        for what, p in (("prior", self.prior), ("posterior", self.posterior)):
            if not 0.0 <= p <= 1.0:
                raise InvariantViolation(f"{what} is a probability")
        if self.state is ReviewState.ACCEPTED and not self.ledger_entry_id:
            raise InvalidTransition("an association is accepted only with a reviewer's ledger entry (INT-011)")
        if self.state is ReviewState.REJECTED and not self.reason:
            raise InvariantViolation("a rejected association records the reason")

    def transition(
        self, to: ReviewState, *, reviewer: str, ledger_entry_id: str | None = None, reason: str = ""
    ) -> Association:
        if to not in _ALLOWED[self.state]:
            raise InvalidTransition(f"association cannot move from {self.state.value} to {to.value}")
        if to is ReviewState.ACCEPTED and not ledger_entry_id:
            raise InvalidTransition("an association is accepted only with a reviewer's ledger entry (INT-011)")
        return replace(
            self,
            state=to,
            reviewer=reviewer,
            ledger_entry_id=ledger_entry_id or self.ledger_entry_id,
            reason=reason or self.reason,
        )

    def to_document(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "hypothesis_id": self.hypothesis_id,
            "method": self.method,
            "method_version": self.method_version,
            "prior": self.prior,
            "likelihood": self.likelihood,
            "posterior": self.posterior,
            "bayes_factor": self.bayes_factor,
            "competing": list(self.competing),
            "evidence": dict(self.evidence),
            "state": self.state.value,
            "ledger_entry_id": self.ledger_entry_id,
            "reviewer": self.reviewer,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class AssociationPolicy:
    """Versioned settings for one association run; recorded in the graph."""

    policy_id: str
    version: str
    common_epoch_jyear: float
    search_radius_arcsec: float
    min_posterior: float = 1e-3
    """Below this a candidate is negligible; every candidate above it is retained (INT-009)."""
    photometric_terms: bool = False
    counterpart_prior: float = 0.9
    """Prior probability that a record has a counterpart among the anchors at all."""
    anchor_catalogue: str = "gaia"
    """Catalogue sources from this catalogue anchor the astrometric frame; every other record is
    matched against them at its own epoch."""

    def __post_init__(self) -> None:
        check_semver(self.version)
        if not self.search_radius_arcsec > 0:
            raise InvariantViolation("search radius is positive")
        if not 0 <= self.min_posterior < 0.5:
            raise InvariantViolation("the negligible-posterior floor lies in [0, 0.5)")
        if not 0 < self.counterpart_prior < 1:
            raise InvariantViolation("the counterpart prior lies strictly between 0 and 1")

    def to_document(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "version": self.version,
            "common_epoch_jyear": self.common_epoch_jyear,
            "search_radius_arcsec": self.search_radius_arcsec,
            "min_posterior": self.min_posterior,
            "photometric_terms": self.photometric_terms,
            "counterpart_prior": self.counterpart_prior,
            "anchor_catalogue": self.anchor_catalogue,
        }


@dataclass(frozen=True)
class AssociationGraph:
    """Records, hypotheses and every retained association, with the method and the prior density.

    Stored in the evidence repository beside provenance; the dossier draws it with the competing
    hypotheses it rejected.
    """

    policy: AssociationPolicy
    method: str
    method_version: str
    records: tuple[AssociationRecord, ...]
    hypotheses: tuple[ObjectHypothesis, ...]
    associations: tuple[Association, ...]
    source_density_per_arcsec2: float

    def __post_init__(self) -> None:
        record_ids = {r.record_id for r in self.records}
        if len(record_ids) != len(self.records):
            raise InvariantViolation("record ids are unique within a graph")
        hyp_ids = {h.hypothesis_id for h in self.hypotheses}
        if len(hyp_ids) != len(self.hypotheses):
            raise InvariantViolation("hypothesis ids are unique within a graph")
        for a in self.associations:
            if a.record_id not in record_ids or a.hypothesis_id not in hyp_ids:
                raise InvariantViolation("an association links a record and a hypothesis of the same graph")
        if not (math.isfinite(self.source_density_per_arcsec2) and self.source_density_per_arcsec2 >= 0):
            raise InvariantViolation("the chance-alignment density is a finite non-negative number")

    @property
    def graph_id(self) -> str:
        return hash_document(self.to_document(include_id=False))

    def for_record(self, record_id: str) -> tuple[Association, ...]:
        return tuple(
            sorted(
                (a for a in self.associations if a.record_id == record_id),
                key=lambda a: (-a.posterior, a.hypothesis_id),
            )
        )

    def ambiguous_records(self) -> tuple[str, ...]:
        """Records with more than one retained hypothesis."""
        counts: dict[str, int] = {}
        for a in self.associations:
            counts[a.record_id] = counts.get(a.record_id, 0) + 1
        return tuple(sorted(r for r, n in counts.items() if n > 1))

    def accepted(self) -> tuple[Association, ...]:
        return tuple(a for a in self.associations if a.state is ReviewState.ACCEPTED)

    def review(
        self,
        record_id: str,
        hypothesis_id: str,
        to: ReviewState,
        *,
        reviewer: str,
        ledger_entry_id: str | None = None,
        reason: str = "",
    ) -> AssociationGraph:
        updated = []
        found = False
        for a in self.associations:
            if a.record_id == record_id and a.hypothesis_id == hypothesis_id:
                updated.append(a.transition(to, reviewer=reviewer, ledger_entry_id=ledger_entry_id, reason=reason))
                found = True
            else:
                updated.append(a)
        if not found:
            raise InvariantViolation(f"no association of {record_id} with {hypothesis_id}")
        return replace(self, associations=tuple(updated))

    def to_document(self, *, include_id: bool = True) -> dict[str, Any]:
        doc: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "method": self.method,
            "method_version": self.method_version,
            "policy": self.policy.to_document(),
            "source_density_per_arcsec2": self.source_density_per_arcsec2,
            "records": [r.to_document() for r in self.records],
            "hypotheses": [h.to_document() for h in self.hypotheses],
            "associations": [a.to_document() for a in self.associations],
        }
        if include_id:
            doc["graph_id"] = self.graph_id
        return doc


def records_by_id(records: Sequence[AssociationRecord]) -> dict[str, AssociationRecord]:
    return {r.record_id: r for r in records}

"""Reference pulls: what other catalogues hold at a candidate's position, kept with the candidate (INV-023).

A pull is the answer of one reference catalogue (SIMBAD, Gaia DR3, 2MASS, AllWISE, Pan-STARRS, AAVSO
VSX, ...) to one cone around the candidate. It is context for a scientist deciding whether a candidate
is already known or an artefact, never evidence a test read: it does not change the candidate's
results, rank, state or the evidence it used. Each pull records when and by whom it was asked, the
release and citation, the cone, and the archive's raw answer as an artifact, so the comparison a
scientist made can be checked later.

Rows are canonical: the same names as the dossier's catalogue rows (``ra_deg``, ``dec_deg``,
``epoch_jyear``, ``pm_ra_cosdec_masyr``, ``pm_dec_masyr``, ``parallax_mas``, ``g_mag``, ``j_mag``, ...),
plus ``separation_arcsec`` from the candidate, so a view can line catalogues up quantity by quantity.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sdp.kernel.domain.errors import InvariantViolation

PullStatus = Literal["found", "none", "failed"]
PULL_STATUSES: tuple[str, ...] = ("found", "none", "failed")
#: the widest cone a pull may ask: a candidate structure spans arcminutes, a star a few arcseconds
MAX_RADIUS_ARCSEC = 600.0
#: rows kept per pull, nearest first; the raw answer artifact keeps every row
MAX_MATCHES = 10


@dataclass(frozen=True)
class ReferenceTarget:
    """Where a pull looks: the candidate's position at ``epoch_jyear`` and, when known, its proper motion,
    so a catalogue observed at another epoch is compared where the candidate was then."""

    ra_deg: float
    dec_deg: float
    epoch_jyear: float | None = None
    pm_ra_cosdec_masyr: float | None = None
    pm_dec_masyr: float | None = None

    def __post_init__(self) -> None:
        if not (math.isfinite(self.ra_deg) and math.isfinite(self.dec_deg)) or not -90 <= self.dec_deg <= 90:
            raise InvariantViolation(f"a reference target needs a sky position, got ({self.ra_deg}, {self.dec_deg})")

    def at(self, epoch_jyear: float | None) -> tuple[float, float]:
        """The target's position at ``epoch_jyear``: moved along its proper motion when both epochs and
        the motion are known, else where it was measured. Linear in the tangent plane, which is ample
        over decades for the arcsecond comparisons a pull makes."""
        if (
            epoch_jyear is None
            or self.epoch_jyear is None
            or self.pm_ra_cosdec_masyr is None
            or self.pm_dec_masyr is None
        ):
            return self.ra_deg, self.dec_deg
        years = epoch_jyear - self.epoch_jyear
        cos_dec = max(math.cos(math.radians(self.dec_deg)), 1e-9)
        ra = self.ra_deg + self.pm_ra_cosdec_masyr * years / 3.6e6 / cos_dec
        dec = self.dec_deg + self.pm_dec_masyr * years / 3.6e6
        return ra % 360.0, max(-90.0, min(90.0, dec))

    def to_document(self) -> dict[str, Any]:
        return {
            "ra_deg": self.ra_deg,
            "dec_deg": self.dec_deg,
            "epoch_jyear": self.epoch_jyear,
            "pm_ra_cosdec_masyr": self.pm_ra_cosdec_masyr,
            "pm_dec_masyr": self.pm_dec_masyr,
        }

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> ReferenceTarget:
        return cls(
            float(doc["ra_deg"]),
            float(doc["dec_deg"]),
            doc.get("epoch_jyear"),
            doc.get("pm_ra_cosdec_masyr"),
            doc.get("pm_dec_masyr"),
        )


def separation_arcsec(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    """Great-circle separation by the haversine formula, accurate at the sub-arcsecond scale."""
    p1, p2 = math.radians(dec1), math.radians(dec2)
    dp, dl = p2 - p1, math.radians(ra2 - ra1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return math.degrees(2 * math.asin(min(1.0, math.sqrt(h)))) * 3600.0


def matches_near(
    target: ReferenceTarget, rows: Sequence[Mapping[str, Any]], radius_arcsec: float, limit: int = MAX_MATCHES
) -> tuple[dict[str, Any], ...]:
    """The rows within ``radius_arcsec`` of the target, each with its ``separation_arcsec`` from where the
    target was at the row's epoch, nearest first. A row without a position is left out."""
    found: list[dict[str, Any]] = []
    for row in rows:
        ra, dec = row.get("ra_deg"), row.get("dec_deg")
        if not isinstance(ra, int | float) or not isinstance(dec, int | float):
            continue
        if not (math.isfinite(ra) and math.isfinite(dec)):
            continue
        epoch = row.get("epoch_jyear")
        here = target.at(float(epoch) if isinstance(epoch, int | float) and math.isfinite(epoch) else None)
        sep = separation_arcsec(here[0], here[1], float(ra), float(dec))
        if sep <= radius_arcsec:
            found.append({**row, "separation_arcsec": sep})
    found.sort(key=lambda r: (r["separation_arcsec"], str(r.get("source_key", ""))))
    return tuple(found[:limit])


@dataclass(frozen=True)
class ReferencePull:
    """One reference catalogue's answer to one cone around a candidate."""

    source_id: str
    title: str
    release_label: str
    citation: str
    radius_arcsec: float
    target: ReferenceTarget
    asked_at: datetime
    actor: str
    status: PullStatus
    matches: tuple[Mapping[str, Any], ...] = ()
    #: the archive's answer as it came, stored under its SHA-256; none when the archive did not answer
    raw_artifact_id: str | None = None
    #: why a failed pull failed, in the archive's or the transport's words
    error: str | None = None

    def __post_init__(self) -> None:
        if self.status not in PULL_STATUSES:
            raise InvariantViolation(f"a pull is one of {PULL_STATUSES}, not {self.status!r}")
        if not self.actor.strip():
            raise InvariantViolation("a reference pull records who asked for it")
        if not 0 < self.radius_arcsec <= MAX_RADIUS_ARCSEC:
            raise InvariantViolation(f"a pull's radius is above 0 and at most {MAX_RADIUS_ARCSEC} arcsec")
        if (self.status == "found") != bool(self.matches):
            raise InvariantViolation("a pull is 'found' exactly when it holds matches")
        if self.status == "failed" and not self.error:
            raise InvariantViolation("a failed pull says why")

    def to_document(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "title": self.title,
            "release_label": self.release_label,
            "citation": self.citation,
            "radius_arcsec": self.radius_arcsec,
            "target": self.target.to_document(),
            "asked_at": self.asked_at.isoformat(),
            "actor": self.actor,
            "status": self.status,
            "matches": [_jsonable(dict(m)) for m in self.matches],
            "raw_artifact_id": self.raw_artifact_id,
            "error": self.error,
        }

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> ReferencePull:
        return cls(
            source_id=str(doc["source_id"]),
            title=str(doc.get("title", doc["source_id"])),
            release_label=str(doc["release_label"]),
            citation=str(doc.get("citation", "")),
            radius_arcsec=float(doc["radius_arcsec"]),
            target=ReferenceTarget.from_document(doc["target"]),
            asked_at=datetime.fromisoformat(doc["asked_at"]),
            actor=str(doc["actor"]),
            status=doc["status"],
            matches=tuple(dict(m) for m in doc.get("matches", ())),
            raw_artifact_id=doc.get("raw_artifact_id"),
            error=doc.get("error"),
        )


def _jsonable(value: Any) -> Any:
    """Canonical JSON holds no NaN or infinity: a missing value is null."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    return value

"""PullReferences: ask reference catalogues what they hold at a candidate's position (INV-024).

The scientist ticks catalogues on the candidate's comparison page; each is asked for one cone around
the candidate, at the same time, and every answer, a catalogue that found nothing and one that did not
answer included, is kept with the investigation beside the archive's raw answer. A pull never touches
the candidate's results, rank, state or the evidence it used: references are for judging whether a
candidate is already known or an artefact, and a confirmatory test still has to name its own evidence.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sdp.kernel.domain.errors import InvariantViolation
from sdp.kernel.domain.investigation import Investigation
from sdp.kernel.domain.references import (
    MAX_RADIUS_ARCSEC,
    PullStatus,
    ReferencePull,
    ReferenceTarget,
    matches_near,
)
from sdp.kernel.ports.determinism import Clock
from sdp.kernel.ports.references import ReferenceCatalogue
from sdp.kernel.ports.repositories import UnitOfWork
from sdp.kernel.ports.storage import ArtifactStore

#: the cone a scientist gets unless they choose another: wide enough for 2MASS and WISE positions
#: against Gaia's, narrow enough that a neighbour rarely answers for the candidate
DEFAULT_RADIUS_ARCSEC = 5.0
#: how much of an archive's error message a failed pull keeps
ERROR_CHARS = 500


@dataclass(frozen=True)
class PullReferencesCommand:
    investigation_id: str
    sources: tuple[str, ...]
    actor: str
    radius_arcsec: float = DEFAULT_RADIUS_ARCSEC


def target_of(investigation: Investigation, dossier: Mapping[str, Any] | None) -> ReferenceTarget:
    """Where to look: the candidate object's position and motion from its dossier, or the centre of a
    structure's members. A candidate without a dossier has no recorded position, so nothing is pulled."""
    if dossier is None:
        raise InvariantViolation(f"investigation {investigation.investigation_id} has no dossier, so no position")
    objects = {o["object"]["object_key"]: o for o in dossier.get("objects", []) if "object" in o}
    subject = investigation.candidate
    if subject.object_key is not None:
        found = objects.get(subject.object_key)
        if found is None:
            raise InvariantViolation(f"the dossier does not hold {subject.object_key}")
        ra, dec = (float(v) for v in found["object"]["sky_icrs"])
        motion = _motion(found.get("catalogue_rows", {}))
        if motion is None:
            return ReferenceTarget(ra, dec)
        return ReferenceTarget(ra, dec, *motion)
    members = [objects[k]["object"]["sky_icrs"] for k in subject.members if k in objects]
    if not members:
        raise InvariantViolation(f"the dossier holds none of {subject.key}'s members")
    return ReferenceTarget(*_centre([(float(r), float(d)) for r, d in members]))


def _motion(rows_by_source: Mapping[str, Sequence[Mapping[str, Any]]]) -> tuple[float, float, float] | None:
    """The epoch and proper motion of the first catalogue row that has all three (Gaia's, in practice)."""
    for source in sorted(rows_by_source):
        for row in rows_by_source[source]:
            values = (row.get("epoch_jyear"), row.get("pm_ra_cosdec_masyr"), row.get("pm_dec_masyr"))
            if all(isinstance(v, int | float) and math.isfinite(v) for v in values):
                return float(values[0]), float(values[1]), float(values[2])  # type: ignore[arg-type]
    return None


def _centre(positions: Sequence[tuple[float, float]]) -> tuple[float, float]:
    x = y = z = 0.0
    for ra, dec in positions:
        r, d = math.radians(ra), math.radians(dec)
        x, y, z = x + math.cos(d) * math.cos(r), y + math.cos(d) * math.sin(r), z + math.sin(d)
    ra = math.degrees(math.atan2(y, x)) % 360.0
    dec = math.degrees(math.atan2(z, math.hypot(x, y)))
    return ra, dec


@dataclass
class PullReferencesUseCase:
    uow: Callable[[], UnitOfWork]
    store: ArtifactStore
    clock: Clock
    catalogues: Mapping[str, ReferenceCatalogue]
    #: the candidate's dossier by investigation id (QueryInvestigations.dossier)
    dossier: Callable[[str], dict[str, Any] | None]

    def available(self) -> list[ReferenceCatalogue]:
        return [self.catalogues[k] for k in sorted(self.catalogues)]

    def execute(self, command: PullReferencesCommand) -> Investigation:
        if not command.actor.strip():
            raise InvariantViolation("a reference pull records who asked for it")
        sources = tuple(dict.fromkeys(command.sources))
        if not sources:
            raise InvariantViolation("choose at least one catalogue to pull")
        unknown = [s for s in sources if s not in self.catalogues]
        if unknown:
            raise InvariantViolation(f"unknown reference catalogue {unknown[0]!r}; known: {sorted(self.catalogues)}")
        if not 0 < command.radius_arcsec <= MAX_RADIUS_ARCSEC:
            raise InvariantViolation(f"the search radius is above 0 and at most {MAX_RADIUS_ARCSEC:g} arcsec")
        with self.uow() as uow:
            investigation = uow.investigations.get(command.investigation_id)
        if investigation is None:
            raise InvariantViolation(f"no investigation {command.investigation_id}")
        target = target_of(investigation, self.dossier(command.investigation_id))
        asked_at = self.clock.now()
        # Archives answer in seconds to a minute; asking them together makes the wait the slowest one.
        with ThreadPoolExecutor(max_workers=min(8, len(sources))) as pool:
            pulls = list(pool.map(lambda s: self._pull(s, target, command, asked_at), sources))
        with self.uow() as uow:
            fresh = uow.investigations.get(command.investigation_id)
            assert fresh is not None
            fresh.add_references(pulls)
            uow.investigations.save(fresh)
            uow.commit()
        return fresh

    def _pull(
        self, source_id: str, target: ReferenceTarget, command: PullReferencesCommand, asked_at: datetime
    ) -> ReferencePull:
        catalogue = self.catalogues[source_id]

        def answer(
            status: PullStatus,
            matches: tuple[Mapping[str, Any], ...] = (),
            raw: str | None = None,
            error: str | None = None,
        ) -> ReferencePull:
            return ReferencePull(
                source_id=source_id,
                title=catalogue.title,
                release_label=catalogue.release_label,
                citation=catalogue.citation,
                radius_arcsec=command.radius_arcsec,
                target=target,
                asked_at=asked_at,
                actor=command.actor,
                status=status,
                matches=matches,
                raw_artifact_id=raw,
                error=error,
            )

        # The cone is asked a little wider than the radius, because a catalogue observed decades from the
        # candidate's epoch sees it elsewhere; the rows kept are those within the radius once moved.
        try:
            reply = catalogue.cone(target.ra_deg, target.dec_deg, command.radius_arcsec + _drift(target))
        except Exception as exc:  # any failure of one archive is that pull's answer, not the request's
            return answer("failed", error=f"{type(exc).__name__}: {exc}"[:ERROR_CHARS])
        raw = self.store.put(reply.raw, reply.media_type)
        matches = matches_near(target, reply.rows, command.radius_arcsec)
        return answer("found" if matches else "none", matches, raw.value)


def _drift(target: ReferenceTarget, years: float = 30.0) -> float:
    """How far the candidate moves in ``years``, in arcseconds; 0 without a known motion."""
    if target.pm_ra_cosdec_masyr is None or target.pm_dec_masyr is None:
        return 0.0
    return min(math.hypot(target.pm_ra_cosdec_masyr, target.pm_dec_masyr) * years / 1000.0, MAX_RADIUS_ARCSEC)

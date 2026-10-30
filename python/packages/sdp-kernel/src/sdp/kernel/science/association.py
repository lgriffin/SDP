"""Pure cores shared by the association implementations (Integration v0.4.0 §4, §12 T0).

The two probability methods live apart in ``sdp.adapters.association`` (principle 12); what they
share is the geometry they are both given: epoch propagation with grown covariance (INT-010), the
candidate options for each record, blend groups for detections coarser than their candidates, and
the chance-alignment density measured from the field itself.

An *anchor* is a source from the policy's anchor catalogue (Gaia by default); every other record is matched against anchors propagated to its
own epoch, so a 1950s plate and a 2023 detection are compared at one epoch or not at all.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from sdp.kernel.domain.association import (
    AssociationPolicy,
    AssociationRecord,
    CatalogueSource,
    PositionCovariance,
)

MAS_PER_ARCSEC = 1000.0
MAX_EPOCH_GAP_WITHOUT_MOTION_YR = 1.0
"""An anchor with no proper motion is compared only with records within this many years of it."""


@dataclass(frozen=True)
class Propagated:
    """A position at a target epoch with its grown covariance; offsets are in arcsec on the tangent plane."""

    ra_deg: float
    dec_deg: float
    covariance: PositionCovariance
    epoch_jyear: float


def propagate(source: CatalogueSource, epoch_jyear: float) -> Propagated | None:
    """Move a catalogue source to ``epoch_jyear`` along its proper motion (INT-010).

    Straight-line motion at constant distance, which is ``SkyCoord.apply_space_motion`` with zero
    radial velocity; parallax is not applied (no observer position).
    Covariance grows by the proper-motion sigmas times the baseline. Returns ``None`` when the source
    has no motion and the gap exceeds :data:`MAX_EPOCH_GAP_WITHOUT_MOTION_YR`.
    """
    dt = epoch_jyear - source.epoch_jyear
    if source.motion is None:
        if abs(dt) > MAX_EPOCH_GAP_WITHOUT_MOTION_YR:
            return None
        return Propagated(source.ra_deg, source.dec_deg, source.covariance, epoch_jyear)
    m = source.motion
    ra, dec = _move(source.ra_deg, source.dec_deg, m.pmra_mas_yr * dt, m.pmdec_mas_yr * dt)
    c = source.covariance
    grow_ra = (m.sigma_pmra_mas_yr * abs(dt) / MAS_PER_ARCSEC) ** 2
    grow_dec = (m.sigma_pmdec_mas_yr * abs(dt) / MAS_PER_ARCSEC) ** 2
    s_ra = math.sqrt(c.sigma_ra_arcsec**2 + grow_ra)
    s_dec = math.sqrt(c.sigma_dec_arcsec**2 + grow_dec)
    rho = c.rho * c.sigma_ra_arcsec * c.sigma_dec_arcsec / (s_ra * s_dec)
    return Propagated(ra, dec, PositionCovariance(s_ra, s_dec, rho), epoch_jyear)


def _wrap_ra(ra_deg: float) -> float:
    ra_deg %= 360.0
    return 0.0 if ra_deg >= 360.0 else ra_deg


def _move(ra_deg: float, dec_deg: float, east_mas: float, north_mas: float) -> tuple[float, float]:
    """Displace a position by a tangent vector and renormalise: straight-line space motion at
    constant distance, as ``SkyCoord.apply_space_motion`` does with zero radial velocity."""
    r, d = math.radians(ra_deg), math.radians(dec_deg)
    p = (math.cos(d) * math.cos(r), math.cos(d) * math.sin(r), math.sin(d))
    e = (-math.sin(r), math.cos(r), 0.0)
    n = (-math.sin(d) * math.cos(r), -math.sin(d) * math.sin(r), math.cos(d))
    ke = math.radians(east_mas / MAS_PER_ARCSEC / 3600.0)
    kn = math.radians(north_mas / MAS_PER_ARCSEC / 3600.0)
    v = tuple(p[i] + ke * e[i] + kn * n[i] for i in range(3))
    norm = math.sqrt(sum(x * x for x in v))
    x, y, z = (c / norm for c in v)
    return _wrap_ra(math.degrees(math.atan2(y, x))), math.degrees(math.asin(max(-1.0, min(1.0, z))))


def separation_arcsec(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    """Great-circle separation in arcsec (haversine), valid at every separation up to 180 degrees."""
    r1, d1, r2, d2 = map(math.radians, (ra1, dec1, ra2, dec2))
    h = math.sin((d2 - d1) / 2) ** 2 + math.cos(d1) * math.cos(d2) * math.sin((r2 - r1) / 2) ** 2
    return math.degrees(2 * math.asin(min(1.0, math.sqrt(h)))) * 3600.0


def offset_arcsec(ra0: float, dec0: float, ra: float, dec: float) -> tuple[float, float]:
    """Gnomonic offset of (ra, dec) from (ra0, dec0), in arcsec east and north.

    A point 90 degrees or more away has no gnomonic projection; it is infinitely far, so it never
    falls inside a search radius (near the antipode the formula would otherwise shrink towards 0).
    """
    r, d, r0, d0 = map(math.radians, (ra, dec, ra0, dec0))
    cos_c = math.sin(d0) * math.sin(d) + math.cos(d0) * math.cos(d) * math.cos(r - r0)
    if cos_c <= 0:
        return math.inf, math.inf
    x = math.cos(d) * math.sin(r - r0) / cos_c
    y = (math.cos(d0) * math.sin(d) - math.sin(d0) * math.cos(d) * math.cos(r - r0)) / cos_c
    return math.degrees(x) * 3600.0, math.degrees(y) * 3600.0


@dataclass(frozen=True)
class CandidateOption:
    """One way a record could be explained: by one anchor, or by a blend of several.

    ``members`` are anchor record ids; ``position`` is the anchor (or the blend's mean position)
    propagated to the record's epoch, with the members' mean covariance.
    """

    members: tuple[str, ...]
    position: Propagated

    @property
    def is_blend(self) -> bool:
        return len(self.members) > 1


@dataclass(frozen=True)
class RecordOptions:
    record: AssociationRecord
    options: tuple[CandidateOption, ...]


def _group_blends(near: list[tuple[str, Propagated]], resolution_arcsec: float) -> list[list[tuple[str, Propagated]]]:
    """Friends-of-friends on the propagated anchors with the record's resolution as linking length."""
    groups: list[list[tuple[str, Propagated]]] = []
    unvisited = sorted(near, key=lambda kv: kv[0])
    while unvisited:
        seed = unvisited.pop(0)
        group = [seed]
        frontier = [seed]
        while frontier:
            _, p = frontier.pop()
            close = []
            for kv in unvisited:
                dx, dy = offset_arcsec(p.ra_deg, p.dec_deg, kv[1].ra_deg, kv[1].dec_deg)
                if math.hypot(dx, dy) < resolution_arcsec:
                    close.append(kv)
            for kv in close:
                unvisited.remove(kv)
            group.extend(close)
            frontier.extend(close)
        groups.append(sorted(group, key=lambda kv: kv[0]))
    return groups


def _blend_position(group: list[tuple[str, Propagated]], epoch: float) -> Propagated:
    """Where a detection that blends the group is expected: its members' mean position, uncertain by
    the members' own sigmas and by where between them the light falls (INT-023).

    A blend's centroid is its members' positions weighted by their flux in the detection's band,
    which nothing here knows: a G = 13 star 5 arcsec from a G = 17.6 one puts a WISE centroid on the
    bright star, not halfway. With every flux split equally likely (uniform weights on the simplex),
    the centroid has the members' mean and their scatter about it over n + 1 as its covariance, which
    is added to the members' mean covariance. For two members d apart that is d^2 / 12 along the line
    joining them."""
    n = len(group)
    ra0, dec0 = group[0][1].ra_deg, group[0][1].dec_deg
    xs = [offset_arcsec(ra0, dec0, p.ra_deg, p.dec_deg) for _, p in group]
    mx = sum(x for x, _ in xs) / n
    my = sum(y for _, y in xs) / n
    dec = dec0 + my / 3600.0
    ra = _wrap_ra(ra0 + mx / 3600.0 / math.cos(math.radians(dec0)))
    split = n * (n + 1)
    sxx = sum((x - mx) ** 2 for x, _ in xs) / split
    syy = sum((y - my) ** 2 for _, y in xs) / split
    sxy = sum((x - mx) * (y - my) for x, y in xs) / split
    s_ra = math.sqrt(sum(p.covariance.sigma_ra_arcsec**2 for _, p in group) / n + sxx)
    s_dec = math.sqrt(sum(p.covariance.sigma_dec_arcsec**2 for _, p in group) / n + syy)
    return Propagated(ra, dec, PositionCovariance(s_ra, s_dec, sxy / (s_ra * s_dec)), epoch)


def is_anchor(record: AssociationRecord, policy: AssociationPolicy) -> bool:
    return isinstance(record, CatalogueSource) and record.catalogue == policy.anchor_catalogue


def candidate_options(records: Sequence[AssociationRecord], policy: AssociationPolicy) -> tuple[RecordOptions, ...]:
    """For each non-anchor record, its options within the search radius; anchors get none.

    Anchors closer together than the record's resolution are one blend option rather than several
    one-to-one options, so a coarse detection that blends two sources is never matched to one of
    them alone. Output is sorted by record id, so the result does not depend on input order.
    """
    anchors = sorted((r for r in records if is_anchor(r, policy)), key=lambda r: r.record_id)
    out: list[RecordOptions] = []
    for rec in sorted(records, key=lambda r: r.record_id):
        if is_anchor(rec, policy):
            out.append(RecordOptions(rec, ()))
            continue
        near: list[tuple[str, Propagated]] = []
        for a in anchors:
            assert isinstance(a, CatalogueSource)
            p = propagate(a, rec.epoch_jyear)
            if p is None:
                continue
            if separation_arcsec(rec.ra_deg, rec.dec_deg, p.ra_deg, p.dec_deg) <= policy.search_radius_arcsec:
                near.append((a.record_id, p))
        options = []
        for group in _group_blends(near, rec.resolution_arcsec):
            if len(group) == 1:
                options.append(CandidateOption((group[0][0],), group[0][1]))
            else:
                options.append(CandidateOption(tuple(k for k, _ in group), _blend_position(group, rec.epoch_jyear)))
        out.append(RecordOptions(rec, tuple(options)))
    return tuple(out)


def field_density_per_arcsec2(records: Sequence[AssociationRecord], policy: AssociationPolicy) -> float:
    """Anchors per square arcsecond over the circle that holds every record (the chance-alignment prior).

    Measured from the field itself, as v0.4.0 §4 requires; at least one anchor's worth over a
    one-arcminute circle, so an empty or tiny field never yields a zero or infinite prior.
    """
    if not records:
        return 1.0 / (math.pi * 60.0**2)
    ra0 = (
        math.degrees(
            math.atan2(
                sum(math.sin(math.radians(r.ra_deg)) for r in records),
                sum(math.cos(math.radians(r.ra_deg)) for r in records),
            )
        )
        % 360.0
    )
    dec0 = sum(r.dec_deg for r in records) / len(records)
    radius = max(separation_arcsec(ra0, dec0, r.ra_deg, r.dec_deg) for r in records)
    radius = max(radius, 60.0)
    anchors = max(1, sum(1 for r in records if is_anchor(r, policy)))
    return anchors / (math.pi * radius**2)

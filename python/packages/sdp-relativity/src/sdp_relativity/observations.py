"""ObservationSet: one per instrument, built from transcribed records with their locators (REL-001).

The input bundle is JSON exported from the historical-sources store: every star row carries the
record id and page or table locator it was transcribed from, its uncertainty and that uncertainty's
convention; the bundle also carries the second-pass verification records (REL-G-02).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from sdp_relativity.geometry import StarGeometry
from sdp_relativity.measurement import Convention, Uncertainty, uncertainty

BUNDLE_SCHEMA = "sdp-relativity/observations@1"


@dataclass(frozen=True)
class StarMeasurement:
    star_id: str
    record_id: str
    locator: str
    transcriber: str
    geometry: StarGeometry
    dx_arcsec: float | None
    dy_arcsec: float | None
    error: Uncertainty
    convention: Convention

    @property
    def axes(self) -> tuple[tuple[str, float], ...]:
        """The measured axes: the 1920 tables do not always give both."""
        out: list[tuple[str, float]] = []
        if self.dx_arcsec is not None:
            out.append(("x", self.dx_arcsec))
        if self.dy_arcsec is not None:
            out.append(("y", self.dy_arcsec))
        return tuple(out)


@dataclass(frozen=True)
class Plate:
    plate_id: str
    stars: tuple[StarMeasurement, ...]
    #: Scale (arcsec of displacement per arcmin of position) and rotation from check plates.
    check_scale: float = 0.0
    check_rotation: float = 0.0
    #: Uncertainty of the check-plate scale; plate-correlated errors when the scale is fixed.
    check_scale_sigma: float = 0.0


@dataclass(frozen=True)
class ObservationSet:
    instrument: str
    plates: tuple[Plate, ...]
    solar_radius_arcmin: float

    @property
    def stars(self) -> tuple[StarMeasurement, ...]:
        return tuple(s for p in self.plates for s in p.stars)

    def restrict(self, star_ids: Sequence[tuple[str, str]]) -> ObservationSet:
        """A resample keyed by (plate, star) pairs, duplicates kept (bootstrap)."""
        by_plate: dict[str, list[StarMeasurement]] = {p.plate_id: [] for p in self.plates}
        lookup = {(p.plate_id, s.star_id): s for p in self.plates for s in p.stars}
        for i, key in enumerate(star_ids):
            star = lookup[key]
            by_plate[key[0]].append(StarMeasurement(**{**star.__dict__, "star_id": f"{star.star_id}#{i}"}))
        plates = tuple(
            Plate(p.plate_id, tuple(by_plate[p.plate_id]), p.check_scale, p.check_rotation, p.check_scale_sigma)
            for p in self.plates
            if by_plate[p.plate_id]
        )
        return ObservationSet(self.instrument, plates, self.solar_radius_arcmin)


@dataclass(frozen=True)
class Verification:
    record_id: str
    verifier: str
    matches: bool
    verified_at: str = ""
    """ISO 8601 time of the pass; empty in a hand-made bundle, where the listed order stands."""


@dataclass(frozen=True)
class ObservationBundle:
    sets: tuple[ObservationSet, ...]
    verifications: tuple[Verification, ...] = ()
    #: record id -> document locator, for every transcription the bundle was exported from.
    records: Mapping[str, str] = field(default_factory=dict)
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def instrument(self, name: str) -> ObservationSet:
        for s in self.sets:
            if s.instrument == name:
                return s
        raise KeyError(f"no observation set for instrument {name!r}")

    @property
    def instruments(self) -> tuple[str, ...]:
        return tuple(s.instrument for s in self.sets)


def _star(raw: Mapping[str, Any], solar_radius: float) -> StarMeasurement:
    convention = Convention(raw["convention"])
    return StarMeasurement(
        star_id=str(raw["star_id"]),
        record_id=str(raw.get("record_id", "")),
        locator=str(raw.get("locator", "")),
        transcriber=str(raw.get("transcriber", "")),
        geometry=StarGeometry(float(raw["x_arcmin"]), float(raw["y_arcmin"]), solar_radius),
        dx_arcsec=None if raw.get("dx_arcsec") is None else float(raw["dx_arcsec"]),
        dy_arcsec=None if raw.get("dy_arcsec") is None else float(raw["dy_arcsec"]),
        error=uncertainty(float(raw["error_arcsec"]), convention),
        convention=convention,
    )


def parse_bundle(doc: Mapping[str, Any]) -> ObservationBundle:
    if doc.get("schema") != BUNDLE_SCHEMA:
        raise ValueError(f"expected an observation bundle {BUNDLE_SCHEMA!r}, got {doc.get('schema')!r}")
    sets = []
    for raw_set in doc["instrument_sets"]:
        radius = float(raw_set["solar_radius_arcmin"])
        plates = tuple(
            Plate(
                plate_id=str(p["plate_id"]),
                stars=tuple(_star(s, radius) for s in p["stars"]),
                check_scale=float(p.get("check_scale", 0.0)),
                check_rotation=float(p.get("check_rotation", 0.0)),
                check_scale_sigma=float(p.get("check_scale_sigma", 0.0)),
            )
            for p in raw_set["plates"]
        )
        sets.append(ObservationSet(str(raw_set["instrument"]), plates, radius))
    return ObservationBundle(
        sets=tuple(sets),
        verifications=tuple(
            Verification(str(v["record_id"]), str(v["verifier"]), bool(v["matches"]), str(v.get("verified_at", "")))
            for v in doc.get("verifications", [])
        ),
        records={str(k): str(v) for k, v in doc.get("records", {}).items()},
        provenance=dict(doc.get("provenance", {})),
    )


@dataclass(frozen=True)
class PoolingJustification:
    """Instruments are never pooled without one (REL-004): why, and what is assumed common."""

    instruments: tuple[str, ...]
    reason: str
    assumptions: tuple[str, ...]

    def __post_init__(self) -> None:
        if len(self.instruments) < 2:
            raise ValueError("pooling needs at least two instruments")
        if not self.reason.strip() or not self.assumptions:
            raise ValueError("a pooling justification states its reason and its assumptions")

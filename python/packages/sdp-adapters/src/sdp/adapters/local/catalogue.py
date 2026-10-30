"""A local source's catalogue: every frame, where it is on the sky, when, and how good (#71).

``<data>/catalogue/<source>/frames.parquet`` joins the header index, the solutions and the grades into one
row per frame, and is rebuilt in seconds from those files (no database). ``find`` answers "which frames
cover this place, and when?" from each solved frame's four corners; ``coverage`` sums each target's
nights and hours; ``descriptor`` describes the solved frames to the planner as any archive is described.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from sdp.adapters.local.index import FrameIndex
from sdp.adapters.local.quality import GOOD, POOR, USABLE, Grades, _night
from sdp.adapters.local.solver import pixel_to_sky, project
from sdp.adapters.local.solving import SOLVABLE_KINDS, LocalSolver, usable_name
from sdp.adapters.local.sources import LocalSource
from sdp.kernel.domain.archive import Cone
from sdp.kernel.domain.integration import (
    AdapterInfo,
    Coverage,
    Operation,
    ProductType,
    Rights,
    SourceCapabilityDescriptor,
)

SOLVED, REFUSED, TO_DO, NOT_SOLVED = "solved", "refused", "to_do", "not_solvable"
_MJD_UNIX = 40587.0
#: the widest cone the domain allows (Cone)
MAX_CONE_DEG = 10.0

SCHEMA = pa.schema(
    [
        ("path", pa.string()),
        ("kind", pa.string()),
        ("target", pa.string()),
        ("object", pa.string()),
        ("date_obs", pa.string()),
        ("mjd", pa.float64()),
        ("night", pa.string()),
        ("exposure_s", pa.float64()),
        ("stackcnt", pa.int64()),
        ("filter", pa.string()),
        ("size", pa.int64()),
        ("status", pa.string()),  # solved, refused, to_do, not_solvable (calibration or unreadable)
        ("reason", pa.string()),
        ("ra_deg", pa.float64()),  # the frame's centre
        ("dec_deg", pa.float64()),
        ("radius_deg", pa.float64()),  # half its diagonal
        ("rotation_deg", pa.float64()),
        ("scale_arcsec", pa.float64()),
        ("corner_ra", pa.list_(pa.float64())),  # pixel corners (1,1), (W,1), (W,H), (1,H)
        ("corner_dec", pa.list_(pa.float64())),
        ("rms_arcsec", pa.float64()),
        ("matched", pa.int64()),
        ("sip", pa.bool_()),
        ("grade", pa.string()),
        ("reasons", pa.list_(pa.string())),
        ("fwhm_arcsec", pa.float64()),
        ("zero_point", pa.float64()),
        ("limiting_mag", pa.float64()),
        ("sha256", pa.string()),
    ]
)


def mjd_of(date_obs: str | None) -> float | None:
    from datetime import UTC, datetime

    if not date_obs:
        return None
    try:
        stamp = datetime.fromisoformat(date_obs.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return _MJD_UNIX + stamp.timestamp() / 86400.0


def target_of(row: dict[str, Any]) -> str:
    """OBJECT when it is a name, else the frame's top folder."""
    return usable_name(row.get("object")) or PurePosixPath(row["path"]).parts[0]


@dataclass(frozen=True)
class Catalogue:
    source: LocalSource
    data: Path

    @property
    def path(self) -> Path:
        return self.source.output_dir(self.data, "catalogue") / "frames.parquet"

    def build(self) -> list[dict[str, Any]]:
        """Rebuild the catalogue from the index, the solutions and the grades, and keep it."""
        solver = LocalSolver(self.source, self.data, None, lambda _c: None)  # type: ignore[arg-type,return-value]
        grades = Grades(self.source, self.data).by_path()
        out = []
        for row in FrameIndex(self.source, self.data).rows():
            entry: dict[str, Any] = dict.fromkeys(SCHEMA.names)
            entry |= {k: row.get(k) for k in ("path", "kind", "object", "date_obs", "exposure_s", "stackcnt")}
            entry |= {"filter": row.get("filter"), "size": row.get("size"), "target": target_of(row)}
            entry["mjd"], entry["night"] = mjd_of(row["date_obs"]), _night(row["date_obs"])
            solution, refusal = solver.solution_path(row["path"]), solver.refusal_path(row["path"])
            if row["error"] or row["kind"] not in SOLVABLE_KINDS:
                entry["status"], entry["reason"] = NOT_SOLVED, row["error"] or f"a {row['kind']} frame"
            elif LocalSolver.current(solution, row):
                entry |= _sky(json.loads(solution.read_text("utf-8")))
                entry["status"] = SOLVED
            elif LocalSolver.current(refusal, row):
                doc = json.loads(refusal.read_text("utf-8"))
                entry |= {"status": REFUSED, "reason": doc["reason"], "sha256": (doc.get("file") or {}).get("sha256")}
            else:
                entry["status"] = TO_DO
            graded = grades.get(row["path"])
            # a grade belongs to the file that was solved or refused; a changed file waits for a new one
            if graded is not None and entry["status"] in (SOLVED, REFUSED):
                entry["grade"], entry["reasons"] = graded["grade"], list(graded["reasons"] or [])
            out.append(entry)
        out.sort(key=lambda r: r["path"])
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        pq.write_table(pa.Table.from_pylist(out, schema=SCHEMA), tmp, compression="zstd")
        tmp.replace(self.path)
        return out

    def rows(self) -> list[dict[str, Any]]:
        return pq.read_table(self.path).to_pylist() if self.path.exists() else self.build()


def _sky(solution: dict[str, Any]) -> dict[str, Any]:
    wcs = solution["wcs"]
    w, h = float(wcs["NAXIS1"]), float(wcs["NAXIS2"])
    corners = pixel_to_sky(wcs, np.array([1.0, w, w, 1.0]), np.array([1.0, 1.0, h, h]))
    centre = pixel_to_sky(wcs, np.array([(w + 1) / 2]), np.array([(h + 1) / 2]))
    ra_c, dec_c = float(centre[0][0]), float(centre[1][0])
    radius = max(_separation(ra_c, dec_c, float(r), float(d)) for r, d in zip(*corners, strict=True))
    quality = solution.get("quality") or {}
    return {
        "ra_deg": ra_c,
        "dec_deg": dec_c,
        "radius_deg": radius,
        "rotation_deg": solution.get("rotation_deg"),
        "scale_arcsec": solution.get("scale_arcsec"),
        "corner_ra": [float(v) for v in corners[0]],
        "corner_dec": [float(v) for v in corners[1]],
        "rms_arcsec": solution.get("rms_arcsec"),
        "matched": solution.get("matched"),
        "sip": "A_ORDER" in wcs,
        "fwhm_arcsec": quality.get("fwhm_arcsec"),
        "zero_point": quality.get("zero_point"),
        "limiting_mag": quality.get("limiting_mag"),
        "sha256": (solution.get("file") or {}).get("sha256"),
    }


def _separation(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    r1, d1, r2, d2 = map(math.radians, (ra1, dec1, ra2, dec2))
    c = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(r1 - r2)
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def distance_to_footprint(row: dict[str, Any], ra: float, dec: float) -> float:
    """Degrees from (ra, dec) to the frame's four-cornered footprint: 0 inside it."""
    if _separation(ra, dec, row["ra_deg"], row["dec_deg"]) > 90:
        return math.inf
    xi, eta = project(np.asarray(row["corner_ra"]), np.asarray(row["corner_dec"]), ra, dec)
    points = list(zip(xi.tolist(), eta.tolist(), strict=True))
    inside, best = False, math.inf
    for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1], strict=True):
        if (y1 > 0) != (y2 > 0) and x1 + (0 - y1) * (x2 - x1) / (y2 - y1) > 0:
            inside = not inside
        dx, dy = x2 - x1, y2 - y1
        t = max(0.0, min(1.0, -(x1 * dx + y1 * dy) / (dx * dx + dy * dy))) if dx or dy else 0.0
        best = min(best, math.hypot(x1 + t * dx, y1 + t * dy))
    return 0.0 if inside else best


def find(
    rows: Iterable[dict[str, Any]],
    ra: float,
    dec: float,
    radius_deg: float = 0.0,
    *,
    kind: str | None = None,
    grade: str | None = None,
    since: date | None = None,
    until: date | None = None,
) -> list[dict[str, Any]]:
    """Solved frames whose footprint reaches within ``radius_deg`` of (ra, dec), oldest first. ``grade``
    keeps that grade and better (good, usable, poor); ``since`` and ``until`` bound the night."""
    order = {GOOD: 0, USABLE: 1, POOR: 2}
    found = []
    for row in rows:
        if row["status"] != SOLVED or (kind and row["kind"] != kind):
            continue
        if grade and order.get(row["grade"] or GOOD, 0) > order[grade]:
            continue
        night = date.fromisoformat(row["night"]) if row["night"] else None
        if (since and (night is None or night < since)) or (until and (night is None or night > until)):
            continue
        distance = distance_to_footprint(row, ra, dec)
        if distance <= radius_deg:
            found.append({**row, "distance_deg": distance})
    return sorted(found, key=lambda r: (r["mjd"] if r["mjd"] is not None else math.inf, r["path"]))


def coverage(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each target: nights, frames by status and kind, exposure hours, the share graded good."""
    targets: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row["kind"] not in SOLVABLE_KINDS:
            continue
        t = targets.setdefault(
            row["target"],
            {"target": row["target"], "nights": set(), "lights": 0, "stacks": 0, "solved": 0, "refused": 0,
             "to_do": 0, "hours": 0.0, "good": 0, "first_night": None, "last_night": None},
        )  # fmt: skip
        t["lights" if row["kind"] == "light" else "stacks"] += 1
        t[row["status"]] = t.get(row["status"], 0) + 1
        if row["kind"] == "light":
            t["hours"] += (row["exposure_s"] or 0.0) / 3600
        if row["grade"] == GOOD:
            t["good"] += 1
        if row["night"]:
            t["nights"].add(row["night"])
    out = []
    for name in sorted(targets):
        t = targets[name]
        nights = sorted(t.pop("nights"))
        solved = t["solved"]
        out.append(
            {
                **t,
                "nights": len(nights),
                "first_night": nights[0] if nights else None,
                "last_night": nights[-1] if nights else None,
                "hours": round(t["hours"], 2),
                "good_share": round(t["good"] / solved, 3) if solved else None,
            }
        )
    return out


def sessions(rows: Iterable[dict[str, Any]]) -> list[Cone]:
    """One cone per folder of solved frames, holding all of them: the source's sky coverage."""
    by_folder: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if row["status"] == SOLVED:
            by_folder.setdefault(str(PurePosixPath(row["path"]).parent), []).append(row)
    cones = []
    for folder in sorted(by_folder):
        frames = by_folder[folder]
        # the mean direction of the frame centres
        ra, dec = np.radians([f["ra_deg"] for f in frames]), np.radians([f["dec_deg"] for f in frames])
        v = np.array([np.cos(dec) * np.cos(ra), np.cos(dec) * np.sin(ra), np.sin(dec)]).sum(axis=1)
        ra_c = math.degrees(math.atan2(v[1], v[0])) % 360.0
        dec_c = math.degrees(math.asin(v[2] / np.linalg.norm(v)))
        reach = max(_separation(ra_c, dec_c, f["ra_deg"], f["dec_deg"]) + f["radius_deg"] for f in frames)
        if reach <= MAX_CONE_DEG:
            cones.append(Cone(ra_c, dec_c, reach))
        else:  # a folder spread over the sky: a cone for each frame
            cones.extend(Cone(f["ra_deg"], f["dec_deg"], min(f["radius_deg"], MAX_CONE_DEG)) for f in frames)
    return cones


def descriptor(source: LocalSource, rows: list[dict[str, Any]], today: date) -> SourceCapabilityDescriptor:
    """The solved frames as the planner sees any archive: images of the kinds solved, covering each
    session's cone, between the first and last frame's dates, cut out per product. Only the frames the
    gateway offers count: dated, and not graded poor."""
    solved = [r for r in rows if r["status"] == SOLVED and r["mjd"] is not None and r["grade"] != POOR]
    epochs = [r["mjd"] for r in solved if r["mjd"] is not None]
    return SourceCapabilityDescriptor(
        source_id=source.source_id,
        title=f"Own frames in {source.path}",
        publisher="the person who took them",
        verified_on=today,
        origin="hand-written",
        product_types=frozenset({ProductType.IMAGE}) if solved else frozenset(),
        missions=tuple(sorted({r["kind"] for r in solved})),
        calibration_levels=frozenset({1 if r["kind"] == "light" else 3 for r in solved}),
        coverage=Coverage(
            all_sky=False,
            regions=tuple(sessions(solved)),
            t_min_mjd=min(epochs) if epochs else None,
            t_max_mjd=max(epochs) if epochs else None,
        ),
        operations=frozenset({Operation.CONE, Operation.CUTOUT, Operation.DOWNLOAD}),
        cutout_support={"image": "per-product"},
        rights=Rights("own observations", f"Own observations, {source.path} ({source.source_id})"),
        adapter=AdapterInfo("local-frames", "1.0.0", today, {"image": True}),
    )


__all__ = [
    "NOT_SOLVED",
    "REFUSED",
    "SOLVED",
    "TO_DO",
    "Catalogue",
    "LocalDiscovery",
    "coverage",
    "descriptor",
    "distance_to_footprint",
    "find",
    "sessions",
]


@dataclass
class LocalDiscovery:
    """CapabilityDiscoveryService over the registered archives and the person's local sources: a local
    source is described from its catalogue each time it is asked, so the planner sees the frames solved
    so far (#71)."""

    inner: Any
    sources: dict[str, tuple[LocalSource, Path]]
    today: Any = date.today
    #: a plan asks once per requirement; the catalogue is rebuilt at most this often
    fresh_seconds: float = 60.0
    _built: dict[str, tuple[float, SourceCapabilityDescriptor]] = field(default_factory=dict, repr=False)

    def _descriptor(self, source_id: str) -> SourceCapabilityDescriptor:
        now = time.monotonic()
        built = self._built.get(source_id)
        if built is None or now - built[0] > self.fresh_seconds:
            source, data = self.sources[source_id]
            built = (now, descriptor(source, Catalogue(source, data).build(), self.today()))
            self._built[source_id] = built
        return built[1]

    def describe(self, source_id: str) -> SourceCapabilityDescriptor:
        if source_id in self.sources:
            return self._descriptor(source_id)
        return self.inner.describe(source_id)  # type: ignore[no-any-return]

    def match(self, field: Any, need: Any) -> tuple[Any, ...]:
        from sdp.kernel.domain.integration import match_requirement, order_matches

        region = field.query_region()
        local = [match_requirement(self._descriptor(s), region, need) for s in sorted(self.sources)]
        return order_matches([*self.inner.match(field, need), *local])

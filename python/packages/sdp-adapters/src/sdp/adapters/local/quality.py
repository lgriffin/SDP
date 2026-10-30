"""How good each solved local frame is, measured from the solving pass, and its grade (#71).

Measures (all from the stars and background the solver already found, so no extra read of the file):

- ``fwhm_arcsec`` star width and ``ellipticity`` (1 - minor/major axis), medians over matched stars;
- ``background`` and ``background_rms`` per raw pixel, and ``gradient_percent`` across the frame;
- ``zero_point`` the magnitude of one count a second, against the matched stars' Gaia G (transparency);
- ``limiting_mag`` the faintest Gaia G a 5-sigma point source reaches (depth);
- ``detected`` and ``matched`` star counts;
- ``trails`` long thin bright features, such as satellites.

A frame is graded against the other frames of its session (its folder and kind), because a 10 s sub and a
three-hour stack share no fixed limits: ``poor`` when it is far outside the session's spread, ``usable``
when slightly outside it or crossed by a trail, ``good`` otherwise, always with the reasons in words.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from sdp.adapters.local.solver import Solution, Stars
from sdp.adapters.local.sources import LocalSource

GOOD, USABLE, POOR = "good", "usable", "poor"
#: fewer frames than this in a session are not compared with each other
MIN_SESSION = 5
#: a feature this many raw pixels long, and this many times longer than wide, is a trail
TRAIL_LENGTH_PX, TRAIL_ASPECT = 60.0, 6.0

#: (measure, how it fails, poor beyond, usable beyond, what it means); a ratio to the session median for
#: width, background and stars, a difference in magnitudes or ellipticity otherwise
LIMITS = (
    ("fwhm_arcsec", "ratio_above", 1.5, 1.25, "stars {:.2f}x wider than the session's (seeing, focus or dew)"),
    ("ellipticity", "above", 0.15, 0.08, "stars {:+.2f} more elongated than the session's (wind or tracking)"),
    ("zero_point", "below", 0.5, 0.2, "{:.2f} mag less light than the session's clearest (cloud or haze)"),
    ("background", "ratio_above", 2.0, 1.4, "sky {:.2f}x brighter than the session's (moon, dawn or a light)"),
    ("matched", "ratio_below", 0.5, 0.75, "{:.2f}x the session's matched stars"),
)

SCHEMA = pa.schema(
    [
        ("path", pa.string()),
        ("kind", pa.string()),
        ("session", pa.string()),
        ("date_obs", pa.string()),
        ("grade", pa.string()),
        ("reasons", pa.list_(pa.string())),
        ("fwhm_arcsec", pa.float64()),
        ("ellipticity", pa.float64()),
        ("background", pa.float64()),
        ("background_rms", pa.float64()),
        ("gradient_percent", pa.float64()),
        ("zero_point", pa.float64()),
        ("limiting_mag", pa.float64()),
        ("detected", pa.int64()),
        ("matched", pa.int64()),
        ("trails", pa.int64()),
    ]
)
MEASURES = [n for n in SCHEMA.names if n not in ("path", "kind", "session", "date_obs", "grade", "reasons")]


def _moments(image: np.ndarray, x: float, y: float, half: int) -> tuple[float, float] | None:
    """Width (sigma, pixels) and ellipticity of the star at (x, y) on a sky-subtracted image, from Gaussian-
    windowed second moments: for a Gaussian star of covariance C under a window of width w, the windowed
    covariance M obeys C^-1 = M^-1 - 1/w^2, so the window's narrowing is undone exactly while the noise
    far from the star counts for little."""
    ix, iy = round(x), round(y)
    if ix - half < 0 or iy - half < 0 or ix + half >= image.shape[1] or iy + half >= image.shape[0]:
        return None
    cut = image[iy - half : iy + half + 1, ix - half : ix + half + 1].astype(float)
    yy, xx = np.mgrid[-half : half + 1, -half : half + 1].astype(float)
    cx, cy, w = x - ix, y - iy, 2.0
    cov = np.eye(2) * w**2
    for _ in range(8):
        weight = cut * np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * w**2))
        total = weight.sum()
        if total <= 0:
            return None
        cx, cy = (weight * xx).sum() / total, (weight * yy).sum() / total
        dx, dy = xx - cx, yy - cy
        m = np.array([[(weight * dx * dx).sum(), (weight * dx * dy).sum()],
                      [(weight * dx * dy).sum(), (weight * dy * dy).sum()]]) / total  # fmt: skip
        try:
            inverse = np.linalg.inv(m) - np.eye(2) / w**2
            cov = np.linalg.inv(inverse)
        except np.linalg.LinAlgError:
            return None
        values = np.linalg.eigvalsh(cov)
        if values[0] <= 0 or not np.all(np.isfinite(values)):
            return None
        w = min(float(math.sqrt(values.mean())), half / 2)
    minor, major = np.linalg.eigvalsh(cov)
    return math.sqrt((major + minor) / 2), 1.0 - math.sqrt(minor / major)


def _trails(image: np.ndarray, rms: float, binning: int) -> int:
    from scipy import ndimage

    mask = image > 3 * rms
    # eight-connected, so a one-pixel-wide diagonal trail stays one feature
    labels, count = ndimage.label(mask, structure=np.ones((3, 3), dtype=bool))
    if not count:
        return 0
    sizes = ndimage.sum_labels(mask, labels, index=np.arange(1, count + 1))
    boxes = ndimage.find_objects(labels)
    trails = 0
    for label in np.flatnonzero(sizes >= TRAIL_LENGTH_PX / binning) + 1:
        box = boxes[label - 1]
        assert box is not None
        ys, xs = np.nonzero(labels[box] == label)
        cov = np.cov(np.vstack([xs, ys]).astype(float))
        values = np.linalg.eigvalsh(cov)
        if values[0] <= 0:
            values[0] = 1.0 / 12  # one pixel wide
        length = math.sqrt(12 * values[1]) * binning  # a uniform line of length L has variance L^2 / 12
        if length >= TRAIL_LENGTH_PX and math.sqrt(values[1] / values[0]) >= TRAIL_ASPECT:
            trails += 1
    return trails


def measure(stars: Stars, solution: Solution, scale_arcsec: float, exposure_s: float | None) -> dict[str, Any]:
    """A solved frame's measures; ``stars`` must come from ``detect``, which keeps the sky-subtracted image."""
    b = stars.binning
    image = stars.subtracted
    out: dict[str, Any] = {
        "detected": len(stars),
        "matched": solution.matched,
        "background": round(stars.sky["median"] / b**2, 3),
        "background_rms": round(stars.sky["rms"] / b, 3),
        "gradient_percent": round(stars.sky["gradient_percent"], 2),
    }
    if image is None or solution.matches is None:
        return out
    x, y, _, _, mag = solution.matches
    # back to the solving image's 0-based pixels
    sx, sy = (x - 1 - (b - 1) / 2) / b, (y - 1 - (b - 1) / 2) / b
    half = 8
    shapes = [_moments(image, float(px), float(py), half) for px, py in zip(sx, sy, strict=True)]
    good = [s for s in shapes if s is not None]
    if good:
        sigma, ellipticity = np.median(np.array(good), axis=0)
        out["fwhm_arcsec"] = round(2.3548 * float(sigma) * b * scale_arcsec, 3)
        out["ellipticity"] = round(float(ellipticity), 3)
    from photutils.aperture import CircularAperture, aperture_photometry

    radius = max(2.0, 2.0 * (float(np.median([s[0] for s in good])) * 2.3548 if good else 3.0))
    apertures = CircularAperture(np.column_stack([sx, sy]), r=radius)
    flux = np.asarray(aperture_photometry(image, apertures)["aperture_sum"], dtype=float)
    seconds = exposure_s if exposure_s and exposure_s > 0 else 1.0
    ok = flux > 0
    if ok.sum() >= 5:
        zp = float(np.median(mag[ok] + 2.5 * np.log10(flux[ok] / seconds)))
        out["zero_point"] = round(zp, 3)
        noise = stars.sky["rms"] * math.sqrt(math.pi * radius**2)
        if math.isfinite(noise) and noise > 0:  # a sky with no noise has no limit to measure
            out["limiting_mag"] = round(zp - 2.5 * math.log10(5 * noise / seconds), 2)
    out["trails"] = _trails(image, stars.sky["rms"], b)
    return out


def session_of(path: str, kind: str) -> str:
    return f"{PurePosixPath(path).parent}|{kind}"


def grade(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Grades for the frames of one session: each a dict with ``path``, ``measures`` and, for a refused
    frame, ``refused``."""
    solved = [f for f in frames if not f.get("refused")]
    medians: dict[str, float] = {}
    for name, how, *_ in LIMITS:
        values = [float(f["measures"][name]) for f in solved if f["measures"].get(name) is not None]
        if values:
            # transparency is judged against the session's clearest frames, the rest against its middle
            medians[name] = float(np.percentile(values, 90)) if how == "below" else float(np.median(values))
    compare = len(solved) >= MIN_SESSION
    graded = []
    for f in frames:
        if f.get("refused"):
            graded.append({**f, "grade": POOR, "reasons": [f"not solved: {f['refused']}"]})
            continue
        reasons: list[str] = []
        worst = GOOD
        for name, how, poor, usable, words in LIMITS if compare else ():
            value, middle = f["measures"].get(name), medians.get(name)
            if value is None or middle is None:
                continue
            if how.startswith("ratio"):
                if middle <= 0:
                    continue
                off = float(value) / middle
                bad = off > 1 if how == "ratio_above" else off < 1
                level = POOR if (off >= poor if how == "ratio_above" else off <= poor) else None
                level = level or (USABLE if (off >= usable if how == "ratio_above" else off <= usable) else None)
            else:
                off = float(value) - middle if how == "above" else middle - float(value)
                bad = off > 0
                level = POOR if off >= poor else (USABLE if off >= usable else None)
            if bad and level is not None:
                reasons.append(words.format(off))
                worst = POOR if POOR in (worst, level) else USABLE
        if f["measures"].get("trails"):
            reasons.append(f"{f['measures']['trails']} trail(s) across it (satellite or plane)")
            worst = worst if worst == POOR else USABLE
        if not compare:
            reasons.append(f"only {len(solved)} solved frame(s) in its session, so not compared")
        graded.append({**f, "grade": worst, "reasons": reasons})
    return graded


@dataclass(frozen=True)
class Grades:
    """``<data>/quality/<source>/grades.parquet``: one row per light or stack frame solved or refused."""

    source: LocalSource
    data: Path

    @property
    def path(self) -> Path:
        return self.source.output_dir(self.data, "quality") / "grades.parquet"

    def rows(self) -> list[dict[str, Any]]:
        return pq.read_table(self.path).to_pylist() if self.path.exists() else []

    def by_path(self) -> dict[str, dict[str, Any]]:
        return {r["path"]: r for r in self.rows()}

    def write(self, frames: Iterable[Mapping[str, Any]]) -> int:
        rows = []
        for f in frames:
            measures = f.get("measures") or {}
            rows.append(
                {
                    "path": f["path"],
                    "kind": f["kind"],
                    "session": session_of(f["path"], f["kind"]),
                    "date_obs": f.get("date_obs"),
                    "grade": f["grade"],
                    "reasons": list(f["reasons"]),
                    **{m: measures.get(m) for m in MEASURES},
                }
            )
        rows.sort(key=lambda r: r["path"])
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        pq.write_table(pa.Table.from_pylist(rows, schema=SCHEMA), tmp, compression="zstd")
        tmp.replace(self.path)
        return len(rows)


def _night(date_obs: str | None) -> str | None:
    """The evening a frame's night began on: its UTC date, less a day before noon."""
    from datetime import datetime, timedelta

    if not date_obs:
        return None
    try:
        when = datetime.fromisoformat(date_obs.replace("Z", ""))
    except ValueError:
        return None
    return (when - timedelta(hours=12)).date().isoformat()


def grade_source(source: LocalSource, data: Path, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Grade every solved or refused light and stack frame of an index, session by session, and keep the
    grades in the source's grades file."""
    import json

    from sdp.adapters.local.solving import SOLVABLE_KINDS, LocalSolver

    solver = LocalSolver(source, data, None, lambda _c: None)  # type: ignore[arg-type,return-value]
    sessions: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if row["error"] or row["kind"] not in SOLVABLE_KINDS:
            continue
        frame: dict[str, Any] = {"path": row["path"], "kind": row["kind"], "date_obs": row["date_obs"]}
        solution, refusal = solver.solution_path(row["path"]), solver.refusal_path(row["path"])
        if LocalSolver.current(solution, row):
            doc = json.loads(solution.read_text("utf-8"))
            frame["measures"] = {**(doc.get("quality") or {}), "matched": doc.get("matched")}
        elif LocalSolver.current(refusal, row):
            frame["measures"] = {}
            frame["refused"] = json.loads(refusal.read_text("utf-8"))["reason"]
        else:
            continue
        frame["object"] = row["object"]
        sessions.setdefault(session_of(row["path"], row["kind"]), []).append(frame)
    graded = [g for frames in sessions.values() for g in grade(frames)]
    Grades(source, data).write(graded)
    return graded


def nights(graded: list[dict[str, Any]], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each night and target: frames by grade, hours of exposure and the median star width."""
    from sdp.adapters.local.solving import usable_name

    exposure = {r["path"]: r["exposure_s"] or 0.0 for r in rows}
    summary: dict[tuple[str, str], dict[str, Any]] = {}
    for g in graded:
        target = usable_name(g.get("object")) or PurePosixPath(g["path"]).parts[0]
        key = (_night(g.get("date_obs")) or "undated", target)
        entry = summary.setdefault(
            key, {"night": key[0], "target": key[1], "frames": 0, GOOD: 0, USABLE: 0, POOR: 0, "hours": 0.0, "_w": []}
        )
        entry["frames"] += 1
        entry[g["grade"]] += 1
        entry["hours"] += exposure.get(g["path"], 0.0) / 3600
        if g["measures"].get("fwhm_arcsec") is not None:
            entry["_w"].append(g["measures"]["fwhm_arcsec"])
    out = []
    for key in sorted(summary):
        entry = summary[key]
        widths = entry.pop("_w")
        entry["hours"] = round(entry["hours"], 2)
        entry["median_fwhm_arcsec"] = round(float(np.median(widths)), 2) if widths else None
        out.append(entry)
    return out

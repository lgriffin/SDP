"""Solving a local source's frames in batches: rough centres, scales, Gaia references, and the solution
files under ``<data>/solutions/<source>/`` (#71). The frames themselves are only read."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from sdp.adapters.local.index import FrameIndex
from sdp.adapters.local.solver import (
    SOLVER_VERSION,
    Reference,
    SolverConfig,
    Unsolved,
    detect,
    read_frame,
    solve,
    solving_image,
)
from sdp.adapters.local.sources import LocalSource

SOLVED, REFUSED = ".wcs.json", ".unsolved.json"
SOLVABLE_KINDS = ("light", "stack")
_UUID = re.compile(r"^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$", re.IGNORECASE)

#: A rough centre for a name (SIMBAD through Sesame), or None when the name is unknown
NameResolver = Callable[[str], tuple[float, float] | None]
#: Gaia stars around a centre
ReferenceSource = Callable[[tuple[float, float]], Reference]


@dataclass(frozen=True)
class Outcome:
    path: str
    status: str  # solved, refused, skipped
    reason: str = ""
    matched: int = 0
    rms_arcsec: float | None = None
    seconds: float = 0.0


def usable_name(text: str | None) -> str | None:
    """An OBJECT worth looking up: not empty, not a UUID the app wrote in place of a name."""
    name = (text or "").strip()
    return name if name and not _UUID.match(name) else None


@dataclass
class LocalSolver:
    source: LocalSource
    data: Path
    resolve_name: NameResolver
    reference_for: ReferenceSource
    config: SolverConfig = field(default_factory=SolverConfig)
    _names: dict[str, tuple[float, float] | None] = field(default_factory=dict, repr=False)
    _references: dict[tuple[float, float], Reference] = field(default_factory=dict, repr=False)
    _triangles: dict[Any, Any] = field(default_factory=dict, repr=False)

    @property
    def root(self) -> Path:
        return self.source.output_dir(self.data, "solutions")

    def solution_path(self, relative: str) -> Path:
        return self.root / (relative + SOLVED)

    def refusal_path(self, relative: str) -> Path:
        return self.root / (relative + REFUSED)

    def select(
        self,
        rows: Iterable[dict[str, Any]],
        *,
        kind: str | None = None,
        target: str | None = None,
        limit: int | None = None,
        resolve: bool = False,
        retry_refused: bool = False,
    ) -> list[dict[str, Any]]:
        """Frames to solve, in path order: lights and stacks (or one kind), of one target if named,
        without a solution yet (all of them with ``resolve``), and not refused before unless retried."""
        chosen = []
        wanted = target.replace(" ", "").lower() if target else None
        for row in sorted(rows, key=lambda r: r["path"]):
            if row["error"] or row["kind"] not in ((kind,) if kind else SOLVABLE_KINDS):
                continue
            if wanted is not None:
                name = (row["object"] or "").replace(" ", "").lower()
                if name != wanted and wanted not in [
                    p.replace(" ", "").lower() for p in PurePosixPath(row["path"]).parts[:-1]
                ]:
                    continue
            if not resolve:
                if self.current(self.solution_path(row["path"]), row):
                    continue
                if not retry_refused and self.current(self.refusal_path(row["path"]), row):
                    continue
            chosen.append(row)
            if limit is not None and len(chosen) >= limit:
                break
        return chosen

    @staticmethod
    def current(path: Path, row: dict[str, Any]) -> bool:
        """A result written for the file as it is now indexed (same size and modification time)."""
        if not path.exists():
            return False
        return bool(
            json.loads(path.read_text("utf-8")).get("file") == {"size": row["size"], "mtime_ns": row["mtime_ns"]}
        )

    # -- what each frame needs ----------------------------------------------------------------------

    def _named(self, name: str) -> tuple[float, float] | None:
        if name not in self._names:
            self._names[name] = self.resolve_name(name)
        return self._names[name]

    def rough_centre(self, row: dict[str, Any], rows: list[dict[str, Any]]) -> tuple[tuple[float, float], str]:
        """From OBJECT; else the rough centre a solved frame in the same folder used (so its Gaia read is
        reused); else a neighbour's OBJECT."""
        name = usable_name(row["object"])
        if name is not None:
            found = self._named(name)
            if found is not None:
                return found, f"OBJECT {name}"
        folder = str(PurePosixPath(row["path"]).parent)
        siblings = [r for r in rows if str(PurePosixPath(r["path"]).parent) == folder and r["path"] != row["path"]]
        for sibling in siblings:
            solution = self.solution_path(sibling["path"])
            if solution.exists():
                centre = json.loads(solution.read_text("utf-8"))["rough_centre"]
                return (centre["ra_deg"], centre["dec_deg"]), f"solved neighbour {sibling['path']}"
        for sibling in siblings:
            other = usable_name(sibling["object"])
            if other is not None and (found := self._named(other)) is not None:
                return found, f"neighbour's OBJECT {other}"
        raise Unsolved("no rough centre: OBJECT is empty or unknown, and no frame in its folder has one")

    def scale(self, row: dict[str, Any]) -> tuple[float, str]:
        focal, pixel = row.get("focal_mm"), row.get("pixel_um")
        if focal and pixel:
            return 206.264806 * float(pixel) / float(focal), "FOCALLEN and XPIXSZ"
        if self.source.scale_arcsec:
            return float(self.source.scale_arcsec), "the source's --scale"
        raise Unsolved("no pixel scale: the header has no FOCALLEN and XPIXSZ; add the source with --scale")

    def reference(self, centre: tuple[float, float]) -> Reference:
        key = (round(centre[0], 6), round(centre[1], 6))
        if key not in self._references:
            self._references[key] = self.reference_for(key)
        return self._references[key]

    # -- one frame, then a batch ----------------------------------------------------------------------

    def solve_frame(self, row: dict[str, Any], rows: list[dict[str, Any]]) -> Outcome:
        started = time.monotonic()
        relative = row["path"]
        context: dict[str, Any] = {
            "frame": relative,
            "source": self.source.source_id,
            "solver": SOLVER_VERSION,
            "file": {"size": row["size"], "mtime_ns": row["mtime_ns"]},
        }
        try:
            centre, centre_from = self.rough_centre(row, rows)
            scale, scale_from = self.scale(row)
            context.update(rough_centre={"ra_deg": centre[0], "dec_deg": centre[1], "from": centre_from})
            context.update(scale={"arcsec": scale, "from": scale_from})
            reference = self.reference(centre)
            context["gaia"] = {"artifact_id": reference.artifact_id, "stars": int(reference.ra.size)}
            data, _ = read_frame(self.source.path / relative)
            image, binning = solving_image(data, row["bayer"] if row["kind"] != "stack" else None)
            stars = detect(image, self.config, binning)
            solution = solve(stars, reference, centre, scale, self.config, self._triangles)
            # binning drops an odd last row or column; the frame keeps it
            solution.wcs.update(NAXIS1=int(data.shape[-1]), NAXIS2=int(data.shape[-2]))
        except Unsolved as refused:
            self._write(self.refusal_path(relative), {**context, "reason": refused.reason, **refused.details})
            self.solution_path(relative).unlink(missing_ok=True)
            return Outcome(relative, "refused", refused.reason, seconds=time.monotonic() - started)
        except (OSError, ValueError) as exc:
            reason = f"the frame could not be read: {exc}"
            self._write(self.refusal_path(relative), {**context, "reason": reason})
            self.solution_path(relative).unlink(missing_ok=True)
            return Outcome(relative, "refused", reason, seconds=time.monotonic() - started)
        self._write(
            self.solution_path(relative), {**context, **solution.to_document(), "config": self.config.to_document()}
        )
        self.refusal_path(relative).unlink(missing_ok=True)
        return Outcome(
            relative,
            "solved",
            matched=solution.matched,
            rms_arcsec=solution.rms_arcsec,
            seconds=time.monotonic() - started,
        )

    def run(
        self,
        chosen: list[dict[str, Any]],
        rows: list[dict[str, Any]],
        progress: Callable[[Outcome], None] | None = None,
    ) -> list[Outcome]:
        outcomes = []
        for row in chosen:
            outcome = self.solve_frame(row, rows)
            outcomes.append(outcome)
            if progress is not None:
                progress(outcome)
        return outcomes

    def _write(self, path: Path, document: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        document = {**document, "written_at": datetime.now(UTC).isoformat(timespec="seconds")}
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", "utf-8")
        tmp.replace(path)

    def status(self, index: FrameIndex) -> dict[str, Any]:
        rows = [r for r in index.rows() if r["kind"] in SOLVABLE_KINDS and not r["error"]]
        solved = sum(1 for r in rows if self.solution_path(r["path"]).exists())
        refused: dict[str, int] = {}
        for r in rows:
            path = self.refusal_path(r["path"])
            if path.exists():
                reason = json.loads(path.read_text("utf-8"))["reason"]
                key = re.sub(r"\d+(\.\d+)?", "N", reason)
                refused[key] = refused.get(key, 0) + 1
        return {
            "frames": len(rows),
            "solved": solved,
            "refused": sum(refused.values()),
            "to_do": len(rows) - solved - sum(refused.values()),
            "refused_because": dict(sorted(refused.items(), key=lambda kv: -kv[1])),
            "solutions": str(self.root),
        }

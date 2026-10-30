"""Solving a local source's frames in batches: rough centres, scales, Gaia references, and the solution
files under ``<data>/solutions/<source>/`` (#71). The frames themselves are only read."""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from sdp.adapters.local.distortion import fit_distortion
from sdp.adapters.local.index import FrameIndex
from sdp.adapters.local.names import FROM_SIMBAD, NameBook
from sdp.adapters.local.quality import measure
from sdp.adapters.local.solver import (
    SOLVER_VERSION,
    Reference,
    Solution,
    SolverConfig,
    Stars,
    Unsolved,
    detect,
    read_frame,
    refine,
    solve,
    solving_image,
)
from sdp.adapters.local.sources import LocalSource
from sdp.adapters.local.wcs_file import WCS_SUFFIX, write_wcs_file

SOLVED, REFUSED = ".wcs.json", ".unsolved.json"
SOLVABLE_KINDS = ("light", "stack")
#: frames of a folder solved one after another, each starting from the last; folders are cut into runs of
#: this many so that several workers can share a long night's folder
CHAIN = 100
#: header pointings are rounded to this many degrees, so that the frames of one target share a Gaia read
POINTING_GRID_DEG = 0.1
#: a solved neighbour whose centre is further than this from a frame's header pointing is another target's
NEIGHBOUR_REACH_DEG = 3.0
_UUID = re.compile(r"^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$", re.IGNORECASE)

#: A rough centre for a name (SIMBAD through Sesame), or None when the name is unknown; it raises when
#: SIMBAD cannot be reached
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


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _separation(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    r1, d1, r2, d2 = map(math.radians, (ra1, dec1, ra2, dec2))
    c = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(r1 - r2)
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def usable_name(text: str | None) -> str | None:
    """An OBJECT worth looking up: not empty, not a UUID the app wrote in place of a name."""
    name = (text or "").strip()
    return name if name and not _UUID.match(name) else None


@dataclass
class LocalSolver:
    source: LocalSource
    data: Path
    #: None when SIMBAD is not available: only the names file and the headers give centres then
    resolve_name: NameResolver | None
    reference_for: ReferenceSource
    config: SolverConfig = field(default_factory=SolverConfig)
    names: NameBook | None = None
    _unknown: dict[str, str] = field(default_factory=dict, repr=False)
    _references: dict[tuple[float, float], Reference] = field(default_factory=dict, repr=False)
    _triangles: dict[Any, Any] = field(default_factory=dict, repr=False)
    #: the solutions of each folder's frames, read from disk once a folder is met and kept up to date
    _solved: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict, repr=False)
    _folders: dict[str, list[str]] = field(default_factory=dict, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def __post_init__(self) -> None:
        if self.names is None:
            self.names = NameBook(self.source, self.data)

    @property
    def root(self) -> Path:
        return self.source.output_dir(self.data, "solutions")

    def solution_path(self, relative: str) -> Path:
        return self.root / (relative + SOLVED)

    def refusal_path(self, relative: str) -> Path:
        return self.root / (relative + REFUSED)

    def wcs_path(self, relative: str) -> Path:
        """The solution as a header-only FITS file, for software other than SDP."""
        return self.root / (relative + WCS_SUFFIX)

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
        file = json.loads(path.read_text("utf-8")).get("file") or {}
        return bool((file.get("size"), file.get("mtime_ns")) == (row["size"], row["mtime_ns"]))

    # -- what each frame needs ----------------------------------------------------------------------

    def _first_fingerprint(self, relative: str) -> dict[str, Any] | None:
        """The first SHA-256 an earlier result of this frame recorded, so solving it again never replaces
        the fingerprint ``sdp local verify`` checks against."""
        for path in (self.solution_path(relative), self.refusal_path(relative)):
            if path.exists():
                doc = json.loads(path.read_text("utf-8"))
                file = doc.get("first_file") or doc.get("file") or {}
                if file.get("sha256"):
                    return {"since": doc.get("written_at", ""), **file}
        return None

    def _named(self, name: str) -> tuple[float, float] | None:
        """The names file's position; else SIMBAD's, once, kept in the names file for later runs."""
        assert self.names is not None
        with self._lock:
            known = self.names.get(name)
            if known is not None or name in self._unknown:
                return known
            if self.resolve_name is None:
                self._unknown[name] = "SIMBAD is not available"
                return None
            try:
                found = self.resolve_name(name)
            except Exception as exc:  # any failure to reach SIMBAD leaves the name unknown
                self._unknown[name] = f"SIMBAD could not be reached ({type(exc).__name__})"
                return None
            if found is None:
                self._unknown[name] = "SIMBAD does not know it"
                return None
            self.names.set(name, found[0], found[1], FROM_SIMBAD)
            return found

    def _folder(self, relative: str, rows: list[dict[str, Any]]) -> str:
        folder = str(PurePosixPath(relative).parent)
        with self._lock:
            if relative not in self._folders.get(folder, ()):
                # first met, or the index has grown since
                self._folders = {}
                for r in rows:
                    self._folders.setdefault(str(PurePosixPath(r["path"]).parent), []).append(r["path"])
                self._folders.setdefault(folder, [])
                if relative not in self._folders[folder]:
                    self._folders[folder].append(relative)
                for paths in self._folders.values():
                    paths.sort()
            if folder not in self._solved:
                found: dict[str, dict[str, Any]] = {}
                indexed = {r["path"]: r for r in rows}
                for path in self._folders[folder]:
                    solution = self.solution_path(path)
                    # a solution of a file that has changed since is no start for its neighbours
                    if path in indexed and self.current(solution, indexed[path]):
                        found[path] = json.loads(solution.read_text("utf-8"))
                self._solved[folder] = found
        return folder

    def neighbour(self, row: dict[str, Any], rows: list[dict[str, Any]]) -> tuple[str, dict[str, Any]] | None:
        """The solved frame of the same folder nearest this one in path order (the earlier on a tie), of the
        same target: a frame whose OBJECT names another target, or whose header points more than
        ``NEIGHBOUR_REACH_DEG`` away, is not used."""
        folder = self._folder(row["path"], rows)
        own = usable_name(row["object"])
        objects = {r["path"]: usable_name(r["object"]) for r in rows}
        with self._lock:
            solved = self._solved[folder]
            if not solved:
                return None
            paths = self._folders[folder]
            here = paths.index(row["path"])
            for step in range(1, len(paths)):
                for i in (here - step, here + step):
                    if not (0 <= i < len(paths) and paths[i] in solved):
                        continue
                    other = objects.get(paths[i])
                    if own is not None and other is not None and own != other:
                        continue
                    centre = solved[paths[i]].get("centre") or {}
                    if row.get("ra_deg") is not None and centre.get("ra_deg") is not None:
                        away = _separation(row["ra_deg"], row["dec_deg"], centre["ra_deg"], centre["dec_deg"])
                        if away > NEIGHBOUR_REACH_DEG:
                            continue
                    return paths[i], solved[paths[i]]
            return None

    def rough_centre(self, row: dict[str, Any], rows: list[dict[str, Any]]) -> tuple[tuple[float, float], str]:
        """From a solved frame in the same folder (so its Gaia read is reused); else OBJECT, from the names
        file or SIMBAD; else the header's pointing; else a neighbour's OBJECT."""
        near = self.neighbour(row, rows)
        if near is not None:
            centre = near[1]["rough_centre"]
            return (centre["ra_deg"], centre["dec_deg"]), f"solved neighbour {near[0]}"
        name = usable_name(row["object"])
        if name is not None:
            found = self._named(name)
            if found is not None:
                return found, f"OBJECT {name}"
        if row.get("ra_deg") is not None and row.get("dec_deg") is not None:
            grid = POINTING_GRID_DEG
            ra, dec = round(row["ra_deg"] / grid) * grid % 360.0, round(row["dec_deg"] / grid) * grid
            return (round(ra, 6), round(dec, 6)), "the header's RA and DEC"
        folder = str(PurePosixPath(row["path"]).parent)
        for sibling in rows:
            if str(PurePosixPath(sibling["path"]).parent) != folder or sibling["path"] == row["path"]:
                continue
            other = usable_name(sibling["object"])
            if other is not None and (found := self._named(other)) is not None:
                return found, f"neighbour's OBJECT {other}"
        why = ""
        if name is not None and name in self._unknown:
            why = (
                f"; {name} is not in {self.names.path if self.names else 'the names file'} and "
                f"{self._unknown[name]} (give it with --centre {name}=RA,DEC)"
            )
        raise Unsolved(f"no rough centre: OBJECT is empty or unknown, and no frame in its folder has one{why}")

    def scale(self, row: dict[str, Any]) -> tuple[float, str]:
        focal, pixel = row.get("focal_mm"), row.get("pixel_um")
        if focal and pixel:
            return 206.264806 * float(pixel) / float(focal), "FOCALLEN and XPIXSZ"
        if self.source.scale_arcsec:
            return float(self.source.scale_arcsec), "the source's --scale"
        raise Unsolved("no pixel scale: the header has no FOCALLEN and XPIXSZ; add the source with --scale")

    def reference(self, centre: tuple[float, float]) -> Reference:
        key = (round(centre[0], 6), round(centre[1], 6))
        with self._lock:
            if key not in self._references:
                self._references[key] = self.reference_for(key)
            return self._references[key]

    def _solve(
        self, stars: Stars, reference: Reference, centre: tuple[float, float], scale: float, near: Any
    ) -> tuple[Solution, dict[str, Any]]:
        """From the nearest solved frame of the folder when there is one, else by the triangle search; a
        start that fails the limits falls back to the search, never to a looser test."""
        if near is not None:
            try:
                solution = refine(stars, reference, near[1]["wcs"], self.config)
                return solution, {"method": "chained", "from": near[0]}
            except Unsolved as failed:
                solution = solve(stars, reference, centre, scale, self.config, self._triangles)
                return solution, {"method": "triangles", "chained_failed": {"from": near[0], "reason": failed.reason}}
        return solve(stars, reference, centre, scale, self.config, self._triangles), {"method": "triangles"}

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
        folder = self._folder(relative, rows)
        first = self._first_fingerprint(relative)
        if first is not None:
            context["first_file"] = first
        try:
            centre, centre_from = self.rough_centre(row, rows)
            scale, scale_from = self.scale(row)
            context.update(rough_centre={"ra_deg": centre[0], "dec_deg": centre[1], "from": centre_from})
            context.update(scale={"arcsec": scale, "from": scale_from})
            reference = self.reference(centre)
            context["gaia"] = {"artifact_id": reference.artifact_id, "stars": int(reference.ra.size)}
            # one read of the bytes both fingerprints the file and gives the pixels
            raw = (self.source.path / relative).read_bytes()
            context["file"]["sha256"] = hashlib.sha256(raw).hexdigest()
            context.setdefault("first_file", {**context["file"], "since": _now()})
            data, _ = read_frame(io.BytesIO(raw))
            del raw
            image, binning = solving_image(data, row["bayer"] if row["kind"] != "stack" else None)
            stars = detect(image, self.config, binning)
            solution, how = self._solve(stars, reference, centre, scale, self.neighbour(row, rows))
            context["start"] = how
            solution, context["distortion"] = fit_distortion(solution, stars, reference, self.config)
            try:
                context["quality"] = measure(stars, solution, scale, row.get("exposure_s"))
            except Exception as exc:  # a measure that fails never costs the frame its solution
                context["quality"] = {"error": f"{type(exc).__name__}: {exc}"}
            # binning drops an odd last row or column; the frame keeps it
            solution.wcs.update(NAXIS1=int(data.shape[-1]), NAXIS2=int(data.shape[-2]))
        except Unsolved as refused:
            return self._refuse(folder, relative, {**context, "reason": refused.reason, **refused.details}, started)
        except (OSError, ValueError) as exc:
            return self._refuse(folder, relative, {**context, "reason": f"the frame could not be read: {exc}"}, started)
        document = {**context, **solution.to_document(), "config": self.config.to_document()}
        self._write(self.solution_path(relative), document)
        try:
            write_wcs_file(self.wcs_path(relative), document)
        except (OSError, ValueError):  # the solution stands; sdp local sidecars writes the file again
            self.wcs_path(relative).unlink(missing_ok=True)
        self.refusal_path(relative).unlink(missing_ok=True)
        with self._lock:
            self._solved[folder][relative] = document
        return Outcome(
            relative,
            "solved",
            matched=solution.matched,
            rms_arcsec=solution.rms_arcsec,
            seconds=time.monotonic() - started,
        )

    def _refuse(self, folder: str, relative: str, document: dict[str, Any], started: float) -> Outcome:
        self._write(self.refusal_path(relative), document)
        self.solution_path(relative).unlink(missing_ok=True)
        self.wcs_path(relative).unlink(missing_ok=True)
        with self._lock:
            self._solved[folder].pop(relative, None)
        return Outcome(relative, "refused", document["reason"], seconds=time.monotonic() - started)

    def run(
        self,
        chosen: list[dict[str, Any]],
        rows: list[dict[str, Any]],
        progress: Callable[[Outcome], None] | None = None,
        workers: int = 1,
    ) -> list[Outcome]:
        """Each folder's frames in path order, each starting from its nearest solved neighbour. With several
        workers, runs of up to ``CHAIN`` frames of a folder go side by side; the results are the same frames,
        returned in path order."""
        chains: list[list[dict[str, Any]]] = []
        by_folder: dict[str, list[dict[str, Any]]] = {}
        for row in sorted(chosen, key=lambda r: r["path"]):
            by_folder.setdefault(str(PurePosixPath(row["path"]).parent), []).append(row)
        for frames in by_folder.values():
            chains += [frames[i : i + CHAIN] for i in range(0, len(frames), CHAIN)]
        tell = threading.Lock()

        def chain(frames: list[dict[str, Any]]) -> list[Outcome]:
            done = []
            for row in frames:
                outcome = self.solve_frame(row, rows)
                done.append(outcome)
                if progress is not None:
                    with tell:
                        progress(outcome)
            return done

        if workers <= 1:
            outcomes = [o for frames in chains for o in chain(frames)]
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                outcomes = [o for done in pool.map(chain, chains) for o in done]
        return sorted(outcomes, key=lambda o: o.path)

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

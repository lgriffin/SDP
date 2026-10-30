"""A solved local frame's sky solution in forms other software reads, without touching the frame (#71).

- ``<frame>.wcs`` beside ``<frame>.wcs.json``: a header-only FITS file holding the WCS keywords, the
  convention astrometry.net uses; astropy reads it with ``WCS(fits.getheader(path))``.
- ``sdp local export``: a copy of the frame with the WCS and SDP's provenance written into its header. The
  pixel bytes are copied as they are, never decoded and written again, and nothing is written in the source.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sdp.adapters.local.fits_header import header_size
from sdp.adapters.local.solving import SOLVED, LocalSolver
from sdp.adapters.local.sources import LocalSource, LocalSourceError, inside
from sdp.adapters.local.wcs_file import is_wcs, wcs_cards, write_wcs_file


def write_sidecars(solver: LocalSolver, paths: Iterable[str] | None = None) -> int:
    """A ``.wcs`` beside every solution (or those of ``paths``) that has none or an older one."""
    written = 0
    if paths is None:
        found = solver.root.rglob("*" + SOLVED) if solver.root.is_dir() else iter(())
        paths = [p.relative_to(solver.root).as_posix()[: -len(SOLVED)] for p in found]
    for relative in paths:
        solution_file = solver.solution_path(relative)
        target = solver.wcs_path(relative)
        if not solution_file.exists():
            target.unlink(missing_ok=True)
            continue
        if target.exists() and target.stat().st_mtime_ns >= solution_file.stat().st_mtime_ns:
            continue
        write_wcs_file(target, json.loads(solution_file.read_text("utf-8")))
        written += 1
    return written


def solved_copy(original: bytes, solution: Mapping[str, Any]) -> bytes:
    """The frame's bytes with its header's WCS replaced by the solution's; the pixel bytes are untouched."""
    from astropy.io import fits

    start = header_size(original)
    header = fits.Header.fromstring(original[:start].decode("ascii"))
    for key in [k for k in header if is_wcs(k)]:
        del header[key]
    header.add_history("WCS from SDP's plate solver; pixels copied unchanged")
    for key, value, comment in wcs_cards(solution):
        header[key] = (value, comment) if comment else value
    head: str = header.tostring(padding=True)
    return head.encode("ascii") + original[start:]


@dataclass
class ExportResult:
    written: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    bytes: int = 0


def export(
    source: LocalSource,
    solver: LocalSolver,
    rows: list[dict[str, Any]],
    out: Path,
    *,
    overwrite: bool = False,
    progress: Callable[[str], None] | None = None,
) -> ExportResult:
    """Solved copies of ``rows``' frames under ``out`` at their relative paths. ``out`` may not be inside
    the source; a frame whose file changed since it was solved is skipped."""
    if inside(out, source.path) or inside(source.path, out):
        raise LocalSourceError(f"{out} and the source {source.path} may not hold each other; export elsewhere")
    result = ExportResult()
    for row in rows:
        relative = row["path"]
        solution_file = solver.solution_path(relative)
        if not LocalSolver.current(solution_file, row):
            result.skipped.append(relative)
            continue
        target = out / relative
        if target.exists() and not overwrite:
            result.skipped.append(relative)
            continue
        solution = json.loads(solution_file.read_text("utf-8"))
        original = (source.path / relative).read_bytes()
        recorded = (solution.get("file") or {}).get("sha256")
        if recorded and hashlib.sha256(original).hexdigest() != recorded:
            result.skipped.append(relative)  # the bytes are not the ones that were solved
            continue
        body = solved_copy(original, solution)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_bytes(body)
        tmp.replace(target)
        result.written.append(relative)
        result.bytes += len(body)
        if progress is not None:
            progress(relative)
    return result

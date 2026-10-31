"""The frame index of a local source: one row per FITS file, from its header alone (#71).

It is kept as ``<data>/index/<source>/frames.parquet``. A rerun reads only files that are new or whose
size or modification time changed; the source folder is opened for reading only.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from sdp.adapters.local.fits_header import NotFits, read_header_file
from sdp.adapters.local.sources import LocalSource, LocalSourceError

SUFFIXES = (".fits", ".fit", ".fts")
PROGRESS_EVERY = 500
KINDS = ("light", "dark", "flat", "bias", "stack")
FOLDER_KINDS = {
    "dark": "dark",
    "darks": "dark",
    "flat": "flat",
    "flats": "flat",
    "bias": "bias",
    "biases": "bias",
    "offset": "bias",
    "offsets": "bias",
}

SCHEMA = pa.schema(
    [
        ("path", pa.string()),  # relative to the source, with / separators
        ("size", pa.int64()),
        ("mtime_ns", pa.int64()),
        ("kind", pa.string()),
        ("date_obs", pa.string()),
        ("exposure_s", pa.float64()),
        ("filter", pa.string()),
        ("object", pa.string()),
        ("gain", pa.float64()),
        ("naxis1", pa.int64()),
        ("naxis2", pa.int64()),
        ("naxis3", pa.int64()),
        ("bitpix", pa.int64()),
        ("stackcnt", pa.int64()),
        ("bayer", pa.string()),
        ("focal_mm", pa.float64()),
        ("pixel_um", pa.float64()),
        ("has_wcs", pa.bool_()),
        ("ra_deg", pa.float64()),  # where the header says the telescope pointed, if it says
        ("dec_deg", pa.float64()),
        ("error", pa.string()),
    ]
)


def _number(header: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = header.get(key)
        if isinstance(value, int | float) and not isinstance(value, bool):
            return float(value)
    return None


def _integer(header: dict[str, Any], key: str) -> int | None:
    value = header.get(key)
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


def _text(header: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = header.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _sexagesimal(text: str | None, unit: float) -> float | None:
    """``"00 42 44.3"`` or ``"+41:16:09"`` as a number of ``unit`` (15 for hours of RA, 1 for degrees)."""
    parts = (text or "").replace(":", " ").split()
    try:
        values = [float(p) for p in parts]
    except ValueError:
        return None
    if not 1 <= len(values) <= 3:
        return None
    sign = -1.0 if parts[0].startswith("-") else 1.0
    magnitude = sum(abs(v) / 60.0**i for i, v in enumerate(values))
    return sign * magnitude * unit


def pointing(header: dict[str, Any]) -> tuple[float | None, float | None]:
    """The header's pointing in degrees: RA and DEC as numbers (degrees), else OBJCTRA and OBJCTDEC
    (hours and degrees, sexagesimal); None for each unless both are there and on the sky."""
    ra, dec = _number(header, "RA"), _number(header, "DEC")
    if ra is None or dec is None:
        ra, dec = _sexagesimal(_text(header, "OBJCTRA"), 15.0), _sexagesimal(_text(header, "OBJCTDEC"), 1.0)
    if ra is None or dec is None or not (0 <= ra < 360 and -90 <= dec <= 90):
        return None, None
    return ra, dec


def folder_kind(relative: PurePosixPath | str) -> str | None:
    """The calibration kind a folder holding the frame is named for (``darks``, ``biases``, ...), if any."""
    for folder in PurePosixPath(relative).parent.parts:
        if folder.lower() in FOLDER_KINDS:
            return FOLDER_KINDS[folder.lower()]
    return None


def frame_kind(header: dict[str, Any], relative: PurePosixPath) -> str:
    """``IMAGETYP`` when present, else a calibration folder's name, else a stack (``STACKCNT``, or a
    floating-point colour cube), else a light frame. A calibration folder's name wins over an
    ``IMAGETYP`` of LIGHT: the Vespera writes LIGHT on its darks and biases too (#143)."""
    imagetyp = (_text(header, "IMAGETYP", "FRAME") or "").lower()
    for kind in ("dark", "flat", "bias", "light"):
        if kind in imagetyp:
            return (folder_kind(relative) or kind) if kind == "light" else kind
    if "offset" in imagetyp:
        return "bias"
    if (by_folder := folder_kind(relative)) is not None:
        return by_folder
    cube = header.get("NAXIS") == 3 and isinstance(header.get("BITPIX"), int) and int(header["BITPIX"]) < 0
    if header.get("STACKCNT") is not None or cube:
        return "stack"
    return "light"


def frame_row(relative: PurePosixPath, size: int, mtime_ns: int, header: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": str(relative),
        "size": size,
        "mtime_ns": mtime_ns,
        "kind": frame_kind(header, relative),
        "date_obs": _text(header, "DATE-OBS", "DATE_OBS"),
        "exposure_s": _number(header, "EXPOSURE", "EXPTIME"),
        "filter": _text(header, "FILTER"),
        "object": _text(header, "OBJECT"),
        "gain": _number(header, "GAIN"),
        "naxis1": _integer(header, "NAXIS1"),
        "naxis2": _integer(header, "NAXIS2"),
        "naxis3": _integer(header, "NAXIS3"),
        "bitpix": _integer(header, "BITPIX"),
        "stackcnt": _integer(header, "STACKCNT"),
        "bayer": _text(header, "BAYERPAT", "COLORTYP"),
        "focal_mm": _number(header, "FOCALLEN"),
        "pixel_um": _number(header, "XPIXSZ", "PIXSIZE1"),
        "has_wcs": "CTYPE1" in header and "CRVAL1" in header,
        "ra_deg": pointing(header)[0],
        "dec_deg": pointing(header)[1],
        "error": None,
    }


def _unreadable(relative: PurePosixPath, size: int, mtime_ns: int, error: str) -> dict[str, Any]:
    return {**dict.fromkeys(SCHEMA.names), "path": str(relative), "size": size, "mtime_ns": mtime_ns, "error": error}


def walk_frames(root: Path) -> Iterator[tuple[Path, PurePosixPath]]:
    """Every FITS file under ``root`` in name order, with its path relative to the source."""

    def unreadable(exc: OSError) -> None:
        # a folder that cannot be listed would read as frames removed, so the scan stops instead
        raise LocalSourceError(f"{exc.filename} could not be listed ({exc.strerror}); the index is unchanged")

    for folder, dirs, files in os.walk(root, onerror=unreadable):
        dirs.sort()
        for name in sorted(files):
            if name.lower().endswith(SUFFIXES) and not name.startswith("."):
                path = Path(folder) / name
                yield path, PurePosixPath(path.relative_to(root).as_posix())


@dataclass(frozen=True)
class IndexResult:
    files: int
    read: int
    reused: int
    removed: int
    unreadable: int
    path: Path


@dataclass(frozen=True)
class FrameIndex:
    source: LocalSource
    data: Path

    @property
    def path(self) -> Path:
        return self.source.output_dir(self.data, "index") / "frames.parquet"

    def rows(self) -> list[dict[str, Any]]:
        return pq.read_table(self.path).to_pylist() if self.path.exists() else []

    def table(self) -> pa.Table:
        return pq.read_table(self.path) if self.path.exists() else SCHEMA.empty_table()

    def update(self, progress: Callable[[int, int], None] | None = None) -> IndexResult:
        """Read the header of every new or changed file; keep the rows of unchanged ones."""
        if not self.source.path.is_dir():
            raise LocalSourceError(f"{self.source.path} is not there (is the drive connected?)")
        known = {r["path"]: r for r in self.rows()}
        rows: list[dict[str, Any]] = []
        read = reused = 0
        for count, (path, relative) in enumerate(walk_frames(self.source.path), start=1):
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue  # removed while the scan ran
            previous = known.get(str(relative))
            if (
                previous is not None
                and not previous["error"]
                and set(SCHEMA.names) <= previous.keys()  # an index from before a column was added
                and (previous["size"], previous["mtime_ns"]) == (stat.st_size, stat.st_mtime_ns)
            ):
                if previous["kind"] == "light" and (by_folder := folder_kind(relative)) is not None:
                    # indexed as a light before a calibration folder's name won over IMAGETYP (#143)
                    previous = {**previous, "kind": by_folder}
                rows.append(previous)
                reused += 1
            else:
                try:
                    rows.append(frame_row(relative, stat.st_size, stat.st_mtime_ns, read_header_file(path)))
                except (NotFits, OSError) as exc:
                    rows.append(_unreadable(relative, stat.st_size, stat.st_mtime_ns, str(exc)))
                read += 1
            if progress is not None and count % PROGRESS_EVERY == 0:
                progress(count, read)
        seen = {r["path"] for r in rows}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        pq.write_table(pa.Table.from_pylist(rows, schema=SCHEMA), tmp, compression="zstd")
        tmp.replace(self.path)
        return IndexResult(
            files=len(rows),
            read=read,
            reused=reused,
            removed=len(set(known) - seen),
            unreadable=sum(1 for r in rows if r["error"]),
            path=self.path,
        )

    def summary(self) -> dict[str, Any]:
        """Counts by kind and by target, with exposure hours, for ``sdp local status``."""
        rows = self.rows()
        by_kind: dict[str, int] = {}
        targets: dict[str, dict[str, float]] = {}
        for r in rows:
            kind = r["kind"] or "unreadable"
            by_kind[kind] = by_kind.get(kind, 0) + 1
            if r["kind"] in ("light", "stack"):
                t = targets.setdefault(r["object"] or "(no OBJECT)", {"frames": 0, "hours": 0.0})
                t["frames"] += 1
                t["hours"] += (r["exposure_s"] or 0.0) / 3600.0
        return {
            "source": self.source.source_id,
            "path": str(self.source.path),
            "index": str(self.path),
            "frames": len(rows),
            "bytes": sum(r["size"] or 0 for r in rows),
            "by_kind": dict(sorted(by_kind.items())),
            "with_wcs": sum(1 for r in rows if r["has_wcs"]),
            "targets": {
                k: {"frames": int(v["frames"]), "hours": round(v["hours"], 2)} for k, v in sorted(targets.items())
            },
        }

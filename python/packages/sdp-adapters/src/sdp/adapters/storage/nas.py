"""NasArtifactStore: content-addressed, write-once files on the NAS (ADR-008, NFR-003).

Layout under ``root``::

    objects/sha256/ab/cdef…            the bytes, read-only once written
    objects/sha256/ab/cdef….media      the media type
    paths/<logical path>               a small file holding the artifact id it names

Writes go to a temporary file in the same directory, are fsynced, then renamed into place, so a
crash never leaves a partial object under its final name. Every read re-hashes the bytes.

Two writers may store the same object at once (bulk workers whose files hold the same bytes, #171).
POSIX lets the second rename replace the first; Windows refuses to rename onto a read-only file with
"Access is denied". The object is content-addressed, so when its final name already exists the second
writer has nothing left to do and drops its temporary file. A rename refused for any other reason
(an antivirus or indexer briefly holding the new file open) is retried with a short backoff.
"""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from sdp.kernel.domain.canonical import sha256_id
from sdp.kernel.domain.errors import ImmutableArtifact, IntegrityError, InvariantViolation
from sdp.kernel.domain.ids import ArtifactId

_RAW_PREFIX = "raw/"
# Windows cannot open a directory as a file (PermissionError), and NTFS journals the rename itself.
_CAN_FSYNC_DIRECTORIES = os.name != "nt"
# Windows refuses these in a file name (a task id is "run:<test>@<version>"), and reads "x:" as a drive.
# Logical paths are only ever looked up by name, so each segment is %-escaped on the way to disk there;
# elsewhere a segment is its own file name, as stores written before this expect.
_ESCAPED = frozenset('<>:"|?*\\%') if os.name == "nt" else frozenset()
# A rename Windows refuses while something else holds the file is tried this often, waiting
# _RENAME_BACKOFF_S, then twice that, and so on (about 1.5 s in all) before the error is raised.
_RENAME_ATTEMPTS = 6
_RENAME_BACKOFF_S = 0.05


def _segment(name: str) -> str:
    return "".join(f"%{ord(c):02X}" if c in _ESCAPED or ord(c) < 32 else c for c in name)


def _atomic_write(path: Path, data: bytes, *, read_only: bool) -> None:
    """Write ``data`` to ``path`` through a fsynced temporary file. ``read_only`` marks a write-once,
    content-addressed file: if another writer puts it in place first, this write is already done."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if read_only:
            os.chmod(tmp, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        if not _rename(tmp, path, write_once=read_only):
            _discard(tmp)
            return
    except BaseException:
        _discard(tmp)
        raise
    _fsync_directory(path.parent)


def _rename(tmp: str, path: Path, *, write_once: bool) -> bool:
    """Move ``tmp`` onto ``path``. False when a write-once ``path`` was already in place, so ``tmp``
    is not needed; a refusal with nothing in place is retried, then raised."""
    for attempt in range(_RENAME_ATTEMPTS):
        try:
            os.replace(tmp, path)
            return True
        except PermissionError:
            if write_once and path.exists():
                return False
            if attempt == _RENAME_ATTEMPTS - 1:
                raise
            time.sleep(_RENAME_BACKOFF_S * 2**attempt)
    raise AssertionError("unreachable")  # pragma: no cover


def _discard(tmp: str) -> None:
    """Remove a temporary file, read-only or not (Windows will not delete a read-only file). Best
    effort: a leftover ``.tmp-*`` is harmless (``usage`` skips it), while failing here would hide the
    write's own error or fail a write another writer already completed."""
    try:
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
        os.unlink(tmp)
    except OSError:
        pass


def _fsync_directory(directory: Path) -> None:
    if not _CAN_FSYNC_DIRECTORIES:
        return
    dir_fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


@dataclass(frozen=True)
class NasArtifactStore:
    root: Path

    def __post_init__(self) -> None:
        (self.root / "objects" / "sha256").mkdir(parents=True, exist_ok=True)
        (self.root / "paths").mkdir(parents=True, exist_ok=True)

    def _object(self, artifact_id: ArtifactId) -> Path:
        digest = artifact_id.value.removeprefix("sha256:")
        return self.root / "objects" / "sha256" / digest[:2] / digest[2:]

    def _logical(self, logical_path: str) -> Path:
        parts = PurePosixPath(logical_path).parts
        if not parts or PurePosixPath(logical_path).is_absolute() or ".." in parts:
            raise InvariantViolation(f"not a relative logical path: {logical_path!r}")
        return self.root.joinpath("paths", *(_segment(part) for part in parts))

    def put(self, data: bytes, media_type: str, *, logical_path: str | None = None) -> ArtifactId:
        artifact = ArtifactId(sha256_id(data))
        target = self._object(artifact)
        if not target.exists():
            _atomic_write(target, data, read_only=True)
        media = target.with_name(target.name + ".media")
        if not media.exists():  # also heals an object whose write stopped before its media type
            _atomic_write(media, media_type.encode(), read_only=True)
        if logical_path is not None:
            self.link(logical_path, artifact)
        return artifact

    def get(self, artifact_id: ArtifactId) -> bytes:
        target = self._object(artifact_id)
        try:
            data = target.read_bytes()
        except FileNotFoundError:
            raise KeyError(f"no artifact {artifact_id}") from None
        if sha256_id(data) != artifact_id.value:
            raise IntegrityError(f"artifact {artifact_id} failed SHA-256 verification on read ({target})")
        return data

    def exists(self, artifact_id: ArtifactId) -> bool:
        return self._object(artifact_id).exists()

    def media_type(self, artifact_id: ArtifactId) -> str:
        target = self._object(artifact_id)
        try:
            return target.with_name(target.name + ".media").read_text()
        except FileNotFoundError:
            raise KeyError(f"no artifact {artifact_id}") from None

    def resolve(self, logical_path: str) -> ArtifactId | None:
        try:
            value = self._logical(logical_path).read_text().strip()
        except (FileNotFoundError, IsADirectoryError):
            return None
        return ArtifactId(value)

    def link(self, logical_path: str, artifact_id: ArtifactId) -> None:
        if not self.exists(artifact_id):
            raise KeyError(f"cannot link {logical_path} to missing {artifact_id}")
        current = self.resolve(logical_path)
        if logical_path.startswith(_RAW_PREFIX) and current is not None and current != artifact_id:
            raise ImmutableArtifact(f"{logical_path} already names a different raw artifact (KRN-014)")
        if current == artifact_id:
            return
        _atomic_write(self._logical(logical_path), artifact_id.value.encode(), read_only=False)

    def free_bytes(self) -> int | None:
        return shutil.disk_usage(self.root).free

    def usage(self) -> dict[str, Any]:
        """Objects and bytes stored, in all and per media type, and the free space (#60). It walks the
        object folders and reads no artifact, so it is quick even for a large store."""
        by_media: dict[str, dict[str, int]] = {}
        for shard in (self.root / "objects" / "sha256").iterdir():
            if not shard.is_dir():
                continue
            for entry in shard.iterdir():
                if entry.name.endswith(".media") or entry.name.startswith(".tmp-"):
                    continue
                media = entry.with_name(entry.name + ".media")
                kind = media.read_text() if media.exists() else "unknown"
                counts = by_media.setdefault(kind, {"objects": 0, "bytes": 0})
                counts["objects"] += 1
                counts["bytes"] += entry.stat().st_size
        return {
            "root": str(self.root),
            "objects": sum(c["objects"] for c in by_media.values()),
            "bytes": sum(c["bytes"] for c in by_media.values()),
            "by_media_type": dict(sorted(by_media.items())),
            "free_bytes": self.free_bytes(),
        }

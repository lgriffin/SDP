"""Production determinism seams and the environment probe (§6, KRN-004, KRN-013)."""

from __future__ import annotations

import hashlib
import importlib.metadata
import os
import platform
import secrets
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sdp.kernel.domain.canonical import sha256_id
from sdp.kernel.domain.experiment import EnvironmentRecord
from sdp.kernel.science.plan import Backend

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass
class UlidGenerator:
    """ULIDs: 48-bit millisecond time + 80 random bits, Crockford base32, monotonic within a millisecond."""

    _last_ms: int = -1
    _last_random: int = 0

    def _ulid(self) -> str:
        ms = time.time_ns() // 1_000_000
        if ms == self._last_ms:
            self._last_random += 1
        else:
            self._last_ms, self._last_random = ms, secrets.randbits(80)
        value = (ms << 80) | (self._last_random & ((1 << 80) - 1))
        return "".join(_CROCKFORD[(value >> (5 * i)) & 31] for i in reversed(range(26)))

    def new(self, prefix: str) -> str:
        return f"{prefix}-{self._ulid()}"


class Sha256Hasher:
    def sha256(self, data: bytes) -> str:
        return sha256_id(data)


def run_command(*args: str, cwd: Path | None = None) -> str | None:
    if shutil.which(args[0]) is None:
        return None
    try:
        out = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=10, check=True)
    except (subprocess.SubprocessError, OSError):
        return None
    return out.stdout.strip() or None


@dataclass
class SystemEnvironmentProbe:
    """Code revision from Git, lock digest from uv.lock, image digest from the container, GPU versions
    from nvidia-smi. Anything unknowable is recorded as such, never guessed."""

    repo: Path
    lock_file: Path | None = None
    constants_version: str | None = None
    packages: tuple[str, ...] = ("sdp-kernel", "sdp-adapters", "numpy", "astropy", "scipy", "pyarrow")
    agreement_test_id: str | None = field(default_factory=lambda: os.environ.get("SDP_GPU_AGREEMENT_TEST_ID"))

    def _lock_digest(self) -> str:
        lock = self.lock_file or self.repo / "python" / "uv.lock"
        if lock.exists():
            return "sha256:" + hashlib.sha256(lock.read_bytes()).hexdigest()
        return "sha256:" + "0" * 64

    def _versions(self) -> dict[str, str]:
        out = {}
        for name in self.packages:
            try:
                out[name] = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                continue
        return out

    def record(self, backend: Backend) -> EnvironmentRecord:
        revision = (
            os.environ.get("SDP_CODE_REVISION") or run_command("git", "rev-parse", "HEAD", cwd=self.repo) or "unknown"
        )
        dirty = run_command("git", "status", "--porcelain", "--untracked-files=no", cwd=self.repo)
        cuda = backend is Backend.CUDA
        driver = cuda_version = None
        if cuda:
            query = run_command("nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader")
            driver = query.splitlines()[0] if query else None
            smi = run_command("nvidia-smi") or ""
            marker = "CUDA Version:"
            cuda_version = smi.split(marker, 1)[1].split()[0] if marker in smi else None
        return EnvironmentRecord(
            code_revision=revision + ("+dirty" if dirty else ""),
            lock_digest=self._lock_digest(),
            backend=backend.value,
            python_version=platform.python_version(),
            platform=f"{platform.system().lower()}-{platform.machine()}",
            image_digest=os.environ.get("SDP_IMAGE_DIGEST"),
            driver_version=driver,
            cuda_version=cuda_version,
            agreement_test_id=self.agreement_test_id if cuda else None,
            constants_version=self.constants_version,
            packages=self._versions(),
        )

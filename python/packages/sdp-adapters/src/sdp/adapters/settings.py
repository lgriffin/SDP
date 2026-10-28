"""Settings from the environment. Credentials come only from here, never from Git (NFR-009)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


class SettingsError(ValueError):
    pass


def _flag(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    database_url: str
    artifact_root: Path
    ledger_dir: Path
    repo_root: Path
    signing_key: Path | None = None
    signer: str | None = None
    allowed_signers: Path | None = None
    ledger_commit: bool = False
    require_signatures: bool = True
    metrics_dir: Path | None = None
    threads: int = 1
    min_free_bytes: int = 50 * 1024**3
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        missing = [k for k in ("SDP_DATABASE_URL", "SDP_ARTIFACT_ROOT") if not env.get(k)]
        if missing:
            raise SettingsError(f"missing environment variables: {', '.join(missing)}")
        repo = Path(env.get("SDP_REPO_ROOT", ".")).resolve()

        def path(key: str) -> Path | None:
            return Path(env[key]) if env.get(key) else None

        return cls(
            database_url=env["SDP_DATABASE_URL"],
            artifact_root=Path(env["SDP_ARTIFACT_ROOT"]),
            ledger_dir=path("SDP_LEDGER_DIR") or repo / "ledger",
            repo_root=repo,
            signing_key=path("SDP_SIGNING_KEY"),
            signer=env.get("SDP_SIGNER") or None,
            allowed_signers=path("SDP_ALLOWED_SIGNERS"),
            ledger_commit=_flag(env.get("SDP_LEDGER_COMMIT")),
            require_signatures=not _flag(env.get("SDP_ALLOW_UNSIGNED")),
            metrics_dir=path("SDP_METRICS_DIR"),
            threads=int(env.get("SDP_THREADS", "1")),
            min_free_bytes=int(env.get("SDP_MIN_FREE_BYTES", str(50 * 1024**3))),
            log_level=env.get("SDP_LOG_LEVEL", "INFO"),
        )

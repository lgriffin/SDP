"""Settings from the environment. Credentials come only from here, never from Git (NFR-009)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path


class SettingsError(ValueError):
    pass


def _flag(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    database_url: str = field(repr=False)
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
    #: live (the archives, politely), replay (committed cassettes only) or record (live, writing cassettes)
    archive_mode: str = "live"
    cassette_dir: Path | None = None
    #: Zenodo: the token never appears in a repr, a log or a manifest; the sandbox is the default service
    zenodo_token: str | None = field(default=None, repr=False)
    zenodo_url: str = "https://sandbox.zenodo.org"
    zenodo_creators: str = ""
    zenodo_community: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        database_url = secret(env, "SDP_DATABASE_URL")
        missing = [
            k
            for k, v in (("SDP_DATABASE_URL", database_url), ("SDP_ARTIFACT_ROOT", env.get("SDP_ARTIFACT_ROOT")))
            if not v
        ]
        if missing:
            raise SettingsError(f"missing environment variables: {', '.join(missing)}")
        repo = Path(env.get("SDP_REPO_ROOT", ".")).resolve()

        def path(key: str) -> Path | None:
            return Path(env[key]) if env.get(key) else None

        return cls(
            database_url=str(database_url),
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
            archive_mode=archive_mode(env.get("SDP_ARCHIVE_MODE", "live")),
            cassette_dir=path("SDP_CASSETTE_DIR") or repo / "python" / "tests" / "cassettes",
            zenodo_token=secret(env, "SDP_ZENODO_TOKEN"),
            zenodo_url=env.get("SDP_ZENODO_URL", "https://sandbox.zenodo.org"),
            zenodo_creators=env.get("SDP_ZENODO_CREATORS", ""),
            zenodo_community=env.get("SDP_ZENODO_COMMUNITY") or None,
        )


def secret(env: Mapping[str, str], key: str) -> str | None:
    """A credential from ``KEY`` or from the file named by ``KEY_FILE`` (a Podman secret mount)."""
    if env.get(key):
        return env[key].strip()
    if env.get(f"{key}_FILE"):
        return Path(env[f"{key}_FILE"]).read_text("utf-8").strip() or None
    return None


ARCHIVE_MODES = ("live", "replay", "record")


def archive_mode(value: str) -> str:
    if value not in ARCHIVE_MODES:
        raise SettingsError(f"SDP_ARCHIVE_MODE must be one of {', '.join(ARCHIVE_MODES)}, got {value!r}")
    return value

"""Settings from the environment. Credentials come only from here, never from Git (NFR-009)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

from sdp.kernel.application.planning import DEFAULT_BUDGET
from sdp.kernel.domain.integration import AcquisitionBudget


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
    #: where Gaia fields are read: auto (the bulk copy once complete, else the archive), tap or bulk (#72)
    gaia_source: str = "auto"
    #: Zenodo: the token never appears in a repr, a log or a manifest; the sandbox is the default service
    zenodo_token: str | None = field(default=None, repr=False)
    zenodo_url: str = "https://sandbox.zenodo.org"
    zenodo_creators: str = ""
    zenodo_community: str | None = None
    #: The AcquisitionBudget plans are drafted under (Integration v0.4.0 §5); its NAS headroom floor
    #: is ``min_free_bytes``, the same floor DSI-010 refuses acquisition below
    plan_budget: AcquisitionBudget = DEFAULT_BUDGET

    def __post_init__(self) -> None:
        # One floor, not two: the plan's headroom is always min_free_bytes, however Settings is built.
        object.__setattr__(self, "plan_budget", replace(self.plan_budget, min_nas_headroom_bytes=self.min_free_bytes))

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
            gaia_source=gaia_source(env.get("SDP_GAIA_SOURCE", "auto")),
            zenodo_token=secret(env, "SDP_ZENODO_TOKEN"),
            zenodo_url=env.get("SDP_ZENODO_URL", "https://sandbox.zenodo.org"),
            zenodo_creators=env.get("SDP_ZENODO_CREATORS", ""),
            zenodo_community=env.get("SDP_ZENODO_COMMUNITY") or None,
            plan_budget=AcquisitionBudget(
                max_bytes=int(env.get("SDP_PLAN_MAX_BYTES", str(DEFAULT_BUDGET.max_bytes))),
                max_requests_per_source=int(
                    env.get("SDP_PLAN_MAX_REQUESTS_PER_SOURCE", str(DEFAULT_BUDGET.max_requests_per_source))
                ),
                max_wall_seconds=float(env.get("SDP_PLAN_MAX_WALL_SECONDS", str(DEFAULT_BUDGET.max_wall_seconds))),
                min_nas_headroom_bytes=0,  # replaced by min_free_bytes in __post_init__
                approval_threshold_bytes=int(
                    env.get("SDP_PLAN_APPROVAL_THRESHOLD_BYTES", str(DEFAULT_BUDGET.approval_threshold_bytes))
                ),
            ),
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


GAIA_SOURCES = ("auto", "tap", "bulk")


def gaia_source(value: str) -> str:
    if value not in GAIA_SOURCES:
        raise SettingsError(f"SDP_GAIA_SOURCE must be one of {', '.join(GAIA_SOURCES)}, got {value!r}")
    return value

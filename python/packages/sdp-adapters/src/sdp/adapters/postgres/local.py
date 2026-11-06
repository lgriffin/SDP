"""A PostgreSQL that runs on this computer for SDP alone, started and stopped by ``sdp db start|stop``
(#198): the portable PostgreSQL 16 that ``pnpm install`` brings for the desktop app, or any other
``pg_ctl``, with its data folder under ``<SDP data>/postgres``.

After an unclean exit the server is down with its ``postmaster.pid`` left behind, and the next start
spends a minute in crash recovery. ``start`` reads the port from ``SDP_DATABASE_URL``, moves a stale
``postmaster.pid`` aside (only when ``pg_ctl status`` finds no server and no live process holds the
port), and waits for recovery to finish.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

#: where ``pnpm install`` puts the desktop app's PostgreSQL, relative to the checkout
EMBEDDED = Path("typescript") / "apps" / "sdp-desktop" / "node_modules" / "@embedded-postgres"
LOOPBACK = {"127.0.0.1", "localhost", "::1"}

Runner = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]


def _run(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(args), capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)


def platform_folder() -> str:
    return "windows-x64" if sys.platform == "win32" else "darwin-arm64" if sys.platform == "darwin" else "linux-x64"


def exe(name: str) -> str:
    return f"{name}.exe" if sys.platform == "win32" else name


def host_port(database_url: str) -> tuple[str, int]:
    parts = urlsplit(database_url)
    return parts.hostname or "127.0.0.1", parts.port or 5432


def reachable(host: str, port: int, timeout_s: float = 2.0) -> bool:
    """Whether anything accepts a TCP connection there: a quick check before a run, not a login."""
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def find_bin(env: Mapping[str, str], repo_root: Path | None) -> Path | None:
    """The folder holding ``pg_ctl``: ``SDP_PG_BIN``, else the desktop app's PostgreSQL in the checkout
    (``pnpm install``), else ``pg_ctl`` on PATH."""
    if env.get("SDP_PG_BIN"):
        return Path(env["SDP_PG_BIN"])
    if repo_root is not None:
        direct = repo_root / EMBEDDED / platform_folder() / "native" / "bin"
        if (direct / exe("pg_ctl")).is_file():
            return direct
        # pnpm keeps the real copy under node_modules/.pnpm/@embedded-postgres+<platform>@<version>
        store = repo_root / "node_modules" / ".pnpm"
        if store.is_dir():
            pattern = f"@embedded-postgres+{platform_folder()}@*/node_modules/@embedded-postgres/{platform_folder()}/native/bin"
            for candidate in sorted(store.glob(pattern), reverse=True):
                if (candidate / exe("pg_ctl")).is_file():
                    return candidate
    on_path = shutil.which("pg_ctl")
    return Path(on_path).parent if on_path else None


@dataclass(frozen=True)
class LocalPostgres:
    pgdata: Path
    bin_dir: Path
    host: str
    port: int
    log_file: Path
    run: Runner = _run

    @property
    def pg_ctl(self) -> str:
        return str(self.bin_dir / exe("pg_ctl"))

    @property
    def pid_file(self) -> Path:
        return self.pgdata / "postmaster.pid"

    def server_running(self) -> bool:
        """``pg_ctl status``: exit 0 when a server runs on this data folder (it checks the pid is alive)."""
        return self.run([self.pg_ctl, "status", "-D", str(self.pgdata)]).returncode == 0

    def status(self) -> dict[str, object]:
        running = self.server_running()
        return {
            "pgdata": str(self.pgdata),
            "pg_ctl": self.pg_ctl,
            "address": f"{self.host}:{self.port}",
            "server_running": running,
            "reachable": reachable(self.host, self.port),
            "stale_pid_file": self.pid_file.exists() and not running,
        }

    def start(self, timeout_s: int = 600, clock: Callable[[], float] = time.monotonic) -> dict[str, object]:
        if not (self.pgdata / "PG_VERSION").is_file():
            raise ValueError(
                f"{self.pgdata} is not a PostgreSQL data folder (no PG_VERSION); pass --pgdata, or set SDP_PGDATA"
            )
        if self.host not in LOOPBACK:
            raise ValueError(
                f"SDP_DATABASE_URL points at {self.host}, not this computer; sdp db start runs a local server only"
            )
        if self.server_running():
            note = "already running"
            if not reachable(self.host, self.port):
                note += (
                    f", but nothing answers on {self.host}:{self.port} yet: it may still be recovering (see "
                    f"{self.log_file}); if that lasts, postmaster.pid may name an unrelated process, so stop "
                    "that process or check the folder by hand"
                )
            return {**self.status(), "started": False, "note": note}
        moved = None
        if self.pid_file.exists():
            if reachable(self.host, self.port):
                raise ValueError(
                    f"no server runs on {self.pgdata}, yet something answers on {self.host}:{self.port}; "
                    "another PostgreSQL (a Windows service?) has the port, so nothing was changed"
                )
            # pg_ctl found no live server for this pid file: an unclean exit left it behind
            moved = self.pid_file.with_name(f"postmaster.pid.stale-{int(time.time())}")
            self.pid_file.replace(moved)
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        started = clock()
        done = self.run(
            [
                self.pg_ctl,
                "start",
                "-D",
                str(self.pgdata),
                "-w",
                "-t",
                str(timeout_s),
                "-l",
                str(self.log_file),
                "-o",
                self._server_options(),
            ]
        )
        if done.returncode != 0:
            raise ValueError(
                f"pg_ctl start failed ({done.returncode}): {(done.stderr or done.stdout).strip()[-800:]}; see {self.log_file}"
            )
        return {
            **self.status(),
            "started": True,
            "seconds": round(clock() - started, 1),
            **({"moved_stale_pid_file": str(moved)} if moved else {}),
        }

    def _server_options(self) -> str:
        """The port from SDP_DATABASE_URL, loopback only; off Windows no Unix socket, whose default folder
        (/var/run/postgresql) an ordinary user cannot write."""
        listen = "127.0.0.1" if self.host == "localhost" else self.host
        options = f"-p {self.port} -c listen_addresses={listen}"
        return options if sys.platform == "win32" else options + " -c unix_socket_directories=''"

    def stop(self) -> dict[str, object]:
        if not self.server_running():
            return {**self.status(), "stopped": False, "note": "not running"}
        done = self.run([self.pg_ctl, "stop", "-D", str(self.pgdata), "-m", "fast", "-w", "-t", "120"])
        if done.returncode != 0:
            raise ValueError(f"pg_ctl stop failed ({done.returncode}): {(done.stderr or done.stdout).strip()[-800:]}")
        return {**self.status(), "stopped": True}


def local_postgres(
    database_url: str,
    env: Mapping[str, str] | None = None,
    pgdata: Path | None = None,
    bin_dir: Path | None = None,
    run: Runner = _run,
) -> LocalPostgres:
    """The local server ``SDP_DATABASE_URL`` names, with its data folder (``--pgdata``, ``SDP_PGDATA`` or
    ``<SDP data>/postgres``) and ``pg_ctl``."""
    env = os.environ if env is None else env
    host, port = host_port(database_url)
    data = Path(env["SDP_DATA"]) if env.get("SDP_DATA") else None
    folder = pgdata or (Path(env["SDP_PGDATA"]) if env.get("SDP_PGDATA") else (data / "postgres" if data else None))
    if folder is None:
        raise ValueError("no data folder for the database: pass --pgdata, or set SDP_PGDATA or SDP_DATA")
    repo = Path(env["SDP_REPO_ROOT"]) if env.get("SDP_REPO_ROOT") else None
    found = bin_dir or find_bin(env, repo)
    if found is None:
        raise ValueError(
            "pg_ctl not found: run `pnpm install` in the checkout (it brings PostgreSQL 16), set SDP_PG_BIN to the "
            "folder holding pg_ctl, or pass --bin"
        )
    logs = (data / "logs") if data else folder.parent / "logs"
    return LocalPostgres(folder, found, host, port, logs / "postgres-server.log", run)


def unreachable_message(database_url: str, error: BaseException) -> str:
    """What to tell a person whose command could not reach the database."""
    host, port = host_port(database_url)
    first = str(error).strip().splitlines()[0] if str(error).strip() else type(error).__name__
    local = host in LOOPBACK
    return f"the database at {host}:{port} is not reachable ({first})." + (
        " If it is SDP's own PostgreSQL on this computer, run `sdp db start`; `sdp db status` says what it finds."
        if local
        else " Check that the server is up and SDP_DATABASE_URL is right."
    )

"""``sdp db start|stop|status`` for SDP's own local PostgreSQL (#198), with pg_ctl replaced by a fake that
records its arguments. The real start is exercised by hand on Windows; these pin the decisions."""

from __future__ import annotations

import socket
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from sdp.adapters.postgres import local
from sdp.adapters.postgres.local import local_postgres, unreachable_message


class FakePgCtl:
    def __init__(self, running: bool = False, start_code: int = 0) -> None:
        self.running = running
        self.start_code = start_code
        self.calls: list[list[str]] = []

    def __call__(self, args: Sequence[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(args))
        verb = args[1]
        if verb == "status":
            return subprocess.CompletedProcess(args, 0 if self.running else 3, "", "")
        if verb == "start":
            self.running = self.start_code == 0
            return subprocess.CompletedProcess(args, self.start_code, "", "could not start" if self.start_code else "")
        if verb == "stop":
            self.running = False
        return subprocess.CompletedProcess(args, 0, "", "")


@pytest.fixture
def pgdata(tmp_path: Path) -> Path:
    folder = tmp_path / "data" / "postgres"
    folder.mkdir(parents=True)
    (folder / "PG_VERSION").write_text("16\n", "utf-8")
    return folder


@pytest.fixture
def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def server(pgdata: Path, port: int, fake: FakePgCtl) -> local.LocalPostgres:
    env = {"SDP_DATA": str(pgdata.parent), "SDP_PG_BIN": str(pgdata.parent / "bin")}
    return local_postgres(f"postgresql://sdp:x@127.0.0.1:{port}/sdp", env, run=fake)


def test_the_data_folder_and_port_come_from_sdp_data_and_the_database_url(pgdata: Path, free_port: int) -> None:
    pg = server(pgdata, free_port, FakePgCtl())
    assert pg.pgdata == pgdata and pg.port == free_port and pg.host == "127.0.0.1"
    assert pg.log_file == pgdata.parent / "logs" / "postgres-server.log"


def test_a_stale_pid_file_is_moved_aside_and_the_server_started_on_the_urls_port(pgdata: Path, free_port: int) -> None:
    (pgdata / "postmaster.pid").write_text("12345\n", "utf-8")
    fake = FakePgCtl(running=False)
    pg = server(pgdata, free_port, fake)
    assert pg.status()["stale_pid_file"] is True
    out = pg.start()
    assert out["started"] is True and not (pgdata / "postmaster.pid").exists()
    assert Path(str(out["moved_stale_pid_file"])).name.startswith("postmaster.pid.stale-")
    start = next(c for c in fake.calls if c[1] == "start")
    assert start[start.index("-o") + 1].startswith(f"-p {free_port} -c listen_addresses=127.0.0.1")
    assert start[start.index("-t") + 1] == "600", "crash recovery after an unclean exit takes about a minute"


def test_a_running_server_is_left_alone(pgdata: Path, free_port: int) -> None:
    fake = FakePgCtl(running=True)
    out = server(pgdata, free_port, fake).start()
    assert out["started"] is False and "already running" in str(out["note"])
    assert [c[1] for c in fake.calls if c[1] == "start"] == []


def test_a_pid_file_is_kept_when_another_server_holds_the_port(pgdata: Path) -> None:
    (pgdata / "postmaster.pid").write_text("12345\n", "utf-8")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        with pytest.raises(ValueError, match="another PostgreSQL"):
            server(pgdata, port, FakePgCtl()).start()
    assert (pgdata / "postmaster.pid").exists()


def test_a_folder_that_is_not_a_cluster_or_a_remote_url_is_refused(pgdata: Path, tmp_path: Path) -> None:
    env = {"SDP_DATA": str(tmp_path), "SDP_PG_BIN": str(tmp_path)}
    with pytest.raises(ValueError, match="no PG_VERSION"):
        local_postgres("postgresql://a@127.0.0.1:5544/x", env, pgdata=tmp_path / "empty", run=FakePgCtl()).start()
    with pytest.raises(ValueError, match="not this computer"):
        local_postgres("postgresql://a@db.example:5432/x", env, pgdata=pgdata, run=FakePgCtl()).start()


def test_a_failed_start_says_where_the_log_is(pgdata: Path, free_port: int) -> None:
    with pytest.raises(ValueError, match=r"postgres-server\.log"):
        server(pgdata, free_port, FakePgCtl(start_code=1)).start()


def test_stop_is_fast_and_waits(pgdata: Path, free_port: int) -> None:
    fake = FakePgCtl(running=True)
    assert server(pgdata, free_port, fake).stop()["stopped"] is True
    stop = next(c for c in fake.calls if c[1] == "stop")
    assert stop[stop.index("-m") + 1] == "fast" and "-w" in stop


def test_pg_ctl_is_found_in_the_checkouts_pnpm_store(tmp_path: Path) -> None:
    folder = local.platform_folder()
    store = (
        tmp_path
        / "node_modules"
        / ".pnpm"
        / f"@embedded-postgres+{folder}@16.14.0-beta.17"
        / "node_modules"
        / "@embedded-postgres"
        / folder
        / "native"
        / "bin"
    )
    store.mkdir(parents=True)
    (store / local.exe("pg_ctl")).write_text("", "utf-8")
    assert local.find_bin({}, tmp_path) == store
    assert local.find_bin({"SDP_PG_BIN": "/opt/pg"}, tmp_path) == Path("/opt/pg")


def test_an_unreachable_database_message_names_the_address_and_the_command() -> None:
    message = unreachable_message("postgresql://sdp:x@127.0.0.1:5544/sdp", OSError("connection refused\nmore"))
    assert message.startswith("the database at 127.0.0.1:5544 is not reachable (connection refused)")
    assert "sdp db start" in message
    assert "sdp db start" not in unreachable_message("postgresql://a@db.example/x", OSError("timeout"))

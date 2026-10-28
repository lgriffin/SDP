"""Behaviour that only the real adapters have: files on disk, Ed25519 signatures, textfiles, JSON logs."""

from __future__ import annotations

import io
import json
import logging
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from sdp.adapters.ledger.git import GitSignedLedger, LedgerError, load_signing_key
from sdp.adapters.logs import JsonFormatter, configure_logging
from sdp.adapters.settings import Settings, SettingsError
from sdp.adapters.storage.nas import NasArtifactStore
from sdp.adapters.system import SystemEnvironmentProbe, UlidGenerator
from sdp.adapters.telemetry.prometheus import PrometheusTextfileTracker, gpu_telemetry, write_gpu_telemetry
from sdp.kernel.domain.canonical import sha256_id
from sdp.kernel.domain.errors import IntegrityError
from sdp.kernel.ports.ledger import LedgerKind
from sdp.kernel.science.plan import Backend
from sdp.testing.fakes import FixedClock

from .keys import write_signing_key

pytestmark = [pytest.mark.tier("T5"), pytest.mark.integration]


# --- NAS artifact store -------------------------------------------------------------------------


@pytest.mark.req("NFR-003")
def test_nas_detects_a_corrupted_object_on_read(tmp_path: Path) -> None:
    store = NasArtifactStore(tmp_path)
    artifact = store.put(b"original bytes", "text/plain")
    obj = next(p for p in (tmp_path / "objects").rglob("*") if p.is_file() and not p.name.endswith(".media"))
    obj.chmod(0o644)
    obj.write_bytes(b"bit rot")
    with pytest.raises(IntegrityError):
        store.get(artifact)


def test_nas_objects_are_content_addressed_and_read_only(tmp_path: Path) -> None:
    store = NasArtifactStore(tmp_path)
    artifact = store.put(b"x", "text/plain")
    hexdigest = artifact.value.removeprefix("sha256:")
    path = tmp_path / "objects" / "sha256" / hexdigest[:2] / hexdigest[2:]
    assert path.read_bytes() == b"x"
    assert not path.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)


def test_nas_survives_a_reopen(tmp_path: Path) -> None:
    artifact = NasArtifactStore(tmp_path).put(b"kept", "application/json", logical_path="raw/gaia/part-0")
    reopened = NasArtifactStore(tmp_path)
    assert reopened.get(artifact) == b"kept"
    assert reopened.media_type(artifact) == "application/json"
    assert reopened.resolve("raw/gaia/part-0") == artifact


# --- Git-signed ledger --------------------------------------------------------------------------


def _ledger(tmp_path: Path, **kwargs: object) -> GitSignedLedger:
    key_path, _ = write_signing_key(tmp_path, "leigh")
    return GitSignedLedger(
        root=tmp_path / "ledger",
        clock=FixedClock(),
        signing_key=load_signing_key(key_path),
        allowed_signers=tmp_path / "allowed_signers",
        **kwargs,  # type: ignore[arg-type]
    )


def _append(ledger: GitSignedLedger, n: int = 3) -> None:
    for i in range(n):
        kind: LedgerKind = "predeclaration" if i % 2 == 0 else "approval"
        ledger.append(kind=kind, subject_id=f"s{i}", subject_hash=sha256_id(bytes([i])), signer="leigh", payload={})


@pytest.mark.req("KRN-002")
def test_ledger_writes_one_chain_across_kind_files(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    _append(ledger, 4)
    assert len((tmp_path / "ledger" / "predeclarations.jsonl").read_text().splitlines()) == 2
    assert len((tmp_path / "ledger" / "approvals.jsonl").read_text().splitlines()) == 2
    assert [e.sequence for e in ledger.entries()] == [0, 1, 2, 3]
    assert all(e.signature_method == "ssh" for e in ledger.entries())
    assert ledger.verify() == []


@pytest.mark.req("KRN-002")
def test_ledger_detects_an_edited_line(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    _append(ledger)
    path = tmp_path / "ledger" / "predeclarations.jsonl"
    lines = path.read_text().splitlines()
    doc = json.loads(lines[0])
    doc["subject_id"] = "rewritten"
    path.write_text("\n".join([json.dumps(doc), *lines[1:]]) + "\n")
    problems = ledger.verify()
    assert any("bad signature" in p for p in problems)
    assert any("chain broken" in p for p in problems)


def test_ledger_detects_a_deleted_entry(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    _append(ledger, 3)
    (tmp_path / "ledger" / "approvals.jsonl").write_text("")
    assert any("sequence" in p or "chain broken" in p for p in ledger.verify())


def test_ledger_rejects_a_signer_outside_allowed_signers(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.append(kind="predeclaration", subject_id="p", subject_hash=sha256_id(b"p"), signer="mallory", payload={})
    assert any("not in" in p for p in ledger.verify())


def test_ledger_rejects_a_signature_from_another_key(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    other, _ = write_signing_key(tmp_path, "impostor")
    forger = GitSignedLedger(
        root=tmp_path / "ledger",
        clock=FixedClock(),
        signing_key=load_signing_key(other),
        allowed_signers=tmp_path / "allowed_signers",
    )
    forger.append(kind="approval", subject_id="e", subject_hash=sha256_id(b"e"), signer="leigh", payload={})
    assert any("bad signature" in p for p in ledger.verify())


def test_ledger_refuses_to_append_unsigned_when_signatures_are_required(tmp_path: Path) -> None:
    ledger = GitSignedLedger(root=tmp_path / "ledger", clock=FixedClock())
    with pytest.raises(LedgerError):
        ledger.append(kind="predeclaration", subject_id="p", subject_hash=sha256_id(b"p"), signer="x", payload={})


def test_ledger_commits_each_append_when_asked(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.org"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "root"], check=True)
    key_path, _ = write_signing_key(tmp_path, "leigh")
    ledger = GitSignedLedger(
        root=repo / "ledger",
        clock=FixedClock(),
        signing_key=load_signing_key(key_path),
        allowed_signers=tmp_path / "allowed_signers",
        commit=True,
        git_identity=("SDP ledger", "ledger@sdp.invalid"),
    )
    _append(ledger, 2)
    log = subprocess.run([*git, "log", "--format=%s"], check=True, capture_output=True, text=True).stdout
    assert log.splitlines()[:2] == ["ledger: approval s1 (led-000002)", "ledger: predeclaration s0 (led-000001)"]


# --- Telemetry (NFR-010) ------------------------------------------------------------------------


@pytest.mark.req("NFR-010")
def test_tracker_writes_prometheus_gauges_per_experiment(tmp_path: Path) -> None:
    tracker = PrometheusTextfileTracker(tmp_path)
    tracker.log_params("exp-01", {"seed": 42})
    tracker.log_metrics("exp-01", {"rms residual": 0.25, "duration_s": 3.0})
    tracker.set_tags("exp-01", {"verdict": "VERIFIED"})
    text = (tmp_path / "sdp_experiment_exp_01.prom").read_text()
    assert "# TYPE sdp_experiment_rms_residual gauge" in text
    assert 'sdp_experiment_rms_residual{experiment_id="exp-01"} 0.25' in text
    assert 'sdp_experiment_info{experiment_id="exp-01",seed="42",verdict="VERIFIED"} 1' in text
    assert not list(tmp_path.glob(".*.tmp"))


@pytest.mark.req("NFR-010")
def test_gpu_telemetry_parses_nvidia_smi_and_is_empty_without_a_gpu(tmp_path: Path) -> None:
    csv = "0, 87, 30512, 131072, 61, 210.5\n"
    text = gpu_telemetry(csv)
    assert 'sdp_gpu_utilization_gpu{gpu="0"} 87.0' in text
    assert 'sdp_gpu_power_draw{gpu="0"} 210.5' in text
    assert gpu_telemetry("") == ""
    assert write_gpu_telemetry(tmp_path, csv).read_text() == text


# --- Logs (NFR-005) -----------------------------------------------------------------------------


@pytest.mark.req("NFR-005")
def test_json_logs_carry_the_kernel_extras() -> None:
    stream = io.StringIO()
    handler = configure_logging("INFO", stream)
    try:
        logging.getLogger("sdp.kernel.runner").info(
            "task completed",
            extra={"experiment_id": "exp-1", "task_id": "fit", "duration_s": 1.5, "resource_maxrss_kb": 2048.0},
        )
    finally:
        logging.getLogger("sdp").removeHandler(handler)
    doc = json.loads(stream.getvalue())
    assert doc["message"] == "task completed" and doc["level"] == "info" and doc["time"].endswith("Z")
    assert doc["experiment_id"] == "exp-1" and doc["task_id"] == "fit"
    assert doc["duration_s"] == 1.5 and doc["resource_maxrss_kb"] == 2048.0


def test_json_formatter_includes_exceptions() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        record = logging.makeLogRecord(
            {"name": "sdp", "levelname": "ERROR", "msg": "failed", "exc_info": sys.exc_info()}
        )
    doc = json.loads(JsonFormatter().format(record))
    assert "ValueError: boom" in doc["exception"]


# --- System seams and settings ------------------------------------------------------------------


def test_ulids_sort_by_creation_time() -> None:
    ids = UlidGenerator()
    values = [ids.new("exp") for _ in range(200)]
    assert values == sorted(values)


def test_environment_probe_reads_git_and_the_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SDP_CODE_REVISION", "a" * 40)
    monkeypatch.setenv("SDP_IMAGE_DIGEST", "sha256:" + "b" * 64)
    lock = tmp_path / "uv.lock"
    lock.write_text("lock")
    record = SystemEnvironmentProbe(repo=tmp_path, lock_file=lock).record(Backend.CPU)
    assert record.code_revision.startswith("a" * 40)
    assert record.lock_digest == sha256_id(b"lock")
    assert record.image_digest == "sha256:" + "b" * 64
    assert record.backend == "cpu" and record.cuda_version is None


def test_settings_come_from_the_environment_and_name_what_is_missing(tmp_path: Path) -> None:
    with pytest.raises(SettingsError, match="SDP_DATABASE_URL"):
        Settings.from_env({"SDP_ARTIFACT_ROOT": str(tmp_path)})
    settings = Settings.from_env(
        {"SDP_DATABASE_URL": "postgresql://x", "SDP_ARTIFACT_ROOT": str(tmp_path), "SDP_REPO_ROOT": str(tmp_path)}
    )
    assert settings.ledger_dir == tmp_path.resolve() / "ledger"
    assert settings.require_signatures

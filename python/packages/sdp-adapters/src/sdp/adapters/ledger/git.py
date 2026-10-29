"""GitSignedLedger: the production Ledger (ADR-004, §15).

Entries live one JSON object per line in ``<root>/<kind>s.jsonl`` and form one hash chain across
both files, ordered by ``sequence``. Each entry is signed with an Ed25519 key held in OpenSSH format
(the same key a person uses for ``git commit -S`` with ``gpg.format=ssh``) and verified against an
``allowed_signers`` file in the format ``ssh-keygen -Y verify`` reads: ``<principal> ssh-ed25519 <key>``.

With ``commit=True`` every append is also committed to Git, so the repository history is a second,
independent copy of the chain; ``require_signatures`` refuses to append or verify unsigned entries.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from sdp.kernel.domain.canonical import canonical_json
from sdp.kernel.ports.determinism import Clock
from sdp.kernel.ports.ledger import LedgerEntry, LedgerKind

_KINDS: tuple[LedgerKind, ...] = ("predeclaration", "approval")


class LedgerError(RuntimeError):
    pass


def load_signing_key(path: Path, password: bytes | None = None) -> Ed25519PrivateKey:
    key = serialization.load_ssh_private_key(path.read_bytes(), password=password)
    if not isinstance(key, Ed25519PrivateKey):
        raise LedgerError(f"{path}: the ledger signs with Ed25519 keys only")
    return key


def public_key_line(key: Ed25519PrivateKey) -> str:
    """``ssh-ed25519 AAAA...`` for an allowed_signers entry."""
    return key.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH).decode()


def read_allowed_signers(path: Path) -> dict[str, Ed25519PublicKey]:
    signers: dict[str, Ed25519PublicKey] = {}
    if not path.exists():
        return signers
    for number, raw in enumerate(path.read_text().splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 3:
            raise LedgerError(f"{path}:{number}: expected '<principal> <key-type> <key>'")
        key = serialization.load_ssh_public_key(f"{parts[1]} {parts[2]}".encode())
        if not isinstance(key, Ed25519PublicKey):
            raise LedgerError(f"{path}:{number}: only ssh-ed25519 keys are accepted")
        for principal in parts[0].split(","):
            signers[principal] = key
    return signers


def _signed_bytes(entry: LedgerEntry) -> bytes:
    return canonical_json(entry.unsigned_document()).encode()


@dataclass
class GitSignedLedger:
    root: Path
    clock: Clock
    signing_key: Ed25519PrivateKey | None = None
    allowed_signers: Path | None = None
    commit: bool = False
    require_signatures: bool = True
    #: (name, email) for ledger commits; None uses the repository's configured identity.
    git_identity: tuple[str, str] | None = None
    _signers: dict[str, Ed25519PublicKey] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.allowed_signers = Path(self.allowed_signers or self.root / "allowed_signers")
        self._signers = read_allowed_signers(self.allowed_signers)

    def path(self, kind: LedgerKind) -> Path:
        return self.root / f"{kind}s.jsonl"

    @contextmanager
    def _locked(self) -> Iterator[None]:
        with (self.root / ".lock").open("a+") as handle:
            if sys.platform == "win32":
                import msvcrt

                # Lock the first byte; LK_LOCK retries for about ten seconds before raising.
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle, fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle, fcntl.LOCK_UN)

    def _read(self) -> list[LedgerEntry]:
        entries: list[LedgerEntry] = []
        for kind in _KINDS:
            path = self.path(kind)
            if path.exists():
                for line in path.read_text().splitlines():
                    if line.strip():
                        entries.append(LedgerEntry.from_document(json.loads(line)))
        return sorted(entries, key=lambda e: e.sequence)

    def append(
        self, *, kind: LedgerKind, subject_id: str, subject_hash: str, signer: str, payload: Mapping[str, Any]
    ) -> LedgerEntry:
        if self.signing_key is None and self.require_signatures:
            raise LedgerError("no signing key configured and require_signatures is set")
        with self._locked():
            existing = self._read()
            sequence = existing[-1].sequence + 1 if existing else 0
            unsigned = LedgerEntry(
                entry_id=f"led-{sequence + 1:06d}",
                sequence=sequence,
                kind=kind,
                subject_id=subject_id,
                subject_hash=subject_hash,
                recorded_at=self.clock.now(),
                signer=signer,
                signature_method="ssh" if self.signing_key else "unsigned",
                signature="",
                previous_entry_hash=existing[-1].entry_hash() if existing else None,
                payload=dict(payload),
            )
            signature = (
                base64.b64encode(self.signing_key.sign(_signed_bytes(unsigned))).decode() if self.signing_key else ""
            )
            entry = LedgerEntry(**{**unsigned.__dict__, "signature": signature})
            path = self.path(kind)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry.to_document(), sort_keys=True, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            if self.commit:
                self._commit(path, entry)
            return entry

    def _commit(self, path: Path, entry: LedgerEntry) -> None:
        message = f"ledger: {entry.kind} {entry.subject_id} ({entry.entry_id})"
        git = ["git"]
        if self.git_identity:
            git += ["-c", f"user.name={self.git_identity[0]}", "-c", f"user.email={self.git_identity[1]}"]
        try:
            subprocess.run([*git, "add", str(path)], cwd=self.root, check=True, capture_output=True)
            subprocess.run(
                [*git, "commit", "-m", message, "--", str(path)], cwd=self.root, check=True, capture_output=True
            )
        except subprocess.CalledProcessError as exc:
            raise LedgerError(f"git commit of {path} failed: {exc.stderr.decode(errors='replace').strip()}") from exc

    def find(self, subject_hash: str, kind: LedgerKind = "predeclaration") -> LedgerEntry | None:
        for entry in self._read():
            if entry.subject_hash == subject_hash and entry.kind == kind:
                return entry
        return None

    def entries(self) -> Sequence[LedgerEntry]:
        return self._read()

    def verify(self) -> list[str]:
        problems: list[str] = []
        previous: str | None = None
        try:
            entries = self._read()
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            return [f"unreadable ledger: {exc}"]
        for i, entry in enumerate(entries):
            if entry.sequence != i:
                problems.append(f"{entry.entry_id}: sequence {entry.sequence} at position {i}")
            if entry.previous_entry_hash != previous:
                problems.append(f"{entry.entry_id}: chain broken")
            problems.extend(self._check_signature(entry))
            previous = entry.entry_hash()
        return problems

    def _check_signature(self, entry: LedgerEntry) -> list[str]:
        if entry.signature_method == "unsigned":
            return [f"{entry.entry_id}: unsigned"] if self.require_signatures else []
        if entry.signature_method != "ssh":
            return [f"{entry.entry_id}: unsupported signature method {entry.signature_method!r}"]
        key = self._signers.get(entry.signer)
        if key is None:
            return [f"{entry.entry_id}: signer {entry.signer!r} is not in {self.allowed_signers}"]
        try:
            key.verify(base64.b64decode(entry.signature), _signed_bytes(entry))
        except (InvalidSignature, ValueError):
            return [f"{entry.entry_id}: bad signature"]
        return []

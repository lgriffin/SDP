"""Downloads are untrusted data (§15, NFR-009): validated before parsing, and parsed out of process.

``validate_votable`` refuses what is not a plausible VOTable before any parser sees it: too large,
not XML, or carrying a DTD (VOTable needs none, and a DTD is how entity-expansion attacks arrive).
``IsolatedVotableParser`` then parses in a child interpreter under memory and CPU limits; the child
returns an Arrow IPC stream on stdout, a format the parent can read without executing anything, so
a parser compromised by a hostile document gains nothing in the parent process.
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass

import pyarrow as pa

from sdp.adapters.archives.votable import TapQueryError

MAX_DOCUMENT_BYTES = 512 * 1024**2
TAP_ERROR_EXIT = 3
_DECLARATION = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)


class UntrustedInput(ValueError):
    pass


def validate_votable(body: bytes, max_bytes: int = MAX_DOCUMENT_BYTES) -> None:
    if len(body) > max_bytes:
        raise UntrustedInput(f"response of {len(body)} bytes exceeds the {max_bytes}-byte limit")
    head = body[:4096].lstrip(b"\xef\xbb\xbf \t\r\n")
    if not head.startswith((b"<?xml", b"<VOTABLE")):
        raise UntrustedInput("response is not an XML VOTable document")
    # the whole (bounded) document, so a long valid prefix cannot push a DTD past the check
    if _DECLARATION.search(body):
        raise UntrustedInput("VOTable documents carry no DTD; refusing a document that declares one")


@dataclass(frozen=True)
class IsolatedVotableParser:
    memory_bytes: int = 4 * 1024**3
    cpu_seconds: int = 120
    timeout_s: float = 300.0

    def __call__(self, body: bytes) -> pa.Table:
        validate_votable(body)
        # the child sets its own limits before importing the parser: no preexec_fn, which can
        # deadlock between fork and exec in a threaded parent such as the API server
        command = [sys.executable, "-I", "-m", "sdp.adapters.archives.isolated_parse"]
        command += ["--memory", str(self.memory_bytes), "--cpu", str(self.cpu_seconds)]
        try:
            done = subprocess.run(command, input=body, capture_output=True, timeout=self.timeout_s, check=False)
        except subprocess.TimeoutExpired as exc:
            raise UntrustedInput(f"VOTable parsing exceeded {self.timeout_s:.0f} s") from exc
        message = done.stderr.decode("utf-8", "replace").strip().splitlines()
        if done.returncode == TAP_ERROR_EXIT:
            raise TapQueryError(message[-1] if message else "TAP query failed")
        if done.returncode != 0:
            reason = message[-1] if message else f"exit status {done.returncode}"
            raise UntrustedInput(f"VOTable parser failed in isolation: {reason}")
        with pa.ipc.open_stream(done.stdout) as reader:
            return reader.read_all()

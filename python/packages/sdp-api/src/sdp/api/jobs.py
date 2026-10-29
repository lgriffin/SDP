"""Long commands on a worker thread, so a request never waits minutes on an archive (INV-018).

Executing a JWST plan or reading MAST's descriptor takes 5 to 20 minutes, longer than a browser or
the UI server waits for an answer. The endpoint starts the work here and answers at once; the UI
then reads the job's state over GraphQL until it has finished or failed. Jobs live in the API's
process: a restart forgets a running job, and the plan stays approved and can be started again.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger(__name__)

RUNNING = "running"
FINISHED = "finished"
FAILED = "failed"
REGISTER_JOB = "archives:register"


def plan_job(plan_id: str) -> str:
    return f"plan:{plan_id}"


def reproduce_job(set_id: str) -> str:
    return f"reproduce:{set_id}"


@dataclass
class Job:
    key: str
    started_at: datetime
    state: str = RUNNING
    finished_at: datetime | None = None
    error: str | None = None
    result: Any = None
    """What the work returned, for a job whose answer the UI shows (a reproduction's report)."""

    def to_document(self) -> dict[str, Any]:
        doc = {
            "state": self.state,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "error": self.error,
        }
        return doc if self.result is None else {**doc, "result": self.result}


def _thread(work: Callable[[], None]) -> None:
    threading.Thread(target=work, daemon=True).start()


class Jobs:
    """At most one job per key at a time; the last job of each key is kept for the UI to read."""

    def __init__(
        self,
        spawn: Callable[[Callable[[], None]], None] = _thread,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._spawn = spawn
        self._now = now
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Job | None:
        with self._lock:
            return self._jobs.get(key)

    def start(self, key: str, work: Callable[[], Any]) -> tuple[Job, bool]:
        """The job for ``key`` and whether this call started it; a running job is never started twice."""
        with self._lock:
            current = self._jobs.get(key)
            if current is not None and current.state == RUNNING:
                return current, False
            job = Job(key, self._now())
            self._jobs[key] = job

        def run() -> None:
            try:
                result = work()
            except Exception as exc:  # the job reports every failure; nothing is left to raise to
                log.exception("job failed", extra={"job": key})
                with self._lock:
                    job.state, job.error = FAILED, f"{type(exc).__name__}: {exc}"
            else:
                with self._lock:
                    job.state, job.result = FINISHED, result
            finally:
                with self._lock:
                    job.finished_at = self._now()

        self._spawn(run)
        return job, True

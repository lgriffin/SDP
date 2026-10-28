"""CpuExecutor: the CPU reference executor (ADR-007).

It runs a plan in topological order in this process using the kernel's reference loop, with BLAS
and OpenMP threads pinned so results do not depend on how many cores the host has (§8). Tasks run
in-process because checkpoints and inputs never cross a pickle boundary (§15).
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from sdp.kernel.application.execution import run_plan
from sdp.kernel.ports.compute import CheckpointSink
from sdp.kernel.science.plan import Backend, ComputePlan, TaskKindRegistry

_THREAD_VARIABLES = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS")


@contextmanager
def _pinned_threads(threads: int) -> Iterator[None]:
    saved = {k: os.environ.get(k) for k in _THREAD_VARIABLES}
    os.environ.update({k: str(threads) for k in _THREAD_VARIABLES})
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@dataclass
class CpuExecutor:
    threads: int = 1
    backend: Backend = Backend.CPU

    def execute(
        self,
        plan: ComputePlan,
        registry: TaskKindRegistry,
        *,
        seed: int,
        sink: CheckpointSink,
        services: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        with _pinned_threads(self.threads):
            return run_plan(plan, registry, backend=Backend.CPU, seed=seed, sink=sink, services=services)

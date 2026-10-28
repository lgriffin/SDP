"""Run metrics and GPU telemetry as Prometheus textfiles for node_exporter, scraped into Grafana (NFR-010).

node_exporter's textfile collector reads ``*.prom`` files from one directory. Each experiment gets
its own file, rewritten atomically on every update, so a half-written file is never scraped.
"""

from __future__ import annotations

import math
import os
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sdp.adapters.system import run_command

_NAME = re.compile(r"[^a-zA-Z0-9_]")


def metric_name(name: str) -> str:
    cleaned = _NAME.sub("_", name)
    return cleaned if cleaned and not cleaned[0].isdigit() else f"_{cleaned}"


def _label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


@dataclass
class PrometheusTextfileTracker:
    """ExperimentTracker writing ``sdp_experiment_<metric>`` gauges per experiment."""

    directory: Path
    _metrics: dict[str, dict[str, float]] = field(default_factory=dict, repr=False)
    _params: dict[str, dict[str, str]] = field(default_factory=dict, repr=False)
    _tags: dict[str, dict[str, str]] = field(default_factory=dict, repr=False)

    def path(self, experiment_id: str) -> Path:
        return Path(self.directory) / f"sdp_experiment_{metric_name(experiment_id)}.prom"

    def log_params(self, experiment_id: str, params: Mapping[str, Any]) -> None:
        self._params.setdefault(experiment_id, {}).update({k: str(v) for k, v in params.items()})
        self._flush(experiment_id)

    def log_metrics(self, experiment_id: str, metrics: Mapping[str, float]) -> None:
        self._metrics.setdefault(experiment_id, {}).update({k: float(v) for k, v in metrics.items()})
        self._flush(experiment_id)

    def set_tags(self, experiment_id: str, tags: Mapping[str, str]) -> None:
        self._tags.setdefault(experiment_id, {}).update({k: str(v) for k, v in tags.items()})
        self._flush(experiment_id)

    def render(self, experiment_id: str) -> str:
        exp = _label(experiment_id)
        lines: list[str] = []
        for name, value in sorted(self._metrics.get(experiment_id, {}).items()):
            if math.isnan(value):
                rendered = "NaN"
            elif math.isinf(value):
                rendered = "+Inf" if value > 0 else "-Inf"
            else:
                rendered = repr(value)
            metric = f"sdp_experiment_{metric_name(name)}"
            lines += [f"# TYPE {metric} gauge", f'{metric}{{experiment_id="{exp}"}} {rendered}']
        info = {**self._params.get(experiment_id, {}), **self._tags.get(experiment_id, {})}
        if info:
            labels = ",".join(f'{metric_name(k)}="{_label(v)}"' for k, v in sorted(info.items()))
            lines += ["# TYPE sdp_experiment_info gauge", f'sdp_experiment_info{{experiment_id="{exp}",{labels}}} 1']
        return "\n".join(lines) + "\n" if lines else ""

    def _flush(self, experiment_id: str) -> None:
        _write_atomic(self.path(experiment_id), self.render(experiment_id))


_GPU_FIELDS = ("utilization.gpu", "memory.used", "memory.total", "temperature.gpu", "power.draw")


def gpu_telemetry(smi_csv: str | None = None) -> str:
    """``sdp_gpu_*`` gauges from ``nvidia-smi --query-gpu``; empty when there is no GPU."""
    if smi_csv is None:
        smi_csv = run_command(
            "nvidia-smi", f"--query-gpu=index,{','.join(_GPU_FIELDS)}", "--format=csv,noheader,nounits"
        )
    if not smi_csv:
        return ""
    lines = [f"# TYPE sdp_gpu_{metric_name(f)} gauge" for f in _GPU_FIELDS]
    for row in smi_csv.strip().splitlines():
        index, *values = (v.strip() for v in row.split(","))
        for name, value in zip(_GPU_FIELDS, values, strict=False):
            try:
                number = float(value)
            except ValueError:
                continue
            lines.append(f'sdp_gpu_{metric_name(name)}{{gpu="{_label(index)}"}} {number!r}')
    return "\n".join(lines) + "\n"


def write_gpu_telemetry(directory: Path, smi_csv: str | None = None) -> Path:
    path = Path(directory) / "sdp_gpu.prom"
    _write_atomic(path, gpu_telemetry(smi_csv))
    return path

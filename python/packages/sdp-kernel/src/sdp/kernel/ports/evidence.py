"""EvidenceRepository (PROV-aligned graph) and ExperimentTracker (§6)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from sdp.kernel.domain.provenance import ProvGraph


class EvidenceRepository(Protocol):
    def record(self, graph: ProvGraph) -> None:
        """Merge a fragment of activities, entities, agents and relations into the graph."""
        ...

    def subgraph(self, activity_id: str) -> ProvGraph:
        """Everything connected to an activity (an experiment run), for its manifest."""
        ...

    def lineage(self, entity_id: str) -> list[str]:
        """Upstream entities and activities of an entity, nearest first."""
        ...


class ExperimentTracker(Protocol):
    """Optional mirror of metrics and parameters; the manifest stays the source of truth."""

    def log_params(self, experiment_id: str, params: Mapping[str, Any]) -> None: ...

    def log_metrics(self, experiment_id: str, metrics: Mapping[str, float]) -> None: ...

    def set_tags(self, experiment_id: str, tags: Mapping[str, str]) -> None: ...

"""What flows into and out of a ScientificProtocol."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from sdp.kernel.domain.errors import InvariantViolation
from sdp.kernel.domain.ids import ArtifactId
from sdp.kernel.domain.sources import SourceRef

Params = Mapping[str, Any]


@dataclass(frozen=True)
class InputSpec:
    """An artifact the experiment must supply, by role and media type."""

    role: str
    media_type: str
    description: str = ""
    required: bool = True


@dataclass(frozen=True)
class ResolvedInput:
    role: str
    artifact_id: ArtifactId
    media_type: str
    source: SourceRef
    data: Any


@dataclass(frozen=True)
class ResolvedInputs:
    """Inputs loaded and decoded by the kernel before plan() is called."""

    items: Mapping[str, ResolvedInput] = field(default_factory=dict)

    def __getitem__(self, role: str) -> Any:
        try:
            return self.items[role].data
        except KeyError:
            raise InvariantViolation(f"no input bound to role {role!r}") from None

    def __contains__(self, role: object) -> bool:
        return role in self.items

    def artifact(self, role: str) -> ResolvedInput:
        return self.items[role]


@dataclass(frozen=True)
class ComputeResults:
    """Task id -> result value, for the tasks the plan declared as outputs (or all of them)."""

    values: Mapping[str, Any]

    def __getitem__(self, task_id: str) -> Any:
        return self.values[task_id]

    def __contains__(self, task_id: object) -> bool:
        return task_id in self.values

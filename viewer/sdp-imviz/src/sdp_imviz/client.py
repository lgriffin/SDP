"""The plugin's only way to the kernel: ``POST /investigations`` on sdp-api (INV-012)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol


class InvestigateError(RuntimeError):
    """sdp-api refused or could not be reached; the message is the server's."""


@dataclass(frozen=True)
class Candidate:
    investigation_id: str
    candidate: str
    score: float
    state: str
    label: str
    positions: tuple[tuple[float, float], ...]

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> Candidate:
        return cls(
            doc["investigation_id"],
            doc["candidate"],
            float(doc["score"]),
            doc["state"],
            doc["label"],
            tuple((float(ra), float(dec)) for ra, dec in doc.get("positions", [])),
        )


@dataclass(frozen=True)
class RunResult:
    """One InvestigateField run as the API returned it. Everything in it is EXPLORATORY."""

    set_id: str
    field_id: str
    profile: str
    profile_hash: str
    label: str
    stages: Mapping[str, str]
    objects_examined: int
    candidates: tuple[Candidate, ...]
    raw: Mapping[str, Any] = field(repr=False, compare=False, default_factory=dict)

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> RunResult:
        return cls(
            doc["set_id"],
            doc["field_id"],
            doc["profile"],
            doc["profile_hash"],
            doc["label"],
            dict(doc["stages"]),
            int(doc["objects_examined"]),
            tuple(Candidate.from_document(c) for c in doc["candidates"]),
            raw=dict(doc),
        )

    def rows(self) -> list[dict[str, Any]]:
        """One row per marker: a candidate object, or each member of a candidate structure."""
        return [
            {
                "investigation": c.investigation_id,
                "candidate": c.candidate,
                "score": c.score,
                "state": c.state,
                "ra_deg": ra,
                "dec_deg": dec,
            }
            for c in self.candidates
            for ra, dec in c.positions
        ]


class InvestigateClient(Protocol):
    def investigate(
        self, region: str, profile: Mapping[str, Any], *, seed: int = 0, label: str = "", epoch_jyear: float = 2016.0
    ) -> RunResult: ...


@dataclass(frozen=True)
class HttpInvestigateClient:
    base_url: str = "http://127.0.0.1:8000"
    timeout_s: float = 3600.0

    def investigate(
        self, region: str, profile: Mapping[str, Any], *, seed: int = 0, label: str = "", epoch_jyear: float = 2016.0
    ) -> RunResult:
        body = json.dumps(
            {"region": region, "profile": dict(profile), "seed": seed, "label": label, "epoch_jyear": epoch_jyear}
        ).encode()
        request = urllib.request.Request(
            self.base_url.rstrip("/") + "/investigations",
            data=body,
            headers={"content-type": "application/json", "accept": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                return RunResult.from_document(json.loads(response.read()))
        except urllib.error.HTTPError as exc:
            try:
                message = json.loads(exc.read()).get("error", exc.reason)
            except ValueError:
                message = exc.reason
            raise InvestigateError(f"sdp-api answered {exc.code}: {message}") from None
        except urllib.error.URLError as exc:
            raise InvestigateError(f"cannot reach {self.base_url}: {exc.reason}") from None

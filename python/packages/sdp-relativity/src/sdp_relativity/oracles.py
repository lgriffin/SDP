"""The oracle registry and what the module may do with it (§9, §11.2)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib import resources
from typing import Any

import yaml

from sdp.kernel.domain.findings import OracleStatus


@dataclass(frozen=True)
class Oracle:
    oracle_id: str
    kind: str
    status: str
    instrument: str | None = None
    value_arcsec: float | None = None
    uncertainty_arcsec: float | None = None
    convention: str | None = None
    source: str | None = None
    locator: str | None = None
    transcriber: str | None = None
    verifications: tuple[Mapping[str, Any], ...] = ()
    raw: Mapping[str, Any] = field(default_factory=dict)

    @property
    def verified(self) -> bool:
        """Analytic oracles are verified by construction; a transcribed value needs a locator and a
        matching second pass, which its transcriber may make (SDP is single-user)."""
        if self.status == "analytic":
            return True
        return bool(self.locator) and any(v.get("matches") and v.get("verifier") for v in self.verifications)

    def status_record(self) -> OracleStatus:
        note = (
            "analytic"
            if self.status == "analytic"
            else (
                "transcribed and verified"
                if self.verified
                else f"{self.status}: no locator or second-pass verification"
            )
        )
        return OracleStatus(self.oracle_id, self.verified, note)


def load_registry(text: str | None = None) -> dict[str, Oracle]:
    if text is None:
        text = resources.files("sdp_relativity").joinpath("oracles.yaml").read_text("utf-8")
    doc = yaml.safe_load(text)
    out: dict[str, Oracle] = {}
    for raw in doc["oracles"]:
        out[raw["oracle_id"]] = Oracle(
            oracle_id=raw["oracle_id"],
            kind=raw["kind"],
            status=raw["status"],
            instrument=raw.get("instrument"),
            value_arcsec=raw.get("value_arcsec"),
            uncertainty_arcsec=raw.get("uncertainty_arcsec"),
            convention=raw.get("convention"),
            source=raw.get("source"),
            locator=raw.get("locator"),
            transcriber=raw.get("transcriber"),
            verifications=tuple(raw.get("verifications") or ()),
            raw=raw,
        )
    return out

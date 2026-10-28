"""From transcribed records to an observation bundle, keeping every locator (REL-001).

Each TranscribedRecord holds one printed row exactly as transcribed. Rows with ``kind: star`` carry
a star's position and measured displacement on one plate; rows with ``kind: plate`` carry a plate's
check-plate constants; rows with ``kind: instrument`` carry the solar radius used for that set.
A verification that does not match, with corrections, supersedes the transcribed fields.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sdp.kernel.domain.historical import TranscribedRecord, VerificationRecord
from sdp_relativity.observations import BUNDLE_SCHEMA

_STAR_FIELDS = ("x_arcmin", "y_arcmin", "dx_arcsec", "dy_arcsec", "error_arcsec", "convention")


def _effective(record: TranscribedRecord, verifications: Sequence[VerificationRecord]) -> dict[str, Any]:
    fields = dict(record.fields)
    for v in verifications:
        if v.record_id == record.record_id and not v.matches and v.verifier != record.transcriber:
            fields.update(v.corrections)
    return fields


def bundle_from_records(
    records: Sequence[TranscribedRecord],
    verifications: Sequence[VerificationRecord],
    provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    instruments: dict[str, dict[str, Any]] = {}
    plates: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records:
        f = _effective(record, verifications)
        kind = f.get("kind")
        instrument = str(f.get("instrument", ""))
        if kind == "instrument":
            instruments.setdefault(instrument, {"instrument": instrument, "plates": []})["solar_radius_arcmin"] = float(
                f["solar_radius_arcmin"]
            )
        elif kind == "plate":
            plate = plates.setdefault((instrument, str(f["plate_id"])), {"plate_id": str(f["plate_id"]), "stars": []})
            for key in ("check_scale", "check_rotation", "check_scale_sigma"):
                if key in f:
                    plate[key] = float(f[key])
        elif kind == "star":
            plate = plates.setdefault((instrument, str(f["plate_id"])), {"plate_id": str(f["plate_id"]), "stars": []})
            missing = [k for k in _STAR_FIELDS if k not in f and k not in ("dx_arcsec", "dy_arcsec")]
            if missing:
                raise ValueError(f"record {record.record_id} ({record.locator}) lacks {missing}")
            plate["stars"].append(
                {
                    "star_id": str(f["star_id"]),
                    "record_id": record.record_id,
                    "locator": record.locator,
                    "transcriber": record.transcriber,
                    **{k: f.get(k) for k in _STAR_FIELDS},
                }
            )
    for (instrument, _), plate in sorted(plates.items()):
        instruments.setdefault(instrument, {"instrument": instrument, "plates": []})["plates"].append(plate)
    missing_radius = [i for i, s in instruments.items() if "solar_radius_arcmin" not in s]
    if missing_radius:
        raise ValueError(f"no solar radius transcribed for {missing_radius}")
    return {
        "schema": BUNDLE_SCHEMA,
        "instrument_sets": [instruments[k] for k in sorted(instruments)],
        "records": {r.record_id: r.locator for r in records},
        "verifications": [
            {"record_id": v.record_id, "verifier": v.verifier, "matches": v.matches} for v in verifications
        ],
        "provenance": {"kind": "transcription", **dict(provenance or {})},
    }

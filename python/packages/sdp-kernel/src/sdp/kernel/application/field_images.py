"""The images a field investigation read, as the evidence UI draws them (INV-020).

For each image product Retrieve fetched: what it is (from its envelope), the checks its envelope
passed or failed (INT-019), and every Gaia star the image-counterpart test placed on it, with the
pixel where its motion puts it, the pixel of its counterpart, whether the match was ambiguous, and
whether the star stands out at the profile's alpha. Read only from the set's stored stage artifacts,
so it is the same for every reader and needs no archive.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from sdp.kernel.application.media import JSON, decode_artifact
from sdp.kernel.domain.ids import ArtifactId
from sdp.kernel.domain.investigation import Investigation, InvestigationSet, Stage
from sdp.kernel.ports.storage import ArtifactStore

IMAGE = "image"
COUNTERPART_TEST = "image-counterpart"
#: Envelope fields a scientist reads first, in this order; the rest stay in the full envelope.
SUMMARY_FIELDS = (
    "mission",
    "instrument",
    "detector",
    "filter_or_grating",
    "programme_id",
    "observation_id",
    "calibration_level",
    "pipeline_version",
    "units",
    "dimensions",
    "size_bytes",
    "time",
    "access_status",
    "has_wcs",
)


def chance_p(d2: float | None) -> float:
    """The chance of an offset at least this large: χ² survival with two degrees of freedom, exp(-d²/2).
    A missing counterpart is 0, as the test scores it."""
    return 0.0 if d2 is None else math.exp(-d2 / 2.0)


BLOCK = 2880


def image_size(data: bytes) -> tuple[int, int] | None:
    """Width and height of a FITS file's science image, read from its header cards alone: the HDU
    named SCI, else the first with two axes. None when the bytes hold no such image."""
    at, first = 0, None
    while at + BLOCK <= len(data):
        cards: dict[str, str] = {}
        while at + BLOCK <= len(data):
            block = data[at : at + BLOCK].decode("ascii", "replace")
            at += BLOCK
            for i in range(0, BLOCK, 80):
                key, value = block[i : i + 8].strip(), block[i + 10 : i + 80].split("/")[0].strip()
                cards.setdefault(key, value.strip("' "))
            if "END" in cards:
                break
        try:
            axes = [int(cards.get(f"NAXIS{n}", "0")) for n in range(1, int(cards.get("NAXIS", "0")) + 1)]
            bits = abs(int(cards.get("BITPIX", "8")))
        except ValueError:
            return first
        if len(axes) == 2 and min(axes) > 0:
            if cards.get("EXTNAME") == "SCI":
                return axes[0], axes[1]
            first = first or (axes[0], axes[1])
        size = bits // 8 * math.prod(axes) if axes else 0
        at += -(-size // BLOCK) * BLOCK
    return first


def _stage(store: ArtifactStore, s: InvestigationSet, stage: Stage) -> Any:
    record = s.stages.get(stage)
    if record is None or record.artifact_id is None:
        return None
    return decode_artifact(store.get(record.artifact_id), JSON)


def _envelope(store: ArtifactStore, envelope_id: str | None) -> dict[str, Any] | None:
    if not envelope_id:
        return None
    doc: dict[str, Any] = decode_artifact(store.get(ArtifactId(envelope_id)), JSON)
    return doc


def field_images(
    store: ArtifactStore, s: InvestigationSet, investigations: Sequence[Investigation] = ()
) -> list[dict[str, Any]]:
    retrieved = _stage(store, s, Stage.RETRIEVE)
    if retrieved is None:
        return []
    images: dict[str, dict[str, Any]] = {}
    for bundle in retrieved["bundles"]:
        for p in bundle["products"]:
            if p["product_type"] != IMAGE or p["product"]["product_id"] in images:
                continue
            envelope = _envelope(store, p.get("envelope_artifact_id"))
            metadata: Mapping[str, Any] = envelope["metadata"] if envelope else {}
            validation = envelope.get("validation") if envelope else None
            size = image_size(store.get(ArtifactId(p["artifact_id"])))
            images[p["product"]["product_id"]] = {
                "product_id": p["product"]["product_id"],
                "width": size[0] if size else None,
                "height": size[1] if size else None,
                "archive": p["product"]["archive"],
                "artifact_id": p["artifact_id"],
                "raw_artifact_id": p.get("raw_artifact_id"),
                "envelope_artifact_id": p.get("envelope_artifact_id"),
                "read_from": p.get("read_from"),
                "summary": {k: metadata[k] for k in SUMMARY_FIELDS if k in metadata},
                "checks": validation["checks"] if validation else None,
                "passed": validation["passed"] if validation else None,
                "stars": [],
            }
    tested = _stage(store, s, Stage.TEST)
    alpha = float(s.profile.ranking.alpha)
    by_key = {i.candidate.object_key: i.investigation_id for i in investigations if i.candidate.object_key}
    for r in (tested or {}).get("results", []):
        if r["test_name"] != COUNTERPART_TEST:
            continue
        key = r["subject"]["object_key"]
        details = r["details"]
        for on in details.get("per_image", []):
            image = images.get(on["image"])
            if image is None:
                continue
            p = chance_p(on["d2"])
            image["stars"].append(
                {
                    "object_key": key,
                    "investigation_id": by_key.get(key),
                    "predicted_px": on["predicted_px"],
                    "counterpart_px": on["counterpart_px"],
                    "d2": on["d2"],
                    "p_value": p,
                    "flagged": p < alpha,
                    "ambiguous": on["ambiguous"],
                    # The competing hypotheses are kept for the image the result's details describe.
                    "counterparts": details["counterparts"] if details.get("image") == on["image"] else None,
                }
            )
    for image in images.values():
        image["stars"].sort(key=lambda star: (not star["flagged"], not star["ambiguous"], star["object_key"]))
    return sorted(images.values(), key=lambda i: str(i["product_id"]))

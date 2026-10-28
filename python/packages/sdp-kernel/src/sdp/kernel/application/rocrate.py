"""RO-Crate publication bundles (ADR-009): ro-crate-metadata.json plus the evidence files, zipped
deterministically so the same evidence always produces the same bundle bytes."""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Mapping
from typing import Any

from sdp.kernel.domain.canonical import sha256_id

_EPOCH = (1980, 1, 1, 0, 0, 0)


def build_crate_metadata(
    *,
    name: str,
    description: str,
    licence: str,
    files: Mapping[str, tuple[bytes, str, str]],
    authors: list[str],
    date_published: str,
    citations: list[str],
) -> dict[str, Any]:
    """RO-Crate 1.1 metadata. ``files`` maps path -> (bytes, media type, description)."""
    graph: list[dict[str, Any]] = [
        {
            "@id": "ro-crate-metadata.json",
            "@type": "CreativeWork",
            "conformsTo": {"@id": "https://w3id.org/ro/crate/1.1"},
            "about": {"@id": "./"},
        },
        {
            "@id": "./",
            "@type": "Dataset",
            "name": name,
            "description": description,
            "license": licence,
            "datePublished": date_published,
            "author": [{"@id": f"#author-{i}"} for i in range(len(authors))],
            "citation": citations,
            "hasPart": [{"@id": path} for path in sorted(files)],
        },
    ]
    for i, author in enumerate(authors):
        graph.append({"@id": f"#author-{i}", "@type": "Person", "name": author})
    for path in sorted(files):
        data, media_type, desc = files[path]
        graph.append(
            {
                "@id": path,
                "@type": "File",
                "encodingFormat": media_type,
                "contentSize": str(len(data)),
                "sha256": sha256_id(data).removeprefix("sha256:"),
                "description": desc,
            }
        )
    return {"@context": "https://w3id.org/ro/crate/1.1/context", "@graph": graph}


def zip_crate(metadata: Mapping[str, Any], files: Mapping[str, tuple[bytes, str, str]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        entries = {"ro-crate-metadata.json": json.dumps(metadata, indent=2, sort_keys=True).encode()}
        entries.update({path: data for path, (data, _m, _d) in files.items()})
        for path in sorted(entries):
            info = zipfile.ZipInfo(path, date_time=_EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, entries[path])
    return buf.getvalue()


def read_crate(bundle: bytes) -> tuple[dict[str, Any], dict[str, bytes]]:
    with zipfile.ZipFile(io.BytesIO(bundle)) as zf:
        files = {name: zf.read(name) for name in zf.namelist()}
    metadata = json.loads(files.pop("ro-crate-metadata.json"))
    return metadata, files


def verify_crate(bundle: bytes) -> list[str]:
    """Problems with a bundle: missing parts, size or checksum mismatches."""
    metadata, files = read_crate(bundle)
    problems: list[str] = []
    entities = {e["@id"]: e for e in metadata.get("@graph", [])}
    root = entities.get("./")
    if root is None:
        return ["no root dataset"]
    for part in root.get("hasPart", []):
        path = part["@id"]
        entity = entities.get(path)
        if path not in files or entity is None:
            problems.append(f"{path} listed but missing")
            continue
        if entity.get("sha256") != sha256_id(files[path]).removeprefix("sha256:"):
            problems.append(f"{path} checksum mismatch")
        if entity.get("contentSize") != str(len(files[path])):
            problems.append(f"{path} size mismatch")
    for path in files:
        if path not in entities:
            problems.append(f"{path} present but not described")
    return problems

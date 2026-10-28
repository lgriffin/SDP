"""Decoding stored artifacts into the values protocols receive."""

from __future__ import annotations

import io
import json
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from sdp.kernel.application import codec

JSON = "application/json"
PARQUET = "application/vnd.apache.parquet"
CSV = "text/csv"


def decode_artifact(data: bytes, media_type: str) -> Any:
    if media_type == JSON:
        return json.loads(data)
    if media_type == codec.MEDIA_TYPE:
        return codec.loads(data)
    if media_type == PARQUET:
        return pq.read_table(io.BytesIO(data))
    return data


def parquet_bytes(table: pa.Table) -> bytes:
    """Deterministic Parquet serialisation used for every raw part and curated artifact."""
    sink = io.BytesIO()
    pq.write_table(
        table,
        sink,
        compression="zstd",
        compression_level=3,
        version="2.6",
        write_statistics=True,
        use_dictionary=False,
    )
    return sink.getvalue()

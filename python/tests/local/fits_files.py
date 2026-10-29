"""Write small FITS files card by card, as a camera does: a primary header and some pixel bytes.
No astropy.io in tests (INV-011)."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def card(key: str, value: Any) -> str:
    if isinstance(value, bool):
        text = "T" if value else "F"
        return f"{key:<8}= {text:>20}"
    if isinstance(value, str):
        quoted = "'" + value.replace("'", "''") + "'"
        return f"{key:<8}= {quoted:<20}"
    return f"{key:<8}= {value!s:>20}"


def fits_bytes(header: dict[str, Any], data: bytes = b"", *, comments: bool = True) -> bytes:
    cards = [card(k, v) + (" / a comment" if comments and not isinstance(v, str) else "") for k, v in header.items()]
    cards.append("HISTORY made by the test")
    cards.append("END")
    text = "".join(c.ljust(80)[:80] for c in cards)
    head = text.encode("ascii")
    head += b" " * (-len(head) % 2880)
    return head + data + b"\0" * (-len(data) % 2880)


def raw_sub(**extra: Any) -> dict[str, Any]:
    """A Vespera Pro raw sub's header: 16-bit Bayer GBRG, EXPOSURE rather than EXPTIME."""
    return {
        "SIMPLE": True,
        "BITPIX": 16,
        "NAXIS": 2,
        "NAXIS1": 3536,
        "NAXIS2": 3536,
        "BZERO": 32768,
        "DATE-OBS": "2025-09-14T22:01:03.123",
        "EXPOSURE": 10.0,
        "GAIN": 200,
        "FILTER": "CLS",
        "OBJECT": "M31",
        "BAYERPAT": "GBRG",
        "TEMP": 21.5,
        **extra,
    }


def write(path: Path, header: dict[str, Any], data: bytes = b"\x01\x02" * 64) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(fits_bytes(header, data))
    return path

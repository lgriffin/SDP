"""Read a FITS primary header up to ``END`` and no further: the pixels are never read (#71).

A 25 MB raw sub's header is one or two 2,880-byte blocks, so indexing a 575 GB folder reads a few
tens of MB. Values are parsed as the FITS standard writes them (quoted strings, T/F, integers, reals);
anything else is kept as its text.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import IO, Any

BLOCK = 2880
CARD = 80
#: A header longer than this is not one SDP reads (a sane primary header is a few blocks)
MAX_BLOCKS = 64

_STRING = re.compile(r"^'((?:[^']|'')*)'")
_NUMBER = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([EeDd][+-]?\d+)?$")


class NotFits(ValueError):
    """The file does not start with a FITS primary header."""


def parse_value(text: str) -> Any:
    raw = text.strip()
    if raw.startswith("'"):
        match = _STRING.match(raw)
        return match[1].replace("''", "'").rstrip() if match else raw.strip("'").rstrip()
    value = raw.split("/", 1)[0].strip()
    if value == "T":
        return True
    if value == "F":
        return False
    if _NUMBER.match(value):
        number = value.replace("D", "E").replace("d", "e")
        if re.fullmatch(r"[+-]?\d+", number):
            return int(number)
        return float(number)
    return value or None


def read_header(handle: IO[bytes]) -> dict[str, Any]:
    """The primary header's keywords and values, from an open binary file positioned at its start."""
    header: dict[str, Any] = {}
    for block_number in range(MAX_BLOCKS):
        block = handle.read(BLOCK)
        if len(block) < BLOCK:
            raise NotFits("the header ends before its END card")
        if block_number == 0 and not block.startswith(b"SIMPLE  ="):
            raise NotFits("no SIMPLE card: not a FITS file")
        for start in range(0, BLOCK, CARD):
            card = block[start : start + CARD].decode("ascii", "replace")
            keyword = card[:8].strip()
            if keyword == "END":
                return header
            if card[8:10] != "= " or not keyword or keyword in ("COMMENT", "HISTORY"):
                continue
            header.setdefault(keyword, parse_value(card[10:]))
    raise NotFits(f"no END card in the first {MAX_BLOCKS} header blocks")


def read_header_file(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        return read_header(handle)


def header_size(head: bytes) -> int:
    """How many bytes the primary header takes, its padding included: where the pixels start."""
    if not head.startswith(b"SIMPLE  ="):
        raise NotFits("no SIMPLE card: not a FITS file")
    for block_number in range(MAX_BLOCKS):
        block = head[block_number * BLOCK : (block_number + 1) * BLOCK]
        if len(block) < BLOCK:
            break
        for start in range(0, BLOCK, CARD):
            if block[start : start + 8] == b"END     ":
                return (block_number + 1) * BLOCK
    raise NotFits("no END card in the header")

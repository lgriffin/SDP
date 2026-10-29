"""Read the structure of a VOTable product from bytes, in the shape ``read_hdus`` gives a FITS file:
one ``HduInfo`` per table, with its ``PARAM`` values and time system as the header and its columns
with their units and UCDs (Integration v0.4.0 §3). An archive that serves products as VOTables (Gaia's
DataLink, IRSA's TAP) is then enveloped and validated by the same rules as a FITS one.

The bytes are checked before any parser sees them (NFR-009): bounded in size, XML, and free of a DTD,
the rules ``sdp.adapters.archives.untrusted`` applies to archive answers (adapters may not share code, §4).
"""

from __future__ import annotations

import io
import re
import warnings
from collections.abc import Iterable
from typing import Any

import numpy as np
from astropy.io.votable import parse
from astropy.io.votable.exceptions import VOWarning
from astropy.io.votable.tree import VOTableFile
from astropy.table import Table

from sdp.adapters.products.fits import HduInfo
from sdp.kernel.domain.integration import PayloadExtension

VOTABLE_MEDIA_TYPES = ("application/x-votable+xml", "text/xml", "application/xml")
#: A product larger than this is not a product SDP reads whole as a VOTable.
MAX_PRODUCT_BYTES = 64 * 1024**2
_DECLARATION = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)
#: A column's role from the first word of its UCD; times, ids, positions and wavelengths are axes.
UCD_ROLES = {
    "stat.error": "error",
    "meta.code.status": "quality",
    "meta.code.qual": "quality",
    "phot.flux": "data",
    "phot.mag": "data",
    "spect": "data",
}


def check_votable(data: bytes) -> None:
    """Refuse what is not a plausible VOTable before parsing: too large, not XML, or declaring a DTD
    (VOTable needs none, and a DTD is how entity-expansion attacks arrive)."""
    if len(data) > MAX_PRODUCT_BYTES:
        raise ValueError(f"a VOTable product of {len(data)} bytes exceeds the {MAX_PRODUCT_BYTES}-byte limit")
    if not data.lstrip(b"\xef\xbb\xbf \t\r\n").startswith((b"<?xml", b"<VOTABLE")):
        raise ValueError("not an XML VOTable document")
    if _DECLARATION.search(data):
        raise ValueError("a VOTable declares no DTD; refusing one that does")


def _document(data: bytes) -> VOTableFile:
    check_votable(data)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", VOWarning)  # archives' VOTables are rarely to the letter
            return parse(io.BytesIO(data))
    except Exception as exc:  # astropy raises a zoo of parser errors for malformed XML
        raise ValueError(f"not a readable VOTable: {exc}") from exc


def parse_table(data: bytes) -> Table:
    """The first table's rows, after the same checks ``read_tables`` makes."""
    tables = list(_document(data).iter_tables())
    if not tables:
        raise ValueError("not a readable VOTable: it holds no table")
    return tables[0].to_table(use_names_over_ids=True)


def numbers(table: Table, name: str) -> np.ndarray:
    """A numeric column with masked cells as NaN."""
    return np.ma.filled(np.ma.asarray(table[name], dtype=float), np.nan)


def _plain(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, bool | int | float | str) or value is None:
        return value
    return str(value)


def read_tables(data: bytes) -> tuple[HduInfo, ...]:
    """Every table's structure, in document order. Raises ``ValueError`` for bytes that are not a
    VOTable, carry a DTD, or cannot be parsed."""
    document = _document(data)
    systems: dict[str, Any] = {}
    resources = list(document.resources)
    while resources:
        resource = resources.pop(0)
        resources.extend(resource.resources)
        for timesys in resource.time_systems:
            systems = {
                "TIMESYS": timesys.timescale,
                "TIMEREFPOS": timesys.refposition,
                "TIMEORIGIN": _plain(timesys.timeorigin),
            }
    out = []
    for i, table in enumerate(document.iter_tables()):
        header = {str(p.name): _plain(p.value) for p in table.params}
        header.update({k: v for k, v in systems.items() if v is not None})
        columns = tuple(str(f.name).upper() for f in table.fields)
        units = {str(f.name).upper(): f.unit.to_string() for f in table.fields if f.unit is not None}
        ucds = {str(f.name).upper(): str(f.ucd) for f in table.fields if f.ucd}
        name = str(table.name or table.ID or f"TABLE{i}").upper()
        out.append(HduInfo(i, name, "table", header, (len(table.array),), columns, units, ucds))
    if not out:
        raise ValueError("not a readable VOTable: it holds no table")
    return tuple(out)


def column_roles(table: HduInfo, quality: Iterable[str] = ()) -> tuple[PayloadExtension, ...]:
    """Each column's role from its UCD (``UCD_ROLES``), and ``quality`` for the named columns a
    dialect knows are quality flags though their UCD does not say so; other columns stay unlisted."""
    flags = set(quality)
    out = []
    for column in table.columns:
        role = "quality" if column in flags else UCD_ROLES.get(table.column_ucds.get(column, "").split(";")[0])
        if role is not None:
            out.append(PayloadExtension(f"{table.name}:{column}", role, table.column_units.get(column)))
    return tuple(out)

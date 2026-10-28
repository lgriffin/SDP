"""VOTable responses to pyarrow, and TAP error documents to exceptions."""

from __future__ import annotations

import io
import warnings

import numpy as np
import pyarrow as pa
from astropy.io import votable
from astropy.io.votable.exceptions import VOWarning


class TapQueryError(RuntimeError):
    pass


def _column(values: np.ndarray, mask: np.ndarray | None) -> pa.Array:
    if values.dtype.kind == "S":
        values = np.char.decode(values, "utf-8")
    if values.dtype.kind == "O":
        items = [v.decode("utf-8") if isinstance(v, bytes) else v for v in values.tolist()]
        if mask is not None:
            items = [None if m else v for v, m in zip(items, mask.tolist(), strict=True)]
        return pa.array(items)
    if values.dtype.kind == "U":
        values = values.astype(object)
        return _column(values, mask)
    return pa.array(values, mask=mask if mask is not None and mask.any() else None)


def parse(body: bytes) -> pa.Table:
    """The first table of a VOTable document; a QUERY_STATUS of ERROR raises TapQueryError."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", VOWarning)
        document = votable.parse(io.BytesIO(body), verify="ignore")
    for resource in document.resources:
        for info in resource.infos:
            if info.name == "QUERY_STATUS" and str(info.value).upper() == "ERROR":
                raise TapQueryError((info.content or "TAP query failed").strip())
    table = document.get_first_table()
    array = table.array
    columns: dict[str, pa.Array] = {}
    for f in table.fields:
        name = f.name or f.ID
        data = np.ma.getdata(array[f.ID or f.name])
        mask = np.ma.getmaskarray(array[f.ID or f.name])
        columns[name] = _column(np.asarray(data), np.asarray(mask) if mask.ndim == 1 else None)
    return pa.table(columns)


def to_votable(table: pa.Table) -> bytes:
    """A pyarrow table as a VOTable document, nulls as masked values (test servers and fixture tooling)."""
    from astropy.table import MaskedColumn, Table

    columns = []
    for name in table.column_names:
        col = table.column(name).combine_chunks()
        mask = np.asarray(col.is_null().to_numpy(zero_copy_only=False), dtype=bool)
        if pa.types.is_string(col.type):
            data = np.asarray([v if v is not None else "" for v in col.to_pylist()], dtype=object)
        elif pa.types.is_boolean(col.type):
            data = np.asarray([bool(v) for v in col.to_pylist()])
        else:
            data = col.fill_null(0).to_numpy(zero_copy_only=False)
        columns.append(MaskedColumn(data, name=name, mask=mask))
    out = io.BytesIO()
    votable.from_table(Table(columns)).to_xml(out)
    return out.getvalue()

"""Bulk copies of a whole catalogue release from its publisher's own files (#72).

ESA publishes Gaia DR3 ``gaia_source`` as about 3,400 gzipped ECSV files, each one a range of HEALPix
cells, listed with their MD5 sums in ``_MD5SUM.txt``. ESA asks for these, not TAP, when someone wants the
whole catalogue. ``BulkSweep`` downloads each file, checks its MD5, keeps only the declared columns of the
source (the same names and types its TAP dialect reads) and stores them as one Parquet artifact under
``bulk/<source>/<release>/<file>.parquet``, beside a JSON record of where it came from.

A file already stored is skipped, so a sweep that stops (Ctrl+C, a crash, a full disk) picks up where it
left off when run again. Before each file the store's free space is checked against the same floor that
refuses acquisitions (DSI-010). Nothing is ever written outside the store and its work folder.

A subset (``subsets/*.yaml``) is a second copy from the same files: the parent's columns plus a few more,
kept only for rows where one of them has a value. ``rvs`` is the Gaia DR3 stars with a radial velocity
(DSI-021); it lives under ``bulk/<source>/<release>/<subset>/`` and is read with its own gateway.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import threading
import time
import urllib.request
from collections.abc import Callable, Iterable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from importlib.resources import files as package_files
from pathlib import Path
from typing import IO, Any

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pacsv
import pyarrow.parquet as pq
import yaml

from sdp.adapters.archives import adql
from sdp.kernel.application.acquisition import CHECKSUM_METADATA_KEY, batch_checksum
from sdp.kernel.domain.archive import (
    AcquisitionHandle,
    Box,
    CatalogueQuery,
    ColumnDescriptor,
    Cone,
    SourceDescriptor,
    idempotency_key,
)
from sdp.kernel.domain.errors import StorageExhausted
from sdp.kernel.ports.storage import ArtifactStore

log = logging.getLogger("sdp.adapters.bulk")

GAIA_DR3_BULK = "https://cdn.gea.esac.esa.int/Gaia/gdr3/gaia_source/"
MD5SUM = "_MD5SUM.txt"
PARQUET = "application/vnd.apache.parquet"
JSON = "application/json"

#: Declared datatypes (the TAP source files) as Arrow types
ARROW_TYPES: dict[str, pa.DataType] = {
    "long": pa.int64(),
    "int": pa.int32(),
    "short": pa.int16(),
    "double": pa.float64(),
    "float": pa.float32(),
    "boolean": pa.bool_(),
    "char": pa.string(),
}

Opener = Callable[[str], IO[bytes]]


def _urlopen(url: str) -> IO[bytes]:
    response: IO[bytes] = urllib.request.urlopen(url, timeout=120)
    return response


@dataclass(frozen=True)
class BulkFile:
    name: str
    md5: str

    @property
    def stem(self) -> str:
        return self.name.removesuffix(".gz").removesuffix(".csv")


@dataclass(frozen=True)
class FileResult:
    name: str
    status: str  # "stored", "reused" or "failed"
    rows: int = 0
    parquet_bytes: int = 0
    download_bytes: int = 0
    seconds: float = 0.0
    error: str | None = None

    def to_document(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


def parse_md5sum(text: str) -> list[BulkFile]:
    """``<md5>  <name>`` lines; only the data files (``.csv.gz``), in name order."""
    files = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].endswith(".csv.gz"):
            files.append(BulkFile(parts[1].lstrip("*"), parts[0].lower()))
    return sorted(files, key=lambda f: f.name)


@dataclass(frozen=True)
class BulkSubset:
    """Extra columns of a parent source, kept only for the rows where ``keep_rows_with`` has a value."""

    name: str
    parent: str
    keep_rows_with: str
    columns: tuple[ColumnDescriptor, ...]
    description: str = ""

    def __post_init__(self) -> None:
        if self.keep_rows_with not in {c.name for c in self.columns}:
            raise ValueError(f"subset {self.name}: keep_rows_with {self.keep_rows_with!r} is not one of its columns")

    def descriptor(self, parent: SourceDescriptor) -> SourceDescriptor:
        """The parent's descriptor with this subset's columns after its own."""
        if parent.source_id != self.parent:
            raise ValueError(f"subset {self.name} extends {self.parent}, not {parent.source_id}")
        clash = {c.name for c in self.columns} & set(parent.column_names())
        if clash:
            raise ValueError(f"subset {self.name}: {sorted(clash)} are already columns of {parent.source_id}")
        return replace(parent, columns=parent.columns + self.columns)

    @classmethod
    def from_document(cls, doc: dict[str, Any]) -> BulkSubset:
        return cls(
            name=str(doc["name"]),
            parent=str(doc["parent"]),
            keep_rows_with=str(doc["keep_rows_with"]),
            columns=tuple(
                ColumnDescriptor(str(c["name"]), str(c["datatype"]), c.get("unit"), str(c.get("description", "")))
                for c in doc["columns"]
            ),
            description=str(doc.get("description", "")),
        )


def load_subsets(directory: Path | None = None) -> dict[str, BulkSubset]:
    root = Path(directory) if directory else Path(str(package_files("sdp.adapters.archives").joinpath("subsets")))
    out: dict[str, BulkSubset] = {}
    for path in sorted(root.glob("*.yaml")):
        subset = BulkSubset.from_document(yaml.safe_load(path.read_text("utf-8")))
        if subset.name in out:
            raise ValueError(f"{path.name}: duplicate subset {subset.name}")
        out[subset.name] = subset
    return out


def bulk_prefix(descriptor: SourceDescriptor, subset: BulkSubset | None = None) -> str:
    base = f"bulk/{descriptor.source_id}/{descriptor.release_label.replace(':', '_')}"
    return f"{base}/{subset.name}" if subset is not None else base


def ecsv_to_table(path: Path, descriptor: SourceDescriptor, columns: Sequence[str]) -> pa.Table:
    """Read the declared columns of a gzipped ECSV file (its ``#`` header lines skipped) with their types."""
    with gzip.open(path, "rt", encoding="utf-8") as text:
        skip = 0
        for line in text:
            if not line.startswith("#"):
                break
            skip += 1
    types = {c.name: ARROW_TYPES.get(c.datatype, pa.string()) for c in descriptor.columns if c.name in columns}
    with pa.input_stream(str(path), compression="gzip") as stream:
        table = pacsv.read_csv(
            stream,
            read_options=pacsv.ReadOptions(skip_rows=skip, block_size=64 << 20),
            convert_options=pacsv.ConvertOptions(
                include_columns=list(columns),
                column_types=types,
                null_values=["null", "", "NaN", "nan"],
                strings_can_be_null=True,
                true_values=["True", "true", "1", "T"],
                false_values=["False", "false", "0", "F"],
            ),
        )
    return table.select(list(columns))


@dataclass
class BulkSweep:
    store: ArtifactStore
    descriptor: SourceDescriptor
    work_dir: Path
    columns: tuple[str, ...]
    base_url: str = GAIA_DR3_BULK
    min_free_bytes: int = 0
    opener: Opener = _urlopen
    attempts: int = 4
    sleep: Callable[[float], None] = time.sleep
    #: a subset sweep: ``descriptor`` is the parent's, the subset's columns are added and rows filtered
    subset: BulkSubset | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        if self.subset is not None:
            self.descriptor = self.subset.descriptor(self.descriptor)
            self.columns = tuple(dict.fromkeys((*self.columns, *(c.name for c in self.subset.columns))))
        unknown = set(self.columns) - set(self.descriptor.column_names())
        if unknown:
            raise ValueError(f"{self.descriptor.source_id}: columns {sorted(unknown)} are not declared")

    # -- layout -------------------------------------------------------------------------------

    @property
    def prefix(self) -> str:
        return bulk_prefix(self.descriptor, self.subset)

    def parquet_path(self, f: BulkFile) -> str:
        return f"{self.prefix}/{f.stem}.parquet"

    def record_path(self, f: BulkFile) -> str:
        return f"{self.prefix}/{f.stem}.json"

    # -- the sweep ----------------------------------------------------------------------------

    def files(self) -> list[BulkFile]:
        """The publisher's file list, kept in the store too: a reader can then tell a complete copy."""
        with self.opener(self.base_url + MD5SUM) as response:
            text = response.read()
        self.store.put(text, "text/plain", logical_path=f"{self.prefix}/{MD5SUM}")
        return parse_md5sum(text.decode("utf-8"))

    def status(self, files: Sequence[BulkFile]) -> dict[str, Any]:
        done = [f for f in files if self.store.resolve(self.parquet_path(f)) is not None]
        rows = parquet_bytes = 0
        for f in done:
            record = self.store.resolve(self.record_path(f))
            if record is not None:
                doc = json.loads(self.store.get(record))
                rows += int(doc.get("rows", 0))
                parquet_bytes += int(doc.get("parquet_bytes", 0))
        return {
            "source": self.descriptor.source_id,
            "release": self.descriptor.release_label,
            **({"subset": self.subset.name} if self.subset is not None else {}),
            "files": len(files),
            "done": len(done),
            "rows": rows,
            "parquet_bytes": parquet_bytes,
            "free_bytes": self.store.free_bytes(),
        }

    def run(
        self,
        files: Iterable[BulkFile],
        *,
        workers: int = 1,
        progress: Callable[[FileResult], None] | None = None,
    ) -> list[FileResult]:
        """Store every file not stored yet. A failed file is reported and skipped; a full disk stops the
        sweep (StorageExhausted) after the files in flight finish."""
        self.work_dir.mkdir(parents=True, exist_ok=True)
        todo = list(files)
        results: list[FileResult] = []
        stop = threading.Event()

        def one(f: BulkFile) -> FileResult | None:
            if stop.is_set():
                return None
            try:
                result = self.store_file(f)
            except StorageExhausted:
                stop.set()
                raise
            if progress is not None:
                with self._lock:
                    progress(result)
            return result

        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = [pool.submit(one, f) for f in todo]
            exhausted: StorageExhausted | None = None
            for future in futures:
                try:
                    done = future.result()
                except StorageExhausted as exc:
                    exhausted = exhausted or exc
                    continue
                if done is not None:
                    results.append(done)
        if exhausted is not None:
            raise exhausted
        return results

    def store_file(self, f: BulkFile) -> FileResult:
        if self.store.resolve(self.parquet_path(f)) is not None:
            return FileResult(f.name, "reused")
        free = self.store.free_bytes()
        if free is not None and free < self.min_free_bytes:
            raise StorageExhausted(
                f"{free} bytes free, below the {self.min_free_bytes} byte floor (DSI-010); the sweep stopped "
                f"before {f.name}. Free some space or point SDP at a bigger drive, then run it again to resume."
            )
        started = time.monotonic()
        download = self.work_dir / f.name
        try:
            size = self._download(f, download)
            table = ecsv_to_table(download, self.descriptor, self.columns)
            read_rows = table.num_rows
            if self.subset is not None:
                table = table.filter(pc.is_valid(table.column(self.subset.keep_rows_with)))
            sink = pa.BufferOutputStream()
            pq.write_table(table, sink, compression="zstd")
            data = sink.getvalue().to_pybytes()
            artifact = self.store.put(data, PARQUET)
            record = {
                "file": f.name,
                "url": self.base_url + f.name,
                "md5": f.md5,
                "source": self.descriptor.source_id,
                "release": self.descriptor.release_label,
                "licence": self.descriptor.licence,
                "citation": self.descriptor.citation,
                "columns": list(self.columns),
                **(
                    {"subset": self.subset.name, "kept_rows_with": self.subset.keep_rows_with, "file_rows": read_rows}
                    if self.subset is not None
                    else {}
                ),
                "rows": table.num_rows,
                "bounds": sky_bounds(table),
                "parquet_bytes": len(data),
                "download_bytes": size,
                "parquet_artifact": artifact.value,
                "fetched_at": datetime.now(UTC).isoformat(),
            }
            self.store.put(
                json.dumps(record, indent=2, sort_keys=True).encode(), JSON, logical_path=self.record_path(f)
            )
            # the Parquet path last: it is what marks the file done
            self.store.link(self.parquet_path(f), artifact)
        except StorageExhausted:
            raise
        except Exception as exc:  # one bad file never stops the sweep; it is reported and retried next run
            log.warning("bulk file failed", extra={"file": f.name, "error": str(exc)})
            return FileResult(f.name, "failed", seconds=time.monotonic() - started, error=str(exc))
        finally:
            download.unlink(missing_ok=True)
        return FileResult(f.name, "stored", table.num_rows, len(data), size, time.monotonic() - started)

    def _download(self, f: BulkFile, target: Path) -> int:
        last: Exception | None = None
        for attempt in range(1, self.attempts + 1):
            digest = hashlib.md5(usedforsecurity=False)
            try:
                with self.opener(self.base_url + f.name) as response, target.open("wb") as out:
                    while chunk := response.read(1 << 20):
                        digest.update(chunk)
                        out.write(chunk)
                if digest.hexdigest() != f.md5:
                    raise OSError(f"{f.name}: MD5 {digest.hexdigest()} does not match the published {f.md5}")
                return target.stat().st_size
            except OSError as exc:
                last = exc
                target.unlink(missing_ok=True)
                if attempt < self.attempts:
                    self.sleep(2.0 * 2 ** (attempt - 1))
        assert last is not None
        raise last


def sky_bounds(table: pa.Table) -> dict[str, float] | None:
    """The RA/Dec box a file's stars lie in, so a field reads only the files it overlaps. ``ra_min`` above
    ``ra_max`` means the box runs across RA 0/360."""
    if table.num_rows == 0 or "ra" not in table.column_names or "dec" not in table.column_names:
        return None
    ra = np.asarray(table.column("ra").to_numpy(zero_copy_only=False), dtype=float) % 360.0
    dec = np.asarray(table.column("dec").to_numpy(zero_copy_only=False), dtype=float)
    ra, dec = ra[np.isfinite(ra) & np.isfinite(dec)], dec[np.isfinite(ra) & np.isfinite(dec)]
    if ra.size == 0:
        return None
    ordered = np.sort(ra)
    gaps = np.diff(np.concatenate([ordered, [ordered[0] + 360.0]]))
    widest = int(np.argmax(gaps))
    # the RA range is the circle minus its widest empty gap
    ra_min, ra_max = float(ordered[(widest + 1) % ordered.size]), float(ordered[widest])
    return {"ra_min": ra_min, "ra_max": ra_max, "dec_min": float(dec.min()), "dec_max": float(dec.max())}


def select_files(files: Sequence[BulkFile], first: int | None, count: int | None) -> list[BulkFile]:
    """Files ``first`` .. ``first + count`` in name order (both optional): for a trial run or a share of the sky."""
    start = first or 0
    return list(files[start : start + count] if count is not None else files[start:])


# -- reading the copy back ------------------------------------------------------------------------


def _spans(lo: float, hi: float) -> list[tuple[float, float]]:
    """An RA interval as pieces inside [0, 360]; ``lo`` above ``hi`` means it runs across 0/360."""
    return [(lo, hi)] if lo <= hi else [(lo, 360.0), (0.0, hi)]


def _region_box(region: Cone | Box) -> tuple[tuple[float, float] | None, float, float]:
    """The RA interval (None for every RA, else folded into [0, 360) and possibly wrapping) and Dec
    limits of a box holding the region."""
    if isinstance(region, Cone):
        ra, dec_min, dec_max = adql.cone_bounds(region)
    else:
        ra, dec_min, dec_max = (region.ra_min_deg, region.ra_max_deg), region.dec_min_deg, region.dec_max_deg
    if ra is None or ra[1] - ra[0] >= 360.0:
        return None, dec_min, dec_max
    return (ra[0] % 360.0, ra[1] % 360.0), dec_min, dec_max


def _overlaps(bounds: dict[str, float], box: tuple[tuple[float, float] | None, float, float]) -> bool:
    """Whether a file's sky box can hold a star of the region's box."""
    ra, dec_lo, dec_hi = box
    if bounds["dec_max"] < dec_lo or bounds["dec_min"] > dec_hi:
        return False
    if ra is None:
        return True
    return any(
        a_lo <= b_hi and b_lo <= a_hi
        for a_lo, a_hi in _spans(bounds["ra_min"], bounds["ra_max"])
        for b_lo, b_hi in _spans(*ra)
    )


_ARROW_OPS: dict[str, Callable[[Any, Any], Any]] = {
    "<": pc.less,
    "<=": pc.less_equal,
    ">": pc.greater,
    ">=": pc.greater_equal,
    "=": pc.equal,
    "!=": pc.not_equal,
}


def _mask(table: pa.Table, q: CatalogueQuery, ra_col: str, dec_col: str) -> pa.ChunkedArray:
    """The rows the TAP query would return: inside the region and passing every filter."""
    ra = np.asarray(table.column(ra_col).to_numpy(zero_copy_only=False), dtype=float)
    dec = np.asarray(table.column(dec_col).to_numpy(zero_copy_only=False), dtype=float)
    region = q.region
    if isinstance(region, Cone):
        # haversine separation, as CONTAINS(POINT, CIRCLE) measures it
        r1, d1 = np.radians(ra), np.radians(dec)
        r2, d2 = np.radians(region.ra_deg), np.radians(region.dec_deg)
        a = np.sin((d1 - d2) / 2) ** 2 + np.cos(d1) * np.cos(d2) * np.sin((r1 - r2) / 2) ** 2
        inside = np.degrees(2 * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))) <= region.radius_deg
    else:  # plain BETWEENs, as the ADQL box is: no RA wrap
        inside = (ra >= region.ra_min_deg) & (ra <= region.ra_max_deg)
        inside &= (dec >= region.dec_min_deg) & (dec <= region.dec_max_deg)
    mask = pa.chunked_array([pa.array(inside)])
    for f in q.filters:
        column = table.column(f.column)
        test = pc.is_valid(column) if f.op == "is_not_null" else _ARROW_OPS[f.op](column, f.value)
        mask = pc.and_kleene(mask, pc.fill_null(test, False))
    return mask


@dataclass
class BulkCatalogueGateway:
    """ArchiveGateway over a complete bulk copy (``BulkSweep``): the same descriptor, queries and batches
    as the TAP gateway, read from the artifact store with no network call (#72, DSI-014)."""

    store: ArtifactStore
    descriptor: SourceDescriptor
    ra_column: str = "ra"
    dec_column: str = "dec"
    key_column: str = "source_id"
    #: read a subset's copy instead (DSI-021): ``descriptor`` is the parent's, and gains the subset's columns
    subset: BulkSubset | None = None

    def __post_init__(self) -> None:
        if self.subset is not None:
            self.descriptor = self.subset.descriptor(self.descriptor)

    @property
    def prefix(self) -> str:
        return bulk_prefix(self.descriptor, self.subset)

    def files(self) -> list[BulkFile]:
        listing = self.store.resolve(f"{self.prefix}/{MD5SUM}")
        return parse_md5sum(self.store.get(listing).decode("utf-8")) if listing is not None else []

    def missing(self) -> list[BulkFile]:
        """Files of the publisher's list not stored yet; a copy with any missing is not read."""
        return [f for f in self.files() if self.store.resolve(f"{self.prefix}/{f.stem}.parquet") is None]

    def complete(self) -> bool:
        return bool(self.files()) and not self.missing()

    def describe(self, source_id: str) -> SourceDescriptor:
        if source_id != self.descriptor.source_id:
            raise KeyError(f"unknown source {source_id!r}; configured: [{self.descriptor.source_id!r}]")
        return self.descriptor

    def query(self, q: CatalogueQuery) -> AcquisitionHandle:
        if q.release_label != self.descriptor.release_label:
            raise ValueError(f"{q.source_id} serves {self.descriptor.release_label}, not {q.release_label}")
        unknown = set(q.columns) - set(self.descriptor.column_names())
        if unknown:
            raise ValueError(f"{q.source_id}: columns {sorted(unknown)} are not in the declared column list")
        # the same text as the TAP gateway, marked: a bulk read is never mistaken for an archive call
        rendered = "BULK " + adql.render(
            q,
            table=self.descriptor.table,
            ra=self.ra_column,
            dec=self.dec_column,
            order_by=q.order_by or self.key_column,
            top=q.row_cap,
        )
        return AcquisitionHandle(q, rendered, idempotency_key(rendered, q.release_label, q.columns), self.descriptor)

    def _read(self, q: CatalogueQuery) -> pa.Table:
        missing = self.missing()
        label = f" ({self.subset.name} subset)" if self.subset is not None else ""
        flag = f" --subset {self.subset.name}" if self.subset is not None else ""
        if not self.files() or missing:
            raise ValueError(
                f"the bulk copy of {self.descriptor.release_label}{label} is incomplete ({len(missing)} files "
                f"missing); finish `sdp archive bulk{flag}` first"
            )
        box = _region_box(q.region)
        needed = sorted({*q.columns, self.ra_column, self.dec_column, self.key_column, *(f.column for f in q.filters)})
        parts: list[pa.Table] = []
        for f in self.files():
            record = self.store.resolve(f"{self.prefix}/{f.stem}.json")
            bounds = json.loads(self.store.get(record)).get("bounds") if record is not None else None
            if record is not None and (bounds is None or not _overlaps(bounds, box)):
                continue  # an empty file, or one wholly outside the region
            artifact = self.store.resolve(f"{self.prefix}/{f.stem}.parquet")
            assert artifact is not None
            table = pq.read_table(pa.BufferReader(self.store.get(artifact)), columns=needed)
            kept = table.filter(_mask(table, q, self.ra_column, self.dec_column))
            if kept.num_rows:
                parts.append(kept)
        if not parts:
            types = {c.name: ARROW_TYPES.get(c.datatype, pa.string()) for c in self.descriptor.columns}
            return pa.schema([(c, types[c]) for c in q.columns]).empty_table()
        whole = pa.concat_tables(parts).sort_by(q.order_by or self.key_column)
        return whole.slice(0, q.row_cap).select(list(q.columns))

    def fetch(self, handle: AcquisitionHandle) -> Iterator[pa.RecordBatch]:
        q = handle.query
        whole = self._read(q)
        part = handle.resume_from_part
        while part * q.chunk_rows < q.row_cap:
            chunk = whole.slice(part * q.chunk_rows, q.chunk_rows).combine_chunks()
            batches = chunk.to_batches()
            batch = batches[0] if batches else pa.RecordBatch.from_pylist([], schema=chunk.schema)
            if batch.num_rows == 0 and part > handle.resume_from_part:
                return
            yield batch.replace_schema_metadata({CHECKSUM_METADATA_KEY: batch_checksum(batch).encode()})
            if batch.num_rows < q.chunk_rows:
                return
            part += 1

"""Bulk copies of a whole catalogue release from its publisher's own files (#72).

ESA publishes Gaia DR3 ``gaia_source`` as about 3,400 gzipped ECSV files, each one a range of HEALPix
cells, listed with their MD5 sums in ``_MD5SUM.txt``. ESA asks for these, not TAP, when someone wants the
whole catalogue. ``BulkSweep`` downloads each file, checks its MD5, keeps only the declared columns of the
source (the same names and types its TAP dialect reads) and stores them as one Parquet artifact under
``bulk/<source>/<release>/<file>.parquet``, beside a JSON record of where it came from.

A file already stored is skipped, so a sweep that stops (Ctrl+C, a crash, a full disk) picks up where it
left off when run again. Before each file the store's free space is checked against the same floor that
refuses acquisitions (DSI-010). Nothing is ever written outside the store and its work folder.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import threading
import time
import urllib.request
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

from sdp.kernel.domain.archive import SourceDescriptor
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
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        unknown = set(self.columns) - set(self.descriptor.column_names())
        if unknown:
            raise ValueError(f"{self.descriptor.source_id}: columns {sorted(unknown)} are not declared")

    # -- layout -------------------------------------------------------------------------------

    @property
    def prefix(self) -> str:
        return f"bulk/{self.descriptor.source_id}/{self.descriptor.release_label.replace(':', '_')}"

    def parquet_path(self, f: BulkFile) -> str:
        return f"{self.prefix}/{f.stem}.parquet"

    def record_path(self, f: BulkFile) -> str:
        return f"{self.prefix}/{f.stem}.json"

    # -- the sweep ----------------------------------------------------------------------------

    def files(self) -> list[BulkFile]:
        with self.opener(self.base_url + MD5SUM) as response:
            return parse_md5sum(response.read().decode("utf-8"))

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
                "rows": table.num_rows,
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


def select_files(files: Sequence[BulkFile], first: int | None, count: int | None) -> list[BulkFile]:
    """Files ``first`` .. ``first + count`` in name order (both optional): for a trial run or a share of the sky."""
    start = first or 0
    return list(files[start : start + count] if count is not None else files[start:])

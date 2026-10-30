"""Admission of the local frames adapter on the person's own drive (INT-002, INT-016, ADR-021, #71).

An archive's adapter is admitted per product type once it passes four kinds of conformance. The frames
here live only on the person's drive, so the four kinds run there, with ``sdp local certify``:

- contract: the gateway answers a search with its metadata, the same way twice, fetches the bytes it
  names and curates them to the canonical form;
- operational: the bytes fetched are the bytes that were solved (their SHA-256), only current solutions
  are offered, nothing outside the source is served, and the frames are not touched;
- semantic: what SDP reads from a frame matches what the person wrote down for it in
  ``reference.yaml`` from their own log (target, kind, date, exposure, filter, pointing), checked by
  them in a separate deliberate pass (``status: verified``);
- scientific: the solutions fit Gaia within the solver's limits and an independent implementation
  (astropy, reading the ``.wcs`` sidecar) puts the corners where SDP does; the light curves' steady
  stars scatter no more than ``MAX_SCATTER_MAG``.

Each product type's result is an ``AdapterCompatibilityRecord`` in ``<data>/compatibility/<source>/``.
The source's descriptor claims a product type only while a passing record for ``local-frames`` at this
version stands and the frames it was run on are unchanged (``admitted``).
"""

from __future__ import annotations

import hashlib
import json
import math
import warnings
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from importlib import metadata
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq
import yaml

from sdp.adapters.local.catalogue import SOLVED, Catalogue
from sdp.adapters.local.gateway import CURVE_PREFIX, IMAGE, LocalFrameDialect, LocalImageGateway
from sdp.adapters.local.lightcurves import LightCurves, file_stem, stars_files
from sdp.adapters.local.quality import POOR
from sdp.adapters.local.solver import SolverConfig, pixel_to_sky
from sdp.adapters.local.solving import LocalSolver, usable_name
from sdp.adapters.local.sources import LocalSource
from sdp.kernel.domain.integration import (
    AdapterCompatibilityRecord,
    ConformanceKind,
    ConformanceResult,
    ProductType,
)
from sdp.kernel.domain.investigation import ObservationCriteria, ProductRef
from sdp.kernel.ports.investigate import ObservationTarget
from sdp.kernel.science.products import LIGHTCURVE, parse_lightcurve

ADAPTER, ADAPTER_VERSION = "local-frames", "1.0.0"
REFERENCE = "reference.yaml"
VERIFIED = "verified"
#: how far the date written down may be from the frame's DATE-OBS (a log is kept to the minute)
DATE_SECONDS = 120.0
EXPOSURE_SECONDS = 0.5
#: astropy and SDP must put a frame's corners this close together
INDEPENDENT_ARCSEC = 0.05
#: the steadiest light-curve stars (the brightest G band measured) must scatter less than this
MAX_SCATTER_MAG = 0.05
MIN_CURVE_STARS = 5
MJD_UNIX = 40587.0
UPSTREAM = ("astropy", "numpy", "photutils", "scipy")


@dataclass
class Check:
    """One conformance kind for one product type: passed when nothing failed and something was checked."""

    kind: ConformanceKind
    passed_checks: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    def ok(self, what: str) -> None:
        self.passed_checks.append(what)

    def fail(self, what: str) -> None:
        self.failures.append(what)

    def expect(self, condition: bool, what: str, otherwise: str) -> bool:
        (self.ok if condition else self.fail)(what if condition else otherwise)
        return condition

    @property
    def passed(self) -> bool:
        return bool(self.passed_checks) and not self.failures

    def to_document(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "passed": self.passed, "checked": self.passed_checks, "failed": self.failures}


def _checks() -> dict[ConformanceKind, Check]:
    return {k: Check(k) for k in ConformanceKind}


def reference_template(rows: Iterable[dict[str, Any]], count: int = 3) -> dict[str, Any]:
    """A reference file to fill in: up to ``count`` solved frames, one per target and night where it can,
    with every value left for the person to write from their own log (never copied from the header)."""
    chosen: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    solved = sorted((r for r in rows if r["status"] == SOLVED), key=lambda r: (r["target"] or "", r["path"]))
    for r in solved:
        key = (r["target"] or "", r["night"] or "")
        if key not in seen and len(chosen) < count:
            seen.add(key)
            chosen.append(r)
    return {
        "status": "to_verify",
        "verified_by": None,
        "frames": [
            {
                "path": r["path"],
                "kind": None,
                "target": None,
                "date_obs": None,
                "exposure_s": None,
                "filter": None,
                "ra_deg": None,
                "dec_deg": None,
            }
            for r in chosen
        ],
    }


def _seconds(date_obs: str) -> float:
    when = datetime.fromisoformat(str(date_obs).replace("Z", "+00:00"))
    return (when if when.tzinfo else when.replace(tzinfo=UTC)).timestamp()


def _same_name(a: str | None, b: str | None) -> bool:
    return a is not None and b is not None and a.replace(" ", "").lower() == b.replace(" ", "").lower()


def _separation(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    r1, d1, r2, d2 = (math.radians(v) for v in (ra1, dec1, ra2, dec2))
    c = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(r1 - r2)
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


@dataclass
class Certification:
    source: LocalSource
    data: Path
    today: Callable[[], date] = date.today
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)

    @property
    def root(self) -> Path:
        return self.source.output_dir(self.data, "compatibility")

    @property
    def reference_path(self) -> Path:
        return self.root / REFERENCE

    def record_path(self, product_type: ProductType) -> Path:
        return self.root / f"{ADAPTER}-{product_type.value}.json"

    def run(self, reference: Mapping[str, Any]) -> dict[str, Any]:
        """Run the four kinds for images, and for time series once light curves exist; write a record
        for each (passing or not) and return what was checked."""
        rows = Catalogue(self.source, self.data).build()
        by_path = {r["path"]: r for r in rows}
        gateway = LocalImageGateway(self.source, self.data)
        frames = list(reference.get("frames") or [])
        stats_before = self._stats(frames)
        run_id = f"sdp-local-certify-{self.source.source_id}-{self.clock().strftime('%Y%m%dT%H%M%SZ')}"
        results = {ProductType.IMAGE: self._images(reference, frames, by_path, gateway)}
        curves = self._curves(reference, frames, by_path, gateway)
        if curves is not None:
            results[ProductType.TIMESERIES] = curves
        for checks in results.values():
            op = checks[ConformanceKind.OPERATIONAL]
            op.expect(
                self._stats(frames) == stats_before,
                "the reference frames are unchanged by the run",
                "a reference frame changed during the run",
            )
        solved = [
            by_path[p]["sha256"] for p in (str(f.get("path")) for f in frames) if by_path.get(p, {}).get("sha256")
        ]
        digests = tuple(sorted({f"sha256:{digest}" for digest in solved}))
        self.root.mkdir(parents=True, exist_ok=True)
        report: dict[str, Any] = {"run_id": run_id, "product_types": {}}
        for product_type, checks in results.items():
            record = AdapterCompatibilityRecord(
                adapter=ADAPTER,
                adapter_version=ADAPTER_VERSION,
                product_type=product_type,
                results=tuple(ConformanceResult(k, c.passed, run_id) for k, c in checks.items()),
                upstream_versions=_upstream(),
                live_smoke_on=self.today(),
                fixture_digests=digests,
            )
            path = self.record_path(product_type)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(record.to_document(), indent=2) + "\n", "utf-8")
            tmp.replace(path)
            report["product_types"][product_type.value] = {
                "admitted": record.passed,
                "record": str(path),
                "kinds": [c.to_document() for c in checks.values()],
            }
        return report

    def _stats(self, frames: list[Mapping[str, Any]]) -> list[tuple[str, int, int] | None]:
        out: list[tuple[str, int, int] | None] = []
        for f in frames:
            path = self.source.path / str(f.get("path", ""))
            try:
                stat = path.stat()
                out.append((str(f.get("path")), stat.st_size, stat.st_mtime_ns))
            except OSError:
                out.append(None)
        return out

    # --- images ---------------------------------------------------------------------------------

    def _images(
        self,
        reference: Mapping[str, Any],
        frames: list[Mapping[str, Any]],
        by_path: Mapping[str, dict[str, Any]],
        gateway: LocalImageGateway,
    ) -> dict[ConformanceKind, Check]:
        checks = _checks()
        contract, operational = checks[ConformanceKind.CONTRACT], checks[ConformanceKind.OPERATIONAL]
        semantic, scientific = checks[ConformanceKind.SEMANTIC], checks[ConformanceKind.SCIENTIFIC]
        self._verified(reference, frames, semantic)
        offered = {f.row["path"]: f for f in gateway.frames()}
        dialect = LocalFrameDialect(gateway)
        config = SolverConfig()
        for entry in frames:
            relative = str(entry.get("path") or "")
            row = by_path.get(relative)
            if row is None or row["status"] != SOLVED:
                for check in checks.values():
                    check.fail(f"{relative}: not a solved frame of {self.source.source_id}")
                continue
            frame = offered.get(relative)
            if not operational.expect(
                frame is not None,
                f"{relative}: offered with a current solution",
                f"{relative}: not offered (its solution is stale, or it is graded poor)",
            ):
                continue
            assert frame is not None
            self._image_contract(relative, row, frame.centre, gateway, dialect, contract, operational)
            self._image_semantic(relative, row, entry, semantic)
            matched, rms = row["matched"] or 0, row["rms_arcsec"] if row["rms_arcsec"] is not None else math.inf
            scientific.expect(
                matched >= config.min_matches and rms <= config.max_rms_arcsec,
                f'{relative}: {matched} Gaia stars matched at {rms:.2f}" RMS',
                f"{relative}: the fit ({matched} stars, {rms:.2f}\" RMS) is outside the solver's limits",
            )
            self._independent(relative, frame.solution, scientific)
        try:
            gateway._path(ProductRef(gateway.archive_id, "../outside.fits", "file:///outside.fits"))
            operational.fail("a product id outside the source was served")
        except LookupError:
            operational.ok("a product id outside the source is refused")
        return checks

    def _verified(self, reference: Mapping[str, Any], frames: list[Mapping[str, Any]], semantic: Check) -> None:
        semantic.expect(
            reference.get("status") == VERIFIED and bool(reference.get("verified_by")),
            f"the reference was verified by {reference.get('verified_by')}",
            "the reference is not verified yet: check each value against your log, then set status: verified "
            "and verified_by: your name",
        )
        semantic.expect(bool(frames), f"{len(frames)} reference frames", "the reference lists no frames")

    def _image_contract(
        self,
        relative: str,
        row: Mapping[str, Any],
        centre: tuple[float, float],
        gateway: LocalImageGateway,
        dialect: LocalFrameDialect,
        contract: Check,
        operational: Check,
    ) -> None:
        target = ObservationTarget(f"local:{relative}", *centre)
        criteria = ObservationCriteria(radius_arcsec=1.0, product_types=(IMAGE,), missions=(row["kind"],))
        index = gateway.search(target, criteria)
        contract.expect(
            bool(index.archive and index.release_label and index.query_text and index.licence and index.citation),
            f"{relative}: the search names its archive, release, query, licence and citation",
            f"{relative}: the search is missing its archive, release, query, licence or citation",
        )
        contract.expect(
            gateway.search(target, criteria).to_document() == index.to_document(),
            f"{relative}: the same search gives the same answer",
            f"{relative}: the same search gave two answers",
        )
        record = next((r for r in index.records if r.product.product_id == relative), None)
        if not contract.expect(
            record is not None and record.product_type == IMAGE and record.mission == row["kind"],
            f"{relative}: found as a {row['kind']} image at its own centre",
            f"{relative}: not found as a {row['kind']} image at its own centre",
        ):
            return
        assert record is not None
        fetched = gateway.fetch(record.product)
        contract.expect(
            bool(fetched.data) and fetched.read_from == record.product.uri and bool(fetched.licence.strip()),
            f"{relative}: fetched from where the record says, with its licence",
            f"{relative}: fetched from elsewhere, or without data or licence",
        )
        operational.expect(
            hashlib.sha256(fetched.data).hexdigest() == row["sha256"],
            f"{relative}: the bytes fetched are the bytes that were solved",
            f"{relative}: the bytes fetched differ from the ones solved (SHA-256)",
        )
        curated = dialect.curate(IMAGE, fetched)
        contract.expect(
            curated is not None
            and curated.media_type == "application/fits"
            and curated == dialect.curate(IMAGE, fetched),
            f"{relative}: curated to the canonical image, the same way twice",
            f"{relative}: not curated to the canonical image, or curated differently twice",
        )

    def _image_semantic(self, relative: str, row: Mapping[str, Any], entry: Mapping[str, Any], semantic: Check) -> None:
        if entry.get("kind") is not None:
            semantic.expect(
                str(entry["kind"]).lower() == row["kind"],
                f"{relative}: kind {row['kind']}",
                f"{relative}: SDP reads it as a {row['kind']}, you wrote {entry['kind']}",
            )
        if entry.get("target") is not None:
            semantic.expect(
                _same_name(str(entry["target"]), row["target"])
                or _same_name(str(entry["target"]), usable_name(row["object"])),
                f"{relative}: target {entry['target']}",
                f"{relative}: SDP reads its target as {row['target']}, you wrote {entry['target']}",
            )
        if entry.get("date_obs") is not None:
            ok = (
                row["date_obs"] is not None
                and abs(_seconds(str(entry["date_obs"])) - _seconds(row["date_obs"])) <= DATE_SECONDS
            )
            semantic.expect(
                ok,
                f"{relative}: taken at {entry['date_obs']}",
                f"{relative}: SDP reads it as taken at {row['date_obs']}, you wrote {entry['date_obs']} (UTC)",
            )
        if entry.get("exposure_s") is not None:
            semantic.expect(
                row["exposure_s"] is not None
                and abs(float(entry["exposure_s"]) - row["exposure_s"]) <= EXPOSURE_SECONDS,
                f"{relative}: {entry['exposure_s']} s exposure",
                f"{relative}: SDP reads a {row['exposure_s']} s exposure, you wrote {entry['exposure_s']} s",
            )
        if entry.get("filter") is not None:
            semantic.expect(
                _same_name(str(entry["filter"]), row["filter"]),
                f"{relative}: filter {entry['filter']}",
                f"{relative}: SDP reads its filter as {row['filter']}, you wrote {entry['filter']}",
            )
        if entry.get("ra_deg") is not None and entry.get("dec_deg") is not None:
            away = _separation(float(entry["ra_deg"]), float(entry["dec_deg"]), row["ra_deg"], row["dec_deg"])
            semantic.expect(
                away <= row["radius_deg"],
                f"{relative}: the pointing you wrote lies on the frame ({away:.2f} deg from its centre)",
                f"{relative}: the pointing you wrote is {away:.2f} deg from the solved centre, off the frame",
            )
        filled = [k for k in ("kind", "target", "date_obs", "exposure_s") if entry.get(k) is not None]
        semantic.expect(
            len(filled) == 4,
            f"{relative}: kind, target, date and exposure written down",
            f"{relative}: write down its kind, target, date_obs and exposure_s (filter and pointing are optional)",
        )

    def _independent(self, relative: str, solution: Mapping[str, Any], scientific: Check) -> None:
        """astropy, reading the ``.wcs`` sidecar, against SDP's own pixel_to_sky at the four corners."""
        from astropy.io import fits
        from astropy.wcs import WCS, FITSFixedWarning

        sidecar = LocalSolver(self.source, self.data, None, lambda _c: None).wcs_path(relative)  # type: ignore[arg-type,return-value]
        if not scientific.expect(
            sidecar.exists(),
            f"{relative}: .wcs sidecar present",
            f"{relative}: no .wcs sidecar (run sdp local sidecars)",
        ):
            return
        wcs = solution["wcs"]
        w, h = float(wcs["NAXIS1"]), float(wcs["NAXIS2"])
        x, y = np.array([1.0, w, w, 1.0, w / 2]), np.array([1.0, 1.0, h, h, h / 2])
        ra, dec = pixel_to_sky(wcs, x, y)
        header = fits.getheader(sidecar)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FITSFixedWarning)  # a header-only file has no image axes
            sky = WCS(header).all_pix2world(x, y, 1)
        worst = max(_separation(a, b, c, d) for a, b, c, d in zip(ra, dec, sky[0], sky[1], strict=True)) * 3600.0
        scientific.expect(
            worst <= INDEPENDENT_ARCSEC,
            f'{relative}: astropy agrees with SDP to {worst:.4f}" at the corners',
            f'{relative}: astropy and SDP disagree by {worst:.3f}" at the corners',
        )

    # --- light curves ---------------------------------------------------------------------------

    def _curves(
        self,
        reference: Mapping[str, Any],
        frames: list[Mapping[str, Any]],
        by_path: Mapping[str, dict[str, Any]],
        gateway: LocalImageGateway,
    ) -> dict[ConformanceKind, Check] | None:
        summaries = {}
        for path in stars_files(self.source, self.data):
            summary = json.loads(path.read_text("utf-8"))
            if summary.get("all_stars"):
                summaries[path.name.removesuffix("-stars.json")] = summary
        if not summaries:
            return None
        checks = _checks()
        contract, operational = checks[ConformanceKind.CONTRACT], checks[ConformanceKind.OPERATIONAL]
        semantic, scientific = checks[ConformanceKind.SEMANTIC], checks[ConformanceKind.SCIENTIFIC]
        self._verified(reference, frames, semantic)
        offered = {f.row["path"] for f in gateway.frames()}
        dialect = LocalFrameDialect(gateway)
        referenced = {str(f.get("path")): f for f in frames}
        covered = 0
        for stem, summary in sorted(summaries.items()):
            name = summary.get("target") or stem
            star = max(summary["all_stars"], key=lambda s: s["points"])
            target = ObservationTarget(f"gaia:DR3:{star['source_id']}", star["ra_deg"], star["dec_deg"])
            criteria = ObservationCriteria(radius_arcsec=2.0, product_types=(LIGHTCURVE,))
            product_id = f"{CURVE_PREFIX}{file_stem(name)}/{star['source_id']}"
            record = next(
                (r for r in gateway.search(target, criteria).records if r.product.product_id == product_id), None
            )
            if contract.expect(
                record is not None,
                f"{name}: Gaia DR3 {star['source_id']} offered",
                f"{name}: its best-measured star is not offered",
            ):
                assert record is not None
                fetched = gateway.fetch(record.product)
                curve = parse_lightcurve(fetched.data)
                contract.expect(
                    len(curve.mjd) == star["points"]
                    and bool(np.all(np.diff(curve.mjd) >= 0))
                    and bool(np.all(curve.mag_err > 0)),
                    f"{name}: its curve is canonical, in time order, with positive errors",
                    f"{name}: its curve is not canonical (points, order or errors)",
                )
                curated = dialect.curate(LIGHTCURVE, fetched)
                contract.expect(
                    curated is not None and curated.data == fetched.data,
                    f"{name}: curated as written",
                    f"{name}: not curated as written",
                )
            table = pq.read_table(LightCurves(self.source, self.data, name).path, columns=["path", "mjd"]).to_pylist()
            points = {r["path"]: r["mjd"] for r in table}
            stale = sorted(p for p in points if p not in offered)
            operational.expect(
                not stale,
                f"{name}: every point comes from a frame offered now",
                f"{name}: {len(stale)} points come from frames changed, graded poor or unsolved since (e.g. {stale[:1]})",
            )
            for relative, entry in referenced.items():
                if relative not in points or by_path.get(relative) is None:
                    continue
                covered += 1
                row = by_path[relative]
                self._image_semantic(relative, row, entry, semantic)
                written = entry.get("date_obs")
                dated = row["mjd"] is not None and abs(points[relative] - row["mjd"]) * 86400.0 <= 1.0
                if written is not None:
                    dated = (
                        dated and abs(_seconds(str(written)) - (points[relative] - MJD_UNIX) * 86400.0) <= DATE_SECONDS
                    )
                semantic.expect(
                    dated,
                    f"{relative}: its light-curve points are dated as the frame",
                    f"{relative}: its light-curve points are not dated as the frame",
                )
            bands = sorted(
                summary.get("precision_by_g", {}).items(), key=lambda kv: float(kv[0].split()[1].split("-")[0])
            )
            best = next((v for k, v in bands if 10.0 <= float(k.split()[1].split("-")[0]) < 13.0), None)
            scientific.expect(
                summary.get("stars", 0) >= MIN_CURVE_STARS and best is not None and best <= MAX_SCATTER_MAG,
                f"{name}: {summary.get('stars')} stars, the steadiest scatter {best} mag",
                f"{name}: too few stars or too much scatter ({summary.get('stars')} stars, {best} mag at G 10-13)",
            )
        semantic.expect(
            covered > 0,
            f"{covered} reference frames are in the light curves",
            "no reference frame is a sub of a measured target: add one to the reference",
        )
        return checks


def _upstream() -> dict[str, str]:
    out = {}
    for name in UPSTREAM:
        try:
            out[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            continue
    return out


def load_reference(path: Path) -> dict[str, Any]:
    document = yaml.safe_load(path.read_text("utf-8")) or {}
    if not isinstance(document, dict) or not isinstance(document.get("frames", []), list):
        raise ValueError(f"{path} is not a reference file: a mapping with a list of frames")
    return document


def records(source: LocalSource, data: Path) -> list[AdapterCompatibilityRecord]:
    """The compatibility records ``sdp local certify`` wrote for the source."""
    root = source.output_dir(data, "compatibility")
    out = []
    for path in sorted(root.glob(f"{ADAPTER}-*.json")) if root.is_dir() else []:
        try:
            out.append(AdapterCompatibilityRecord.from_document(json.loads(path.read_text("utf-8"))))
        except (OSError, ValueError, KeyError):
            continue
    return out


def admitted(source: LocalSource, data: Path, rows: Iterable[Mapping[str, Any]]) -> dict[str, bool]:
    """Product type -> admitted: a passing record (re-derived from its four results) for this adapter and
    version, whose reference frames are all still catalogued, unchanged and not graded poor."""
    current = {
        f"sha256:{r['sha256']}" for r in rows if r.get("sha256") and r["status"] == SOLVED and r.get("grade") != POOR
    }
    return {
        r.product_type.value: True
        for r in records(source, data)
        if r.adapter == ADAPTER and r.adapter_version == ADAPTER_VERSION and r.passed
        and r.fixture_digests and set(r.fixture_digests) <= current
    }  # fmt: skip


__all__ = ["ADAPTER", "ADAPTER_VERSION", "Certification", "admitted", "load_reference", "records", "reference_template"]

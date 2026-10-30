"""A local source's solved frames as an observation archive, so Investigate's image route reads them
like any archive's images (#71).

``search`` lists the dated, solved frames whose footprint reaches the target. A profile's ``missions``
are frame kinds here (``light``, ``stack``), and ``min_calibration_level: 3`` keeps the stacks alone. Retrieve cuts each one down to a
square around the field (``cutout_product``): the cut is read from the frame (never written), a raw
Bayer frame is binned 2 x 2 as it was for solving, and the result is a FITS image whose header carries
the frame's sky solution, so the image tests' detector, ``align_wcs`` and image-counterpart work
unchanged. A whole frame fetched as is carries no solution; ``LocalFrameDialect`` curates it into the
same canonical form.
"""

from __future__ import annotations

import io
import json
import math
import time
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

import astropy.units as u
import numpy as np

from sdp.adapters.local.index import FrameIndex
from sdp.adapters.local.lightcurves import curve_csv, stars_files
from sdp.adapters.local.quality import POOR, Grades
from sdp.adapters.local.solver import Unsolved, project, read_frame, solving_image
from sdp.adapters.local.solving import SOLVED, LocalSolver
from sdp.adapters.local.sources import LocalSource
from sdp.kernel.domain.investigation import (
    FetchedProduct,
    Field,
    ObservationCriteria,
    ObservationIndex,
    ObservationRecord,
    ProductRef,
)
from sdp.kernel.ports.investigate import (
    CuratedProduct,
    ObservationTarget,
    cutout_product_id,
)
from sdp.kernel.science.products import LIGHTCURVE

IMAGE = "image"
FITS = "application/fits"
CSV = "text/csv"
#: a light curve's product id: ``lightcurve/<target>/<Gaia DR3 source_id>``
CURVE_PREFIX = "lightcurve/"
LICENCE = "The person's own frames: used locally, never published by SDP"
MJD_UNIX = 40587.0
#: A frame kind as an ObsCore calibration level: a raw sub is level 1, a stacked master is combined (3)
CALIBRATION = {"light": 1, "stack": 3}


def _mjd(date_obs: str | None) -> float | None:
    if not date_obs:
        return None
    try:
        stamp = datetime.fromisoformat(date_obs.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return MJD_UNIX + stamp.timestamp() / 86400.0


def _separation_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    r1, d1, r2, d2 = map(math.radians, (ra1, dec1, ra2, dec2))
    c = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(r1 - r2)
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


@dataclass(frozen=True)
class SolvedFrame:
    row: Mapping[str, Any]
    solution: Mapping[str, Any]

    @property
    def wcs(self) -> Mapping[str, Any]:
        return self.solution["wcs"]  # type: ignore[no-any-return]

    @property
    def centre(self) -> tuple[float, float]:
        c = self.solution["centre"]
        return float(c["ra_deg"]), float(c["dec_deg"])

    @property
    def radius_deg(self) -> float:
        """Half the frame's diagonal on the sky."""
        scale = float(self.solution["scale_arcsec"]) / 3600.0
        return 0.5 * math.hypot(self.wcs["NAXIS1"], self.wcs["NAXIS2"]) * scale


def canonical_image(
    data: np.ndarray,
    bayer: str | None,
    wcs: Mapping[str, Any],
    header: Mapping[str, Any],
    *,
    origin: tuple[int, int] = (0, 0),
    date_obs: str | None = None,
) -> bytes:
    """A FITS image of ``data`` (a raw frame or a cut of one starting at raw pixel ``origin``, 0-based),
    reduced as for solving, with the frame's solution moved onto its pixels. ``date_obs`` is the
    indexed date, which also covers a header that spells it DATE_OBS."""
    from astropy.io import fits

    image, binning = solving_image(data, bayer)
    x0, y0 = origin
    cards: dict[str, Any] = {
        k: wcs[k] for k in ("CTYPE1", "CTYPE2", "CUNIT1", "CUNIT2", "RADESYS", "EQUINOX", "CRVAL1", "CRVAL2")
    }
    # raw 1-based pixel p is binned 1-based pixel (p - x0 + (b - 1) / 2) / b
    cards["CRPIX1"] = (wcs["CRPIX1"] - x0 + (binning - 1) / 2) / binning
    cards["CRPIX2"] = (wcs["CRPIX2"] - y0 + (binning - 1) / 2) / binning
    for key in ("CD1_1", "CD1_2", "CD2_1", "CD2_2"):
        cards[key] = wcs[key] * binning
    # a distortion polynomial in raw pixel offsets u = b * u' becomes, in binned ones, A'_pq = A_pq b^(p+q-1)
    for key, value in wcs.items():
        head, _, rest = key.partition("_")
        if head in ("A", "B", "AP", "BP") and rest == "ORDER":
            cards[key] = value
        elif head in ("A", "B", "AP", "BP") and rest.count("_") == 1 and rest.replace("_", "").isdigit():
            p, q = (int(n) for n in rest.split("_"))
            cards[key] = float(value) * binning ** (p + q - 1)
    out = fits.Header()
    for key, value in cards.items():
        out[key] = value
    for key in ("DATE-OBS", "OBJECT", "FILTER", "EXPOSURE", "EXPTIME", "GAIN"):
        if header.get(key) not in (None, ""):
            out[key] = header[key]
    date = date_obs or header.get("DATE-OBS")
    mjd = _mjd(str(date)) if date else None
    if mjd is not None:
        out["DATE-OBS"], out["MJD-OBS"] = str(date), mjd
    out["SDPBIN"] = (binning, "raw pixels per side of each pixel here")
    buffer = io.BytesIO()
    fits.PrimaryHDU(np.asarray(image, dtype=np.float32), header=out).writeto(buffer)
    return buffer.getvalue()


@dataclass
class LocalImageGateway:
    """ObservationArchiveGateway (and ProductCutouts) over a local source's solved frames."""

    source: LocalSource
    data: Path
    #: offer frames graded poor too (LOC-017); by default they are left out once the source is graded
    include_poor: bool = False
    #: a long-running app reads the solutions again this often, so newly solved frames are offered
    fresh_seconds: float = 60.0
    _frames: list[SolvedFrame] | None = field(default=None, repr=False)
    _read_at: float = field(default=0.0, repr=False)
    _curves: list[tuple[str, dict[str, Any]]] | None = field(default=None, repr=False)
    _curves_at: float = field(default=0.0, repr=False)

    @property
    def archive_id(self) -> str:
        return self.source.source_id

    @property
    def release_label(self) -> str:
        return self.source.source_id

    @property
    def citation(self) -> str:
        return f"Own observations, {self.source.path} ({self.source.source_id})"

    def frames(self) -> list[SolvedFrame]:
        """Solved light and stack frames, from the index and the solutions folder (read again after
        ``fresh_seconds``)."""
        if self._frames is None or time.monotonic() - self._read_at > self.fresh_seconds:
            root = self.source.output_dir(self.data, "solutions")
            grades = {} if self.include_poor else Grades(self.source, self.data).by_path()
            frames = []
            for row in FrameIndex(self.source, self.data).rows():
                if grades.get(row["path"], {}).get("grade") == POOR:
                    continue
                path = root / (row["path"] + SOLVED)
                # a solution written for the file as it is now, not before it was replaced
                if row["kind"] in ("light", "stack") and LocalSolver.current(path, row):
                    frames.append(SolvedFrame(row, json.loads(path.read_text("utf-8"))))
            self._frames, self._read_at = frames, time.monotonic()
        return self._frames

    def curves(self) -> list[tuple[str, dict[str, Any]]]:
        """(target, star) for each star ``sdp local lightcurve`` measured (read again after
        ``fresh_seconds``)."""
        if self._curves is None or time.monotonic() - self._curves_at > self.fresh_seconds:
            self._curves_at = time.monotonic()
            self._curves = [
                (path.name.removesuffix("-stars.json"), star)
                for path in stars_files(self.source, self.data)
                for star in json.loads(path.read_text("utf-8"))["all_stars"]
            ]
        return self._curves

    def _curve_records(self, target: ObservationTarget, criteria: ObservationCriteria) -> list[ObservationRecord]:
        if criteria.product_types and LIGHTCURVE not in criteria.product_types:
            return []
        if criteria.missions and "light" not in criteria.missions:
            return []
        records = []
        for name, star in self.curves():
            separation = _separation_deg(target.ra_deg, target.dec_deg, star["ra_deg"], star["dec_deg"])
            if separation * 3600.0 > criteria.radius_arcsec:
                continue
            product_id = f"{CURVE_PREFIX}{name}/{star['source_id']}"
            uri = (self.source.output_dir(self.data, "lightcurves") / f"{name}.parquet").as_uri()
            records.append(
                ObservationRecord(
                    ProductRef(self.archive_id, product_id, uri),
                    mission="light",
                    instrument="green pixels",
                    product_type=LIGHTCURVE,
                    epoch_mjd=star.get("first_mjd"),
                    epoch_end_mjd=star.get("last_mjd"),
                    size_bytes=None,
                    licence=LICENCE,
                    citation=self.citation,
                )
            )
        return records

    def _ref(self, frame: SolvedFrame) -> ProductRef:
        return ProductRef(self.archive_id, frame.row["path"], (self.source.path / frame.row["path"]).as_uri())

    def _frame(self, product: ProductRef) -> SolvedFrame:
        for frame in self.frames():
            if frame.row["path"] == product.product_id:
                return frame
        raise LookupError(f"{product.product_id} is not a solved frame of {self.archive_id}")

    def search(self, target: ObservationTarget, criteria: ObservationCriteria) -> ObservationIndex:
        reach = criteria.radius_arcsec / 3600.0
        records = self._curve_records(target, criteria)
        for frame in self.frames():
            if _separation_deg(target.ra_deg, target.dec_deg, *frame.centre) > frame.radius_deg + reach:
                continue
            kind = frame.row["kind"]
            epoch = _mjd(frame.row["date_obs"])
            if epoch is None:  # an image with no date cannot be compared with Gaia's moving stars
                continue
            if criteria.epoch_min_mjd is not None and epoch < criteria.epoch_min_mjd:
                continue
            if criteria.epoch_max_mjd is not None and epoch > criteria.epoch_max_mjd:
                continue
            if criteria.product_types and IMAGE not in criteria.product_types:
                continue
            if criteria.missions and kind not in criteria.missions:
                continue
            if criteria.min_calibration_level is not None and CALIBRATION[kind] < criteria.min_calibration_level:
                continue
            records.append(
                ObservationRecord(
                    self._ref(frame),
                    mission=kind,
                    instrument=frame.row.get("filter") or "",
                    product_type=IMAGE,
                    epoch_mjd=epoch,
                    size_bytes=int(frame.row["size"]),
                    licence=LICENCE,
                    citation=self.citation,
                )
            )
        records.sort(key=lambda r: (r.epoch_mjd or 0.0, r.product.product_id))
        return ObservationIndex(
            self.archive_id,
            self.release_label,
            f"LOCAL solved frames within {criteria.radius_arcsec:g} arcsec of ({target.ra_deg:.6f}, {target.dec_deg:.6f})",
            target.key,
            tuple(records),
            LICENCE,
            self.citation,
        )

    def _path(self, product: ProductRef) -> Path:
        """A solved frame's file, refused when it resolves outside the source (a link elsewhere)."""
        self._frame(product)
        path = (self.source.path / PurePosixPath(product.product_id)).resolve()
        if not path.is_relative_to(self.source.path.resolve()):
            raise LookupError(f"{product.product_id} lies outside {self.source.path}")
        return path

    def fetch(self, product: ProductRef) -> FetchedProduct:
        """The frame's bytes as they are on disk (read-only), or a star's light curve as canonical CSV."""
        if product.product_id.startswith(CURVE_PREFIX):
            name, _, source_id = product.product_id.removeprefix(CURVE_PREFIX).rpartition("/")
            body = curve_csv(self.source, self.data, name, int(source_id))
            return FetchedProduct(product, body, CSV, self.release_label, LICENCE, self.citation, product.uri)
        path = self._path(product)
        try:
            body = path.read_bytes()
        except OSError as exc:  # moved or deleted since it was indexed: skip this frame, not the run
            raise LookupError(f"{product.product_id} could not be read: {exc}") from exc
        return FetchedProduct(product, body, FITS, self.release_label, LICENCE, self.citation, product.uri)

    def cutout_product(self, product: ProductRef, ra_deg: float, dec_deg: float, side_arcsec: float) -> FetchedProduct:
        frame = self._frame(product)
        wcs = frame.wcs
        scale = float(frame.solution["scale_arcsec"])
        # the cut's centre in raw pixels (1-based), from the solution
        cd = np.array([[wcs["CD1_1"], wcs["CD1_2"]], [wcs["CD2_1"], wcs["CD2_2"]]])
        xi, eta = project(np.array([ra_deg]), np.array([dec_deg]), wcs["CRVAL1"], wcs["CRVAL2"])
        dx, dy = np.linalg.solve(cd, np.array([xi[0], eta[0]]))
        cx, cy = wcs["CRPIX1"] + dx, wcs["CRPIX2"] + dy
        half = math.ceil(side_arcsec / scale / 2)
        width, height = int(wcs["NAXIS1"]), int(wcs["NAXIS2"])
        # whole Bayer cells: start and end on even raw pixels
        x0 = max(0, int(cx - 1 - half) // 2 * 2)
        y0 = max(0, int(cy - 1 - half) // 2 * 2)
        x1 = min(width, (int(cx - 1 + half) // 2 + 1) * 2)
        y1 = min(height, (int(cy - 1 + half) // 2 + 1) * 2)
        if x1 - x0 < 16 or y1 - y0 < 16:
            raise ValueError(f"({ra_deg:.5f}, {dec_deg:.5f}) is off {product.product_id}")
        try:
            data, header = read_frame(self._path(product))
        except (OSError, Unsolved) as exc:
            raise LookupError(f"{product.product_id} could not be read: {exc}") from exc
        cut = data[..., y0:y1, x0:x1]
        bayer = frame.row["bayer"] if frame.row["kind"] != "stack" else None
        body = canonical_image(cut, bayer, wcs, header, origin=(x0, y0), date_obs=frame.row["date_obs"])
        ref = replace(product, product_id=cutout_product_id(product.product_id, ra_deg, dec_deg, side_arcsec))
        return FetchedProduct(ref, body, FITS, self.release_label, LICENCE, self.citation, product.uri)

    def cutout(self, field_: Field, size: u.Quantity) -> FetchedProduct:
        raise NotImplementedError("local frames are cut per frame (cutout_product); there is no mosaic of the field")


@dataclass(frozen=True)
class LocalFrameDialect:
    """A whole local frame to the canonical image the tests read: reduced as for solving, with its
    sky solution in the header. A frame with no solution is left uncurated."""

    gateway: LocalImageGateway
    dialect_id: str = "local-frame"
    dialect_version: str = "1.0.0"

    def curate(self, product_type: str, raw: FetchedProduct) -> CuratedProduct | None:
        if product_type == LIGHTCURVE and raw.product.product_id.startswith(CURVE_PREFIX):
            return CuratedProduct(raw.data, CSV)  # written canonical by LightCurves
        if product_type != IMAGE:
            return None
        try:
            frame = self.gateway._frame(raw.product)
        except LookupError:
            return None
        try:
            data, header = read_frame(io.BytesIO(raw.data))
        except (OSError, Unsolved):
            return None
        bayer = frame.row["bayer"] if frame.row["kind"] != "stack" else None
        return CuratedProduct(canonical_image(data, bayer, frame.wcs, header, date_obs=frame.row["date_obs"]), FITS)

"""Fakes for the Investigate ports: FakeObservationArchive, PlantedDetector, NearestWcsRefiner,
FixtureResolver, StaticSourceRegistry and the fixture-backed InMemory repositories' companions.

Each passes the same contract suite as the adapter it stands in for (sdp.testing.contracts.investigate).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import astropy.units as u
import numpy as np

from sdp.kernel.domain.canonical import canonical_json
from sdp.kernel.domain.investigation import (
    FetchedProduct,
    Field,
    ObservationCriteria,
    ObservationIndex,
    ObservationRecord,
    ProductRef,
    ResolverRecord,
    region_centre,
)
from sdp.kernel.ports.investigate import (
    MEASURED,
    OFF_IMAGE,
    CuratedProduct,
    Detection,
    DetectionList,
    ForcedMeasurement,
    ForcedPhotometry,
    FrameDescription,
    ImageFrame,
    ObservationTarget,
    RefinedWcs,
    ServiceRecord,
    SkyPoint,
)


def separation_arcsec(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    r1, d1, r2, d2 = map(math.radians, (ra1, dec1, ra2, dec2))
    h = math.sin((d2 - d1) / 2) ** 2 + math.cos(d1) * math.cos(d2) * math.sin((r2 - r1) / 2) ** 2
    return math.degrees(2 * math.asin(min(1.0, math.sqrt(h)))) * 3600.0


@dataclass(frozen=True)
class PlacedRecord:
    """An observation record and the sky position it covers."""

    ra_deg: float
    dec_deg: float
    record: ObservationRecord


@dataclass
class FakeObservationArchive:
    """Serves fixture observation records and product bytes; prefers a cloud URI when one exists."""

    archive_id: str
    release_label: str
    records: tuple[PlacedRecord, ...] = ()
    products: dict[str, bytes] = field(default_factory=dict)
    licence: str = "fixture licence"
    citation: str = "fixture citation"
    searches: int = 0
    fetches: list[str] = field(default_factory=list)

    def search(self, target: ObservationTarget, criteria: ObservationCriteria) -> ObservationIndex:
        self.searches += 1
        hits = []
        for placed in self.records:
            rec = placed.record
            if separation_arcsec(target.ra_deg, target.dec_deg, placed.ra_deg, placed.dec_deg) > criteria.radius_arcsec:
                continue
            if criteria.missions and rec.mission not in criteria.missions:
                continue
            if criteria.product_types and rec.product_type not in criteria.product_types:
                continue
            if criteria.epoch_min_mjd is not None and (rec.epoch_mjd or -math.inf) < criteria.epoch_min_mjd:
                continue
            if criteria.epoch_max_mjd is not None and (rec.epoch_mjd or math.inf) > criteria.epoch_max_mjd:
                continue
            hits.append(rec)
        hits.sort(key=lambda r: (r.epoch_mjd if r.epoch_mjd is not None else math.inf, r.product.product_id))
        query = "FAKE-OBS " + canonical_json(
            {"target": [target.ra_deg, target.dec_deg], "criteria": criteria.to_document()}
        )
        return ObservationIndex(
            archive=self.archive_id,
            release_label=self.release_label,
            query_text=query,
            target=target.key,
            records=tuple(hits),
            licence=self.licence,
            citation=self.citation,
        )

    def fetch(self, product: ProductRef) -> FetchedProduct:
        if product.product_id not in self.products:
            raise KeyError(f"no fixture bytes for {product.product_id}")
        self.fetches.append(product.product_id)
        return FetchedProduct(
            product=product,
            data=self.products[product.product_id],
            media_type="application/octet-stream",
            release_label=self.release_label,
            licence=self.licence,
            citation=self.citation,
            read_from=product.cloud_uri or product.uri,
        )

    def cutout(self, field: Field, size: u.Quantity) -> FetchedProduct:
        ra, dec = region_centre(field.region)
        uri = f"fake://{self.archive_id}/cutout?ra={ra:.6f}&dec={dec:.6f}&size={size.to_value(u.arcmin):.3f}"
        data = canonical_json({"cutout": field.to_document(), "size_arcmin": size.to_value(u.arcmin)}).encode()
        return FetchedProduct(
            product=ProductRef(self.archive_id, f"cutout-{field.field_id}", uri),
            data=data,
            media_type="application/json",
            release_label=self.release_label,
            licence=self.licence,
            citation=self.citation,
            read_from=uri,
        )


#: A minimal TAN WCS header (1 arcsec pixels) used by the planted detector and its fixtures.
def tan_header(ra_deg: float, dec_deg: float, crpix: float = 512.5, scale_deg: float = 1 / 3600) -> str:
    cards = {
        "CTYPE1": "'RA---TAN'",
        "CTYPE2": "'DEC--TAN'",
        "CRVAL1": f"{ra_deg:.10f}",
        "CRVAL2": f"{dec_deg:.10f}",
        "CRPIX1": f"{crpix}",
        "CRPIX2": f"{crpix}",
        "CD1_1": f"{-scale_deg:.12e}",
        "CD1_2": "0.0",
        "CD2_1": "0.0",
        "CD2_2": f"{scale_deg:.12e}",
    }
    return "\n".join(f"{k:<8}= {v}" for k, v in cards.items())


@dataclass
class PlantedDetector:
    """Returns the detections planted for each image artifact: the SourceDetector fake."""

    planted: Mapping[str, Sequence[Detection]]
    headers: Mapping[str, str] = field(default_factory=dict)
    detector_id: str = "planted"

    def detect(self, frame: ImageFrame, params: Mapping[str, object]) -> DetectionList:
        detections = tuple(self.planted.get(frame.artifact_id, ()))
        header = self.headers.get(frame.artifact_id) or (
            frame.wcs.decode() if frame.wcs else tan_header(*_mean_position(detections))
        )
        return DetectionList(
            detections=detections,
            wcs_header=header,
            background=0.0,
            background_rms=1.0,
            detector_id=self.detector_id,
            params=dict(params),
        )


@dataclass(frozen=True)
class PlantedImage:
    """What the planted photometer knows of one image: its description, and each source's total flux
    and position. A position within ``radius`` of a source gets that share of its flux."""

    description: FrameDescription
    sources: Mapping[str, tuple[float, float, float]]
    """key -> (ra_deg, dec_deg, flux)"""
    flux_err: float = 1.0
    half_width_arcsec: float = 30.0


@dataclass
class PlantedPhotometer:
    """The ForcedPhotometer fake: each image is a set of Gaussian stars of the description's FWHM, and
    a measurement is the share of every planted star's flux the aperture holds (no pixels, no noise)."""

    images: Mapping[str, PlantedImage]
    photometer_id: str = "planted-photometer"

    def describe(
        self, frame: ImageFrame, positions: Sequence[SkyPoint], params: Mapping[str, object]
    ) -> FrameDescription:
        return self.images[frame.artifact_id].description

    def measure(
        self,
        frame: ImageFrame,
        positions: Sequence[SkyPoint],
        radius_arcsec: float,
        annulus_arcsec: tuple[float, float],
        params: Mapping[str, object],
    ) -> ForcedPhotometry:
        image = self.images[frame.artifact_id]
        sigma = (image.description.fwhm_arcsec or 1.0) / (2 * math.sqrt(2 * math.log(2)))
        centre = next(iter(image.sources.values()), (0.0, 0.0, 0.0))
        out = []
        for p in positions:
            if separation_arcsec(p.ra_deg, p.dec_deg, centre[0], centre[1]) > image.half_width_arcsec:
                out.append(ForcedMeasurement(p.key, 0.0, 0.0, OFF_IMAGE))
                continue
            flux = 0.0
            for ra, dec, total in image.sources.values():
                d = separation_arcsec(p.ra_deg, p.dec_deg, ra, dec)
                # the share of a Gaussian centred d away inside the aperture, to first order in d
                flux += (
                    total * (1 - math.exp(-(radius_arcsec**2) / (2 * sigma**2))) * math.exp(-(d**2) / (2 * sigma**2))
                )
            out.append(ForcedMeasurement(p.key, flux, image.flux_err, MEASURED, image.description.fwhm_arcsec))
        return ForcedPhotometry(tuple(out), radius_arcsec, annulus_arcsec, self.photometer_id, dict(params))


def _mean_position(detections: Sequence[Detection]) -> tuple[float, float]:
    if not detections:
        return 0.0, 0.0
    return float(np.mean([d.ra_deg for d in detections])), float(np.mean([d.dec_deg for d in detections]))


@dataclass
class NearestWcsRefiner:
    """Applies the median offset to the nearest reference sources: the WcsRefiner fake.

    It shifts sky positions by a single translation, which is what tweakwcs' "shift" fit does.
    """

    max_separation_arcsec: float = 5.0
    refiner_id: str = "nearest-shift"

    def refine(
        self,
        detections: DetectionList,
        reference_ra: np.ndarray,
        reference_dec: np.ndarray,
        params: Mapping[str, object],
    ) -> RefinedWcs:
        dets = detections.detections
        if not dets or len(reference_ra) == 0:
            return RefinedWcs(detections.wcs_header, 0.0, 0, self.refiner_id, dets)
        dra, ddec = [], []
        for d in dets:
            cosd = math.cos(math.radians(d.dec_deg))
            sep = np.hypot((reference_ra - d.ra_deg) * cosd, reference_dec - d.dec_deg) * 3600.0
            j = int(np.argmin(sep))
            if sep[j] <= self.max_separation_arcsec:
                dra.append(float(reference_ra[j] - d.ra_deg))
                ddec.append(float(reference_dec[j] - d.dec_deg))
        if not dra:
            return RefinedWcs(detections.wcs_header, 0.0, 0, self.refiner_id, dets)
        sra, sdec = float(np.median(dra)), float(np.median(ddec))
        shifted = tuple(Detection(d.x, d.y, (d.ra_deg + sra) % 360.0, d.dec_deg + sdec, d.flux, d.peak) for d in dets)
        residual = [
            math.hypot((a - sra) * math.cos(math.radians(d.dec_deg)), b - sdec) * 3600.0
            for a, b, d in zip(dra, ddec, dets, strict=False)
        ]
        header = detections.wcs_header + f"\nHISTORY  {self.refiner_id}: shift dra={sra:.3e} ddec={sdec:.3e} deg"
        return RefinedWcs(header, float(np.sqrt(np.mean(np.square(residual)))), len(dra), self.refiner_id, shifted)


@dataclass(frozen=True)
class FixtureResolver:
    """A fixture table standing in for SIMBAD: the ObjectResolver fake."""

    table: tuple[ResolverRecord, ...] = ()
    resolver_id: str = "fixture-simbad"
    release_label: str = "simbad:fixture"

    def by_position(self, ra_deg: float, dec_deg: float, radius_arcsec: float) -> ResolverRecord | None:
        best: tuple[float, ResolverRecord] | None = None
        for rec in self.table:
            if rec.ra_deg is None or rec.dec_deg is None:
                continue
            sep = separation_arcsec(ra_deg, dec_deg, rec.ra_deg, rec.dec_deg)
            if sep <= radius_arcsec and (best is None or sep < best[0]):
                best = (sep, rec)
        if best is None:
            return None
        sep, rec = best
        return ResolverRecord(rec.main_id, rec.object_type, rec.bibcodes, rec.ra_deg, rec.dec_deg, round(sep, 6))

    def by_name(self, name: str) -> ResolverRecord | None:
        wanted = " ".join(name.split()).lower()
        return next((r for r in self.table if " ".join(r.main_id.split()).lower() == wanted), None)


@dataclass(frozen=True)
class FieldFixtureResolver(FixtureResolver):
    """The fixture resolver as a FieldResolver: one call for the whole field, each call's size kept (#65)."""

    field_calls: list[int] = field(default_factory=list)

    def resolve_positions(
        self, positions: Sequence[tuple[float, float]], radius_arcsec: float
    ) -> list[ResolverRecord | None]:
        self.field_calls.append(len(positions))
        return [self.by_position(ra, dec, radius_arcsec) for ra, dec in positions]


@dataclass(frozen=True)
class StaticSourceRegistry:
    """Fixed services with an all-sky footprint: the SourceRegistry fake."""

    records: tuple[ServiceRecord, ...] = ()

    def services(self, field: Field, standard: str) -> Sequence[ServiceRecord]:
        return [r for r in self.records if r.standard.lower() == standard.lower()]


@dataclass(frozen=True)
class ThinningDialect:
    """A ProductDialect over products already in canonical CSV: keeps every ``every``-th epoch of the
    product types it curates, so the curated child differs from its raw parent."""

    product_types: tuple[str, ...] = ("lightcurve", "astrometry")
    every: int = 2
    dialect_id: str = "fixture-thinning"
    dialect_version: str = "1.0.0"

    def curate(self, product_type: str, raw: FetchedProduct) -> CuratedProduct | None:
        if product_type not in self.product_types:
            return None
        header, *rows = raw.data.decode("utf-8").splitlines()
        kept = rows[:: self.every]
        return CuratedProduct(("\n".join([header, *kept]) + "\n").encode(), "text/csv")

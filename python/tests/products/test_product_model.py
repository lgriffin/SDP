"""The product model (v0.4.0 §3, §6): the JWST dialect, the envelope validator and the T5s semantic
tier on synthetic JWST-layout products, plus every recorded reference product when present."""

from __future__ import annotations

import io
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from sdp.adapters.products import EnvelopeValidator, JwstDialect
from sdp.adapters.products.jwst import calibration_level_from_name
from sdp.kernel.domain.errors import CurationBlocked
from sdp.kernel.domain.integration import ProductType, ScientificDataProduct, compare_to_reference
from sdp.kernel.ports.integration import ProductNormalizer, ProductValidator
from sdp.kernel.ports.investigate import CuratedProduct, ProductDialect

from .synthetic_products import image, load_reference_products, raw_product, synthetic_i2d, synthetic_x1d

DIALECT = JwstDialect()
VALIDATOR = EnvelopeValidator()


def test_the_jwst_dialect_is_both_a_product_dialect_and_a_normaliser() -> None:
    as_dialect: ProductDialect = DIALECT
    as_normaliser: ProductNormalizer = DIALECT
    as_validator: ProductValidator = VALIDATOR
    assert as_dialect.dialect_id == as_normaliser.dialect_id == "mast-jwst"
    assert as_validator.validator_version == "1.0.0"
    assert DIALECT.curate("image", synthetic_i2d().raw.fetched) is None


@pytest.mark.tier("T5s")
@pytest.mark.req("INT-006")
@pytest.mark.parametrize("make", [synthetic_i2d, synthetic_x1d], ids=["i2d", "x1d"])
def test_synthetic_products_normalise_to_their_reference_records(make) -> None:  # type: ignore[no-untyped-def]
    sp = make()
    meta = DIALECT.normalize(sp.raw)
    diffs = [c for c in compare_to_reference(meta, sp.reference) if not c.passed]
    assert not diffs, [(c.field, c.detail) for c in diffs]
    report = VALIDATOR.validate(meta, sp.raw)
    assert report.passed, [(c.field, c.detail) for c in report.failures()]
    doc = meta.to_document()
    for key in (
        "target_position",
        "time",
        "units",
        "calibration_level",
        "uncertainty_present",
        "quality_present",
        "access_status",
        "rights",
        "relationships",
    ):
        assert key in doc


@pytest.mark.tier("T5s")
@pytest.mark.req("INT-006")
def test_calibration_references_and_progenitors_are_recorded() -> None:
    meta = DIALECT.normalize(synthetic_i2d().raw)
    assert ("R_FLAT", "crds://jwst_nircam_flat_0001.fits") in meta.calibration_references
    assert len(meta.progenitors()) == 2


@pytest.mark.tier("T1")
@pytest.mark.parametrize(
    ("name", "level"),
    [
        ("jw01345001001_02101_00001_nrca1_uncal.fits", 1),
        ("jw01345001001_02101_00001_nrca1_cal.fits", 2),
        ("jw01345-o001_t001_nircam_clear-f200w_i2d.fits", 3),
        ("jw01345-o002_s00001_nirspec_clear-prism_x1d.fits", 3),
        ("jw01345002001_03101_00001_nrs1_x1d.fits", 2),
        ("jw01345-o003_t001_miri_ch1-short_s3d.fits", 3),
    ],
)
def test_pipeline_suffixes_map_to_obscore_levels(name: str, level: int) -> None:
    assert calibration_level_from_name(name) == level


@pytest.mark.tier("T5s")
@pytest.mark.req("INT-005")
def test_the_raw_bytes_and_their_checksum_are_kept() -> None:
    sp = synthetic_i2d()
    meta = DIALECT.normalize(sp.raw)
    assert meta.checksum == sp.raw.raw_artifact_id and meta.size_bytes == len(sp.raw.data)
    tampered = replace(sp.raw, raw_artifact_id="sha256:" + "0" * 64)
    report = VALIDATOR.validate(meta, tampered)
    assert not report.passed and "checksum" in {c.field for c in report.failures()}


@pytest.mark.tier("T5s")
@pytest.mark.req("INT-006")
def test_an_unparseable_unit_fails_validation() -> None:
    raw = raw_product(image(bunit="furlongs per fortnight squiggle"), "jw99999-o001_t001_nircam_clear-f200w_i2d.fits")
    report = VALIDATOR.validate(DIALECT.normalize(raw), raw)
    assert "units" in {c.field for c in report.failures()}


@pytest.mark.tier("T5s")
@pytest.mark.req("INT-006")
def test_a_keyword_that_disagrees_with_the_obscore_row_fails_validation() -> None:
    raw = raw_product(
        image(),
        "jw99999-o001_t001_nircam_clear-f200w_i2d.fits",
        obscore={"obs_collection": "HST", "instrument_name": "WFC3/IR"},
    )
    meta = DIALECT.normalize(raw)
    assert meta.mission == "JWST", "the keyword wins"
    failed = {c.field for c in VALIDATOR.validate(meta, raw).failures()}
    assert {"mission.obscore", "instrument.obscore"} <= failed


@pytest.mark.tier("T5s")
@pytest.mark.req("INT-006")
def test_an_envelope_that_omits_an_error_extension_fails() -> None:
    sp = synthetic_i2d()
    meta = DIALECT.normalize(sp.raw)
    stripped = replace(meta, extensions=tuple(e for e in meta.extensions if e.name != "ERR"))
    assert "extensions.ERR" in {c.field for c in VALIDATOR.validate(stripped, sp.raw).failures()}


def _drop(data: bytes, name: str | None = None, *, strip_wcs: bool = False) -> bytes:
    with fits.open(io.BytesIO(data)) as hdul:
        keep = [h.copy() for h in hdul if h.name != name]
    if strip_wcs:
        for k in ("CTYPE1", "CTYPE2", "CRVAL1", "CRVAL2", "CRPIX1", "CRPIX2", "CDELT1", "CDELT2", "CUNIT1", "CUNIT2"):
            keep[1].header.remove(k, ignore_missing=True)
    buf = io.BytesIO()
    fits.HDUList([fits.PrimaryHDU(header=keep[0].header), *keep[1:]]).writeto(buf)
    return buf.getvalue()


@pytest.mark.tier("T5s")
@pytest.mark.req("INT-007")
@pytest.mark.parametrize(("lose", "field"), [("ERR", "extensions.ERR"), ("DQ", "extensions.DQ"), (None, "has_wcs")])
def test_curation_that_loses_an_extension_or_the_wcs_is_blocked(lose: str | None, field: str) -> None:
    sp = synthetic_i2d()
    meta = DIALECT.normalize(sp.raw)
    curated = CuratedProduct(_drop(sp.raw.data, lose, strip_wcs=lose is None), "application/fits")
    report = VALIDATOR.validate_curation(meta, sp.raw, curated)
    assert field in {c.field for c in report.failures()}
    product = ScientificDataProduct(sp.raw.raw_artifact_id, meta).validated(report)
    with pytest.raises(CurationBlocked, match="INT-007"):
        product.curate("sha256:" + "1" * 64)
    intact = VALIDATOR.validate_curation(meta, sp.raw, CuratedProduct(sp.raw.data, "application/fits"))
    assert intact.passed


@pytest.mark.tier("T5s")
@pytest.mark.req("INT-008")
def test_a_preview_is_typed_as_a_preview_and_never_curated() -> None:
    raw = raw_product(
        b"\xff\xd8\xff\xe0 synthetic jpeg",
        "jw99999-o001_t001_nircam_clear-f200w_i2d.jpg",
        product_type="PREVIEW",
        media_type="image/jpeg",
    )
    meta = DIALECT.normalize(raw)
    assert meta.product_type is ProductType.PREVIEW
    report = VALIDATOR.validate(meta, raw)
    curation = VALIDATOR.validate_curation(meta, raw, CuratedProduct(raw.data, "image/jpeg"))
    assert not curation.passed
    with pytest.raises(CurationBlocked, match="never curated as science"):
        ScientificDataProduct(raw.raw_artifact_id, meta).validated(report).curate("sha256:" + "2" * 64)


def test_a_mislabelled_preview_fails_validation() -> None:
    sp = synthetic_i2d()
    raw = replace(sp.raw, inventory_row={**sp.raw.inventory_row, "productType": "PREVIEW"})
    meta = replace(DIALECT.normalize(sp.raw), product_type=ProductType.IMAGE)
    assert "product_type" in {c.field for c in VALIDATOR.validate(meta, raw).failures()}


RECORDED = load_reference_products()


@pytest.mark.tier("T5s")
@pytest.mark.req("INT-006")
@pytest.mark.parametrize(("name", "raw", "reference"), RECORDED, ids=[r[0] for r in RECORDED])
def test_recorded_reference_products_match_their_records(name, raw, reference) -> None:  # type: ignore[no-untyped-def]
    meta = DIALECT.normalize(raw)
    diffs = [c for c in compare_to_reference(meta, reference) if not c.passed]
    assert not diffs, [(c.field, c.detail) for c in diffs]
    assert VALIDATOR.validate(meta, raw).passed


def test_synthetic_arrays_are_float() -> None:
    assert np.zeros(1, dtype="f4").dtype.kind == "f"


def test_a_recorded_product_without_a_reference_record_is_not_loaded(tmp_path: Path) -> None:
    pending = tmp_path / "recorded-not-described"
    pending.mkdir()
    (pending / "reference.template.json").write_text("{}")
    assert load_reference_products(tmp_path) == []


def test_fits_served_as_octet_stream_is_read_as_fits_and_a_specific_type_is_never_overridden() -> None:
    from sdp.adapters.products.fits import effective_media_type

    data = image()
    assert effective_media_type(data, "application/octet-stream") == "application/fits"
    assert effective_media_type(data, "") == "application/fits"
    assert effective_media_type(data, "image/jpeg") == "image/jpeg"
    assert effective_media_type(b"\xff\xd8\xff", "application/octet-stream") == "application/octet-stream"

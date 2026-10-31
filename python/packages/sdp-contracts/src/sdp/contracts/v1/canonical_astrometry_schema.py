# Generated from contracts/schemas by contracts/tools/generate_python.sh. Do not edit.

from __future__ import annotations

from typing import Annotated, Any
from pydantic import BaseModel, ConfigDict, Field, RootModel
from enum import StrEnum


class AstrometricCorrItem(RootModel[float | None]):
    root: Annotated[float | None, Field(ge=-1.0, le=1.0)]


class AstrometricCorr(RootModel[list[AstrometricCorrItem | None]]):
    root: Annotated[
        list[AstrometricCorrItem | None],
        Field(
            description="Upper triangle of the 5x5 correlation matrix in Gaia order (ra, dec, parallax, pmra, pmdec): ra_dec, ra_parallax, ra_pmra, ra_pmdec, dec_parallax, dec_pmra, dec_pmdec, parallax_pmra, parallax_pmdec, pmra_pmdec.",
            max_length=10,
            min_length=10,
        ),
    ]


class QualityClass(StrEnum):
    GOOD = "good"
    MARGINAL = "marginal"
    POOR = "poor"
    UNKNOWN = "unknown"


class CanonicalAstrometryRow(BaseModel):
    """
    One row of the canonical astrometry table (§8). Curated Parquet carries exactly these columns; x-unit names the astropy unit of each physical column.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    source_key: Annotated[str, Field(pattern="^[a-z0-9]+:([A-Za-z0-9._-]+:)?[^\\s]+$")]
    ra_deg: Annotated[float, Field(ge=0.0, lt=360.0)]
    dec_deg: Annotated[float, Field(ge=-90.0, le=90.0)]
    epoch_jyear: float
    parallax_mas: float | None
    parallax_mas_err: Annotated[float | None, Field(ge=0.0)]
    pm_ra_cosdec_masyr: float | None
    pm_ra_cosdec_masyr_err: Annotated[float | None, Field(ge=0.0)]
    pm_dec_masyr: float | None
    pm_dec_masyr_err: Annotated[float | None, Field(ge=0.0)]
    astrometric_corr: Annotated[
        AstrometricCorr | None,
        Field(
            description="Upper triangle of the 5x5 correlation matrix in Gaia order (ra, dec, parallax, pmra, pmdec): ra_dec, ra_parallax, ra_pmra, ra_pmdec, dec_parallax, dec_pmra, dec_pmdec, parallax_pmra, parallax_pmdec, pmra_pmdec."
        ),
    ]
    g_mag: float | None
    bp_rp: float | None
    j_mag: Annotated[
        float | None,
        Field(description="2MASS J magnitude, null for sources without it. Optional since 1.1.0."),
    ] = None
    h_mag: Annotated[
        float | None,
        Field(description="2MASS H magnitude, null for sources without it. Optional since 1.1.0."),
    ] = None
    k_mag: Annotated[
        float | None,
        Field(description="2MASS Ks magnitude, null for sources without it. Optional since 1.1.0."),
    ] = None
    ruwe: Annotated[float | None, Field(ge=0.0)]
    quality_flags: dict[str, Any]
    quality_class: QualityClass
    zero_point_corrected: Annotated[
        bool,
        Field(description="Parallax zero-point correction is a recorded flag, never silently applied."),
    ]
    source_release: Annotated[str, Field(pattern="^[a-z0-9]+:[A-Za-z0-9._-]+$")]

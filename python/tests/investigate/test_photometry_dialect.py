"""T4: photometric-corroboration reads 2MASS rows exactly as the 2MASS dialect curates them (#141).

The field tests' synthetic 2MASS archive writes ``j_mag`` and ``k_mag`` itself, so it could not see
the dialect keep the magnitudes only in ``quality_flags``: on Praesepe the test examined no star.
Here every 2MASS row comes out of the packaged ``2mass-psc`` dialect, so the two cannot drift apart."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from sdp.adapters.archives.dialect import load_dialects
from sdp.kernel.domain.ids import ArtifactId
from sdp.kernel.domain.investigation import EvidenceBundle, IdentificationMethod, IdentifiedObject
from sdp_investigate.photometry import PhotometricCorroboration

pytestmark = pytest.mark.tier("T4")

TWOMASS = load_dialects()["2mass-psc"]
GAIA_ART = ArtifactId("sha256:" + "a" * 64)
IR_ART = ArtifactId("sha256:" + "b" * 64)
THRESHOLDS = {"source_id": "gaia", "ir_source_id": "2mass"}
OUTLIER = 7


def _bundle(i: int, bp_rp: float, j_m: float | None, k_m: float | None) -> EvidenceBundle:
    key = f"gaia:DR3:{i}"
    ra, dec = 130.0 + i * 1e-3, 19.7
    native: dict[str, Any] = {
        "designation": f"0840{i:04d}+1942000",
        "ra": ra,
        "dec": dec,
        "jdate": 2451545.0,
        "j_m": j_m,
        "h_m": None if j_m is None else j_m - 0.3,
        "k_m": k_m,
        "ph_qual": "AAA",
    }
    gaia_row = {"source_key": key, "ra_deg": ra, "dec_deg": dec, "bp_rp": bp_rp, "g_mag": 12.0}
    obj = IdentifiedObject(key, IdentificationMethod.CATALOGUE, ra, dec, (GAIA_ART,), gaia_source_key=key)
    return EvidenceBundle(
        object=obj,
        catalogue_rows={"gaia": (gaia_row,), "2mass": (TWOMASS.row(native),)},
        catalogue_artifacts={"gaia": GAIA_ART, "2mass": IR_ART},
    )


def _field() -> list[EvidenceBundle]:
    rng = np.random.default_rng(141)
    bundles = []
    for i in range(40):
        bp_rp = 0.5 + i * 0.05
        jk = 0.1 + 0.5 * bp_rp + rng.normal(0.0, 0.02) + (1.5 if i == OUTLIER else 0.0)
        bundles.append(_bundle(i, bp_rp, 11.0 + jk, 11.0))
    bundles.append(_bundle(99, 1.0, None, None))  # a 2MASS row without photometry is skipped, not a crash
    return bundles


@pytest.mark.req("DSI-020")
def test_the_2mass_dialect_puts_j_h_and_k_in_canonical_columns() -> None:
    row = TWOMASS.row(
        {"designation": "x", "ra": 1.0, "dec": 2.0, "jdate": 2451545.0, "j_m": 12.5, "h_m": 12.0, "k_m": 11.75}
        | {"ph_qual": "AAA"}
    )
    assert (row["j_mag"], row["h_mag"], row["k_mag"]) == (12.5, 12.0, 11.75)


@pytest.mark.req("DSI-020")
@pytest.mark.scientific
def test_photometric_corroboration_examines_dialect_curated_2mass_rows() -> None:
    test = PhotometricCorroboration()
    features = test.features(_field(), THRESHOLDS, None)  # type: ignore[arg-type]
    assert len(features.keys) == 40
    scored = {s.subject.object_key: s for s in test.evaluate(features, THRESHOLDS, np.random.default_rng(0))}
    p = {k: s.p_value for k, s in scored.items() if s.p_value is not None}
    assert len(p) == 40
    assert p.pop(f"gaia:DR3:{OUTLIER}") < 1e-6
    assert min(p.values()) > 1e-4

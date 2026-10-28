"""Synthetic observation bundles with a planted deflection: the recovery fixtures for the fitter,
the model comparison and the independent implementation. They are not 1919 data and say so.

    python -m sdp_relativity.synthetic out.json [--alpha 1.75] [--seed 1919]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np

from sdp_relativity.geometry import StarGeometry
from sdp_relativity.measurement import PROBABLE_TO_STANDARD
from sdp_relativity.observations import BUNDLE_SCHEMA

SOLAR_RADIUS_ARCMIN = 15.78


@dataclass(frozen=True)
class InstrumentDesign:
    instrument: str
    plates: int
    stars: int
    probable_error_arcsec: float
    #: True scale error on each plate, unknown to the check plates (the astrographic focus problem).
    hidden_scale_arcsec_per_arcmin: float = 0.0
    check_scale_sigma: float = 0.002


DESIGNS: tuple[InstrumentDesign, ...] = (
    InstrumentDesign("sobral-4in", plates=7, stars=7, probable_error_arcsec=0.12),
    InstrumentDesign(
        "sobral-astro", plates=8, stars=10, probable_error_arcsec=0.25, hidden_scale_arcsec_per_arcmin=0.004
    ),
    InstrumentDesign("principe-astro", plates=2, stars=5, probable_error_arcsec=0.3),
)


def make_bundle(
    alpha_arcsec: float = 1.75,
    seed: int = 1919,
    designs: tuple[InstrumentDesign, ...] = DESIGNS,
    *,
    verified: bool = True,
    locators: bool = True,
) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    sets: list[dict[str, Any]] = []
    records: dict[str, str] = {}
    verifications: list[dict[str, Any]] = []
    for design in designs:
        positions = []
        for _ in range(design.stars):
            r = SOLAR_RADIUS_ARCMIN * rng.uniform(1.6, 6.0)
            theta = rng.uniform(0, 2 * math.pi)
            positions.append((r * math.cos(theta), r * math.sin(theta)))
        plates = []
        for p in range(design.plates):
            plate_id = f"{design.instrument}-{p + 1}"
            offset = rng.normal(0.0, 0.5, 2)
            scale = rng.normal(0.0, 0.01)
            rotation = rng.normal(0.0, 0.01)
            check_scale = scale - design.hidden_scale_arcsec_per_arcmin + rng.normal(0.0, design.check_scale_sigma)
            stars = []
            for s, (x, y) in enumerate(positions):
                gx, gy = StarGeometry(x, y, SOLAR_RADIUS_ARCMIN).response
                sigma = design.probable_error_arcsec * PROBABLE_TO_STANDARD
                dx = offset[0] + scale * x + rotation * y + alpha_arcsec * gx + rng.normal(0.0, sigma)
                dy = offset[1] + scale * y - rotation * x + alpha_arcsec * gy + rng.normal(0.0, sigma)
                record_id = f"syn-{plate_id}-{s + 1}"
                locator = f"synthetic generator, plate {plate_id}, star {s + 1}" if locators else ""
                records[record_id] = locator
                if verified:
                    verifications.append({"record_id": record_id, "verifier": "second-reader", "matches": True})
                stars.append(
                    {
                        "star_id": str(s + 1),
                        "record_id": record_id,
                        "locator": locator,
                        "transcriber": "synthetic-generator",
                        "x_arcmin": round(x, 4),
                        "y_arcmin": round(y, 4),
                        "dx_arcsec": round(dx, 4),
                        "dy_arcsec": round(dy, 4),
                        "error_arcsec": design.probable_error_arcsec,
                        "convention": "probable_error",
                    }
                )
            plates.append(
                {
                    "plate_id": plate_id,
                    "check_scale": round(check_scale, 6),
                    "check_rotation": round(rotation, 6),
                    "check_scale_sigma": design.check_scale_sigma,
                    "stars": stars,
                }
            )
        sets.append({"instrument": design.instrument, "solar_radius_arcmin": SOLAR_RADIUS_ARCMIN, "plates": plates})
    return {
        "schema": BUNDLE_SCHEMA,
        "instrument_sets": sets,
        "records": records,
        "verifications": verifications,
        "provenance": {
            "kind": "synthetic",
            "planted_alpha_arcsec": alpha_arcsec,
            "seed": seed,
            "note": "Synthetic recovery fixture; not 1919 data.",
        },
    }


def dumps(bundle: Mapping[str, Any]) -> bytes:
    return json.dumps(bundle, sort_keys=True, indent=1).encode()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m sdp_relativity.synthetic")
    parser.add_argument("out")
    parser.add_argument("--alpha", type=float, default=1.75)
    parser.add_argument("--seed", type=int, default=1919)
    args = parser.parse_args(argv)
    with open(args.out, "wb") as handle:
        handle.write(dumps(make_bundle(args.alpha, args.seed)))
    sys.stdout.write(f"wrote {args.out}\n")


if __name__ == "__main__":
    main()

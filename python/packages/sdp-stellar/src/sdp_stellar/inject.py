"""SyntheticClusterInjector: plants a cluster of declared richness, distance and dispersion (STL-006).

Members get a common parallax (1000 / distance), a proper-motion centre drawn from the field,
the declared proper-motion dispersion, a 3 pc projected core radius, and magnitudes, colours and
uncertainties resampled from the field so they pass the same quality cuts. Seeded.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pyarrow as pa

from sdp_stellar.quality import column

CORE_RADIUS_PC = 3.0


def inject(
    table: pa.Table,
    region: Mapping[str, float],
    richness: int,
    distance_pc: float,
    pm_dispersion_masyr: float,
    seed: int,
) -> tuple[pa.Table, np.ndarray]:
    """The field with the cluster appended, and the boolean mask of injected rows."""
    rng = np.random.default_rng(seed)
    n = table.num_rows
    donors = rng.choice(n, richness, replace=n < richness)
    rows = table.take(pa.array(donors)).to_pylist()
    # place the centre within half the region radius, uniformly in area
    r = 0.5 * region["radius_deg"] * np.sqrt(rng.uniform())
    theta = rng.uniform(0, 2 * np.pi)
    dec0 = region["dec_deg"] + r * np.sin(theta)
    ra0 = region["ra_deg"] + r * np.cos(theta) / np.cos(np.radians(region["dec_deg"]))
    core_deg = np.degrees(CORE_RADIUS_PC / distance_pc)
    anchor = int(rng.integers(n))
    pm0 = (column(table, "pm_ra_cosdec_masyr")[anchor], column(table, "pm_dec_masyr")[anchor])
    plx = 1000.0 / distance_pc
    for i, row in enumerate(rows):
        row["source_key"] = f"synthetic:injected:{seed}:{i}"
        row["dec_deg"] = float(dec0 + rng.normal(0, core_deg))
        row["ra_deg"] = float((ra0 + rng.normal(0, core_deg) / np.cos(np.radians(dec0))) % 360.0)
        row["pm_ra_cosdec_masyr"] = float(pm0[0] + rng.normal(0, pm_dispersion_masyr))
        row["pm_dec_masyr"] = float(pm0[1] + rng.normal(0, pm_dispersion_masyr))
        err = row.get("parallax_mas_err") or 0.05
        row["parallax_mas"] = float(plx + rng.normal(0, err))
        row["source_release"] = "synthetic:injected"
    injected = pa.Table.from_pylist(rows, schema=table.schema)
    return pa.concat_tables([table, injected]), np.concatenate([np.zeros(n, dtype=bool), np.ones(richness, dtype=bool)])


def recovery(labels: np.ndarray, injected: np.ndarray, threshold: float = 0.5) -> dict[str, Any]:
    """The found cluster that best overlaps the injected stars (Jaccard), and whether it recovers them:
    it holds at least ``threshold`` of the injected stars and is at least ``threshold`` injected itself.
    Best overlap, not best completeness, so a field-sized cluster swallowing the injection does not
    count as the match."""
    best: dict[str, Any] = {"recovered": False, "completeness": 0.0, "purity": 0.0}
    best_j = 0.0
    total = int(injected.sum())
    for label in sorted(set(labels[injected].tolist()) - {-1}):
        members = labels == label
        hit = int((members & injected).sum())
        size = int(members.sum())
        j = hit / (total + size - hit)
        if j > best_j:
            best_j = j
            completeness, purity = hit / total, hit / size
            best = {
                "recovered": completeness >= threshold and purity >= threshold,
                "completeness": completeness,
                "purity": purity,
            }
    return best

"""Quality cuts as recorded configuration (STL-003): applied to canonical astrometry, counted per cut."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import pyarrow as pa


def column(table: pa.Table, name: str) -> np.ndarray:
    return np.asarray(table.column(name).to_numpy(zero_copy_only=False), dtype=np.float64)


@dataclass(frozen=True)
class QualityCuts:
    ruwe_max: float
    parallax_over_error_min: float
    g_mag_max: float
    astrometric_params_solved: tuple[int, ...]

    @classmethod
    def from_parameters(cls, doc: Mapping[str, Any]) -> QualityCuts:
        return cls(
            float(doc["ruwe_max"]),
            float(doc["parallax_over_error_min"]),
            float(doc["g_mag_max"]),
            tuple(int(v) for v in doc["astrometric_params_solved"]),
        )

    def to_document(self) -> dict[str, Any]:
        return {
            "ruwe_max": self.ruwe_max,
            "parallax_over_error_min": self.parallax_over_error_min,
            "g_mag_max": self.g_mag_max,
            "astrometric_params_solved": list(self.astrometric_params_solved),
        }

    def masks(self, table: pa.Table) -> dict[str, np.ndarray]:
        ruwe = column(table, "ruwe")
        plx, plx_err = column(table, "parallax_mas"), column(table, "parallax_mas_err")
        g = column(table, "g_mag")
        solved = np.asarray([_solved(f) for f in table.column("quality_flags").to_pylist()])
        usable_err = np.isfinite(plx_err) & (plx_err > 0)
        with np.errstate(divide="ignore", invalid="ignore"):
            poe = np.where(usable_err, plx / np.where(usable_err, plx_err, 1.0), np.nan)
        astrometry = np.isfinite(column(table, "pm_ra_cosdec_masyr")) & np.isfinite(column(table, "pm_dec_masyr"))
        return {
            "five_parameter_astrometry": astrometry & np.isfinite(plx),
            "ruwe": np.nan_to_num(ruwe, nan=np.inf) <= self.ruwe_max,
            "parallax_over_error": np.isfinite(poe) & (np.nan_to_num(poe, nan=-np.inf) >= self.parallax_over_error_min),
            "g_mag": np.nan_to_num(g, nan=np.inf) <= self.g_mag_max,
            "astrometric_params_solved": np.isin(solved, self.astrometric_params_solved),
        }

    def apply(self, table: pa.Table) -> tuple[pa.Table, dict[str, Any]]:
        masks = self.masks(table)
        keep = np.logical_and.reduce(list(masks.values())) if masks else np.ones(table.num_rows, dtype=bool)
        report = {
            "cuts": self.to_document(),
            "input_rows": table.num_rows,
            "kept_rows": int(keep.sum()),
            # each cut's failures among all input rows; one star can fail several
            "failed_by_cut": {name: int((~m).sum()) for name, m in masks.items()},
            # rows each cut removed when applied in this order, so they sum to input_rows - kept_rows
            "removed_in_order": _removed_in_order(masks, table.num_rows),
        }
        return table.filter(pa.array(keep)), report


def _removed_in_order(masks: Mapping[str, np.ndarray], n: int) -> dict[str, int]:
    remaining = np.ones(n, dtype=bool)
    out: dict[str, int] = {}
    for name, m in masks.items():
        out[name] = int((remaining & ~m).sum())
        remaining &= m
    return out


def _solved(flags: str | None) -> int:
    if not flags:
        return -1
    value = json.loads(flags).get("astrometric_params_solved")
    return -1 if value is None else int(value)


def require_columns(table: pa.Table, names: Sequence[str]) -> None:
    missing = [n for n in names if n not in table.column_names]
    if missing:
        raise ValueError(f"canonical astrometry lacks {missing}")

"""Cut a committed slice of a real data table for the T8R tier (TST-011).

The slice is chosen by rule, never by hand, so anyone can cut it again from the same full table and get the
same bytes. The full table stays where it was pulled (the project share, or SDP_REALDATA on the PC).

    uv run python tools/realdata_slice.py nss-orbits <gaia-dr3-orbits.parquet> <out.parquet>

``nss-orbits`` keeps, from the full Gaia DR3 orbit table the companion screen reads:

- every orbit the screen flags (a dark partner or a neutron star or black hole), so the field list is whole;
- every orbit near one of the screen's cuts (significance, parallax over error, the Chandrasekhar mass),
  where a change of code is most likely to move a verdict;
- every orbit whose source_id is a multiple of 50, for the ordinary, no-mass and unused verdicts.

Only the columns the screen reads are kept. The settings file is read, never changed.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from sdp_stellar import companions as c

NSS_COLUMNS = (
    "source_id",
    "nss_solution_type",
    "combination_method",
    "period",
    "a_thiele_innes",
    "b_thiele_innes",
    "f_thiele_innes",
    "g_thiele_innes",
    "parallax",
    "parallax_error",
    "significance",
    "phot_g_mean_mag",
    "m1",
    "m2",
    "m2_lower",
    "fluxratio_upper",
    "flag",
)
#: an orbit within this fraction of a cut counts as near it
NEAR = 0.2
EVERY = 50


def nss_keep(orbits: dict[str, np.ndarray]) -> np.ndarray:
    """The rows of the slice, as a boolean mask over the full table."""
    settings = c.load_settings()
    masses, verdict = c.judge(orbits, settings)
    o, m = settings["orbits"], settings["mass"]

    def near(x: np.ndarray, cut: float) -> np.ndarray:
        x = np.asarray(x, dtype=float)
        return np.isfinite(x) & (np.abs(x - cut) <= NEAR * cut)

    with np.errstate(divide="ignore", invalid="ignore"):
        poe = np.asarray(orbits["parallax"], dtype=float) / np.asarray(orbits["parallax_error"], dtype=float)
    keep = np.isin(verdict, c.FLAGGED)
    keep |= near(orbits["significance"], float(o["significance_min"]))
    keep |= near(poe, float(o["parallax_over_error_min"]))
    keep |= near(masses["m2_lower_msun"], float(m["chandrasekhar_msun"]))
    keep |= np.asarray(orbits["source_id"]) % EVERY == 0
    return keep


def nss_orbits(src: Path, out: Path) -> int:
    table = pq.read_table(src, columns=list(NSS_COLUMNS))
    orbits = {n: table.column(n).to_numpy(zero_copy_only=False) for n in table.column_names}
    keep = nss_keep(orbits)
    sliced = table.filter(pa.array(keep)).sort_by("source_id")
    meta = dict(pq.read_schema(src).metadata or {})
    meta[b"sdp.slice"] = (
        f"tools/realdata_slice.py nss-orbits: flagged, within {NEAR:.0%} of a cut, or source_id % {EVERY} == 0; "
        f"{int(keep.sum())} of {len(keep)} orbits"
    ).encode()
    sliced = sliced.replace_schema_metadata(meta)
    # fixed writer options so the same input gives the same bytes
    pq.write_table(sliced, out, compression="zstd", compression_level=9, write_statistics=False, store_schema=True)
    return int(keep.sum())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="what", required=True)
    nss = sub.add_parser("nss-orbits", help="slice the Gaia DR3 orbit table the companion screen reads")
    nss.add_argument("src", type=Path)
    nss.add_argument("out", type=Path)
    args = parser.parse_args()
    n = nss_orbits(args.src, args.out)
    print(f"{n} orbits -> {args.out}")


if __name__ == "__main__":
    main()

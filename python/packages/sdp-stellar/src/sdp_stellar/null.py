"""NullFieldGenerator: destroys real kinematic structure, keeps selection effects (STL-006).

Within each 0.5 mag G bin, proper motion in RA, proper motion in Dec and parallax are each permuted
independently, every value travelling with its own uncertainty. Magnitude-dependent errors and the
field's marginal distributions survive, but no star keeps its neighbours' motion and no group keeps
a shared (pm, parallax) clump. Seeded; the seed is recorded with every null field.
"""

from __future__ import annotations

import numpy as np
import pyarrow as pa

from sdp_stellar.quality import column

#: each group is permuted with its own draw
SHUFFLED = (
    ("pm_ra_cosdec_masyr", "pm_ra_cosdec_masyr_err"),
    ("pm_dec_masyr", "pm_dec_masyr_err"),
    ("parallax_mas", "parallax_mas_err"),
)
BIN_MAG = 0.5


def null_field(table: pa.Table, seed: int) -> pa.Table:
    rng = np.random.default_rng(seed)
    g = np.nan_to_num(column(table, "g_mag"), nan=99.0)
    bins = np.floor(g / BIN_MAG).astype(np.int64)
    order = np.arange(table.num_rows)
    out = table
    for group in SHUFFLED:
        source = order.copy()
        for b in np.unique(bins):
            rows = order[bins == b]
            source[rows] = rng.permutation(rows)
        for name in group:
            if name in table.column_names:
                out = out.set_column(out.schema.get_field_index(name), name, table.column(name).take(pa.array(source)))
    return out

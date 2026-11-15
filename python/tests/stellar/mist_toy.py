"""Invented isochrones in MIST's ``.iso.cmd`` layout, for tests only. NOT MIST models: a main sequence
with a turnoff whose mass falls with age as t = 10 Gyr (M / Msun)^-2.5 and a red giant branch rising
from it, with metallicity shifting colour and magnitude. Only the layout copies MIST's."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import numpy as np

NAMES = (
    "EEP", "log10_isochrone_age_yr", "initial_mass", "star_mass", "log_Teff", "log_g", "log_L",
    "[Fe/H]_init", "[Fe/H]", "Gaia_G_DR2Rev", "Gaia_BP_DR2Rev", "Gaia_RP_DR2Rev", "Gaia_G_MAW", "phase",
)  # fmt: skip
AGES = tuple(round(6.5 + 0.05 * i, 2) for i in range(74))  # 6.5 .. 10.15
FEHS = (-0.5, -0.25, 0.0, 0.25)


def turnoff(log_age: float) -> float:
    return float((10 ** (10.0 - log_age)) ** 0.4)


def toy_isochrone(feh: float, log_age: float) -> dict[str, np.ndarray]:
    m_to = turnoff(log_age)
    ms = np.geomspace(0.15, min(m_to, 40.0), 160)
    x = (ms / m_to) ** 8
    colour = 0.82 - 1.6 * np.log10(ms) + 0.15 * feh + 0.1 * x
    mag = 4.8 - 8.0 * np.log10(ms) + 0.3 * feh - 0.7 * x
    n_rgb = 40
    t = np.linspace(0.0, 1.0, n_rgb + 1)[1:]
    rgb_m = m_to * (1.0 + 0.02 * t)
    rgb_c = colour[-1] + 0.1 + (1.6 - colour[-1]) * t
    rgb_g = mag[-1] - 0.3 - (mag[-1] + 2.5) * t**0.7
    mi = np.concatenate([ms, rgb_m])
    col = np.concatenate([colour, rgb_c])
    g = np.concatenate([mag, rgb_g])
    phase = np.concatenate([np.zeros(ms.size), np.full(n_rgb, 2.0)])
    star_mass = mi * np.where(phase == 2, 1.0 - 0.1 * np.concatenate([np.zeros(ms.size), t]), 1.0)
    bp = g + 0.3 + 0.5 * col
    return {
        "EEP": np.arange(202, 202 + mi.size, dtype=float),
        "log10_isochrone_age_yr": np.full(mi.size, log_age),
        "initial_mass": mi,
        "star_mass": star_mass,
        "log_Teff": 3.76 - 0.2 * (col - 0.8),
        "log_g": 4.4 - 0.1 * (4.8 - g),
        "log_L": (4.8 - g) / 2.5,
        "[Fe/H]_init": np.full(mi.size, feh),
        "[Fe/H]": np.full(mi.size, feh),
        "Gaia_G_DR2Rev": g,
        "Gaia_BP_DR2Rev": bp,
        "Gaia_RP_DR2Rev": bp - col,
        "Gaia_G_MAW": g + 0.01,
        "phase": phase,
    }


def toy_cmd_text(feh: float, ages: Sequence[float] = AGES, av: float = 0.0, names: Sequence[str] = NAMES) -> str:
    rule = "# " + "-" * 86
    # the header layout of MIST's own reader (MIST_codes read_mist_models.py, ISOCMD): line 3 holds
    # the photometric system after "=", lines 5-6 the composition, line 8 the number of isochrones and
    # line 9 ends with A_V
    out = [
        "# MIST version number  = 1.2",
        "# MESA revision number =     7503",
        "# photometric system   = UBV(RI)c + 2MASS + Kepler + Hipparcos + Gaia (DR2/MAW)",
        rule,
        "#  Yinit        Zinit   [Fe/H]   [a/Fe]  v/vcrit",
        f"#  0.2703  1.42857E-02  {feh:7.2f}     0.00     0.40",
        rule,
        f"# number of isochrones = {len(ages):4d}",
        f"# Av = {av:6.4f}",
        rule,
    ]
    for age in ages:
        iso = toy_isochrone(feh, age)
        n = iso["EEP"].size
        out.append(f"# number of EEPs, cols = {n:6d} {len(names):5d}")
        out.append("#" + "".join(f"{i + 1:>25d}" for i in range(len(names))))
        out.append("#" + "".join(f"{name:>25s}" for name in names))
        for r in range(n):
            out.append(" " + "".join(f"{iso[name][r]:25.10E}" for name in names))
        out.append("")
        out.append("")
    return "\n".join(out) + "\n"


def cluster(
    feh: float,
    log_age: float,
    av: float,
    modulus: float,
    n: int,
    seed: int,
    settings: dict[str, Any],
    binaries: int = 0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Observed BP-RP and G of n stars drawn along a toy isochrone (masses uniform in log mass on the main
    sequence, a few giants), with the settings' extinction and errors; ``binaries`` of them made equal
    mass pairs, 0.75 mag brighter. Returns colour, G and the true initial masses."""
    from sdp_stellar.isochrones import extinction, photometric_errors

    rng = np.random.default_rng(seed)
    iso = toy_isochrone(feh, log_age)
    on_ms = np.nonzero((iso["phase"] == 0) & (iso["Gaia_G_DR2Rev"] + modulus < 18.5))[0]
    rows = np.concatenate([rng.choice(on_ms, n - 3), rng.choice(np.nonzero(iso["phase"] == 2)[0], 3)])
    col = iso["Gaia_BP_DR2Rev"][rows] - iso["Gaia_RP_DR2Rev"][rows]
    g = iso["Gaia_G_DR2Rev"][rows]
    a_g, e = extinction(av, settings)
    g_obs = g + modulus + float(a_g)
    c_obs = col + float(e)
    g_obs[:binaries] -= 2.5 * math.log10(2.0)
    ce, ge = photometric_errors(g_obs, settings)
    noise = 0.5  # draw inside the floors, which also stand for the models' own error
    return c_obs + rng.normal(0, ce * noise), g_obs + rng.normal(0, ge * noise), iso["initial_mass"][rows]

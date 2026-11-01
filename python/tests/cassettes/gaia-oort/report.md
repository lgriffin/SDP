# UC-04: Oort (1927) rerun with Gaia

Choices SHA-256 `8370ef0ebed703a043b9b1cf89d6a58efb45b029c161ae71c1f349bc0549805f`, match committed: True.

## Part A: the 1927 arithmetic

- Mean A over Table 2 (weights 1/error^2): 0.0306 ± 0.0036 km/s/pc; printed 0.0310 ± 0.0037
- B3-B5: r A x parallax = 0.0331, printed A 0.033
- c-stars (faint): r A x parallax = 0.0377, printed A 0.038
- R with K2/K1 = 0.11: 5,929 pc, printed 5,900
- R with K2/K1 = 0.29: 5,101 pc, printed 5,100
- R with K2/K1 = 0: 6,581 pc, printed 6,600
- Central mass: 9.14e+10 M_sun; printed at least 8e+10

## Proper motions (decides)

Baseline `d=0.3-1kpc |b|<30 G<15 zp=-0.017 all`: 6,638,336 stars, 117,013 clipped.

| | value | error |
|---|---|---|
| U | 9.95 | 0.02 |
| V | 21.93 | 0.01 |
| W | 7.66 | 0.01 |
| A | 15.22 | 0.02 |
| B | -13.24 | 0.02 |
| C | -2.97 | 0.02 |
| K | -2.29 | 0.05 |

Amplitude hypot(A, C) 15.51 ± 0.02 km/s/kpc, l0 5.5 ± 0.0 deg (modern galactic longitude).

**supports_UC04_H1**: 48 scenarios detect rotation, 0 are null, 0 failed.

Against Oort's A = 31.0 ± 3.7 km/s/kpc: **disagrees_with_oort** (0 of 48 scenarios within).

## Radial velocities (Oort's method)

Baseline `d=0.3-1kpc |b|<30 G<15 zp=-0.017 all`: 6,026,902 stars, 31,499 clipped.

| | value | error |
|---|---|---|
| U | 9.97 | 0.02 |
| V | 21.07 | 0.02 |
| W | 7.47 | 0.05 |
| A | 15.15 | 0.03 |
| C | -2.91 | 0.03 |
| K | -2.70 | 0.02 |

Amplitude hypot(A, C) 15.43 ± 0.03 km/s/kpc, l0 5.4 ± 0.0 deg (modern galactic longitude).

**supports_UC04_H1**: 48 scenarios detect rotation, 0 are null, 0 failed.

Against Oort's A = 31.0 ± 3.7 km/s/kpc: **disagrees_with_oort** (0 of 48 scenarios within).

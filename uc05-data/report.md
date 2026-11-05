# UC-05: Oort (1932) rerun with Gaia

Choices SHA-256 `fc60bffeca39690c59c6b51eb4dfc6119e9bd26873d57520e5b5f9cb7f905828`, match committed: True.

## Part A: the 1932 arithmetic

- Formula (9) constant: 7.462e-09, printed 7.48e-09
- Z0 constant: 6.171e+08, printed 6.17e+08; Z0 to reach 500 pc: 30.3 km/s, printed 30
- Shape factor x at a/c = 5.0: 0.7505, printed 0.750
- Shape factor x at a/c = 10.0: 0.8608, printed 0.860
- Shape factor x at a/c = 30.0: 0.9498, printed 0.950
- Shape factor x at a/c = infinity: 1.0000, printed 1.000
- dK/dz through the origin over z<=150: 5.39e-30 s^-2, printed 5.62e-30
- dK/dz through the origin over z<=200: 5.56e-30 s^-2, printed 5.62e-30
- Density for a flat layer from his dK/dz: 0.0990 Msun/pc^3; his cases {'I a': 0.108, 'I b': 0.093, 'II a': 0.089, 'II b': 0.079}, adopted 0.092
- Plateau: 3.787e-9 cm/s^2, printed 3.8e-9
- 1 Msun/pc^3 = 6.768e-23 g/cm^3, printed 6.85e-23

## Part B: the force from Gaia

Baseline `all R<300pc bin=50pc zp=-0.017 clip=on`: 718,002 density stars, 681,444 with velocities; the Sun 8.5 pc above the plane (Oort's tentative 30).

- Slope near the plane k1 = 1.81e-30 ± 1.75e-31 s^-2 (0.32 of Oort's 5.62e-30)
- Flattening over 200-500 pc: 1.22 ± 0.23 (Oort's force: about 0)
- Plateau |K| = 2.29 ± 0.06 e-9 cm/s^2 (Oort: 3.8)
- Local density rho0 = 0.0299 ± 0.0031 Msun/pc^3 (Oort: 0.092)
- rho0 less Oort's visible stars (0.038): -0.0081

| z (pc) | Gaia K (1e-9 cm/s^2) | Oort mean corrected | Oort preliminary |
|---|---|---|---|
| 50 | -0.43 ± 0.37 | 0.77 | 1.15 |
| 100 | 0.65 ± 0.19 | 1.55 | 2.3 |
| 150 | 1.02 ± 0.18 | 2.59 | 3.15 |
| 200 | 1.12 ± 0.22 | 3.52 | 3.8 |
| 250 | 1.49 ± 0.28 | 3.78 | 3.8 |
| 300 | 1.95 ± 0.34 | 3.86 | 3.8 |
| 400 | 2.48 ± 0.42 | 3.68 | 3.75 |
| 600 | 2.89 ± 0.76 | 4.44 | 3.8 |

**inconclusive**: 0 scenarios flatten, 5 do not, 8 failed.

Against Oort's density 0.092 ± 0.027 Msun/pc^3: **mixed** (54 of 72 scenarios within).

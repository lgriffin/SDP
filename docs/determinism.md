# Determinism per TaskKind

NFR-008 asks that nondeterminism which prevents bitwise reproduction be documented per `TaskKind`. Each protocol registers a note with every kind it declares (`TaskKindRegistry.register(..., nondeterminism=...)`), and `ScientificProtocolContract` fails a protocol whose plan uses a kind with a non-`BITWISE` `NumericalPolicy` and no note. The notes below are copied from the protocol modules; `tests/architecture/test_determinism_doc.py` checks that every registered kind appears here.

What holds everywhere:

- Seeds are derived per task from the experiment seed and the task's `seed_offset` (`SeedHandling.DERIVED`), and recorded in the manifest. A seeded `numpy.random.default_rng` stream is bitwise for a given NumPy version.
- No protocol uses wall-clock time, hash ordering or unseeded randomness.
- GPU implementations never decide a gate. A GPU result is used only after its agreement record against the CPU reference passed (ADR-007, STL-004); otherwise the CPU reference runs.
- Results are compared with the task's `NumericalPolicy` tolerances (§11.2), so a change of BLAS build or thread count stays within tolerance rather than reproducing bitwise.

## Synthetic protocol (`sdp.testing.synthetic`)

| TaskKind | What prevents bitwise reproduction |
|---|---|
| `synthetic.fit_line` | least squares in float64; float64 reductions run through NumPy/BLAS, whose summation order can change with the BLAS build, CPU features and thread count, so results agree within the policy tolerance, not bitwise |
| `synthetic.residuals` | residual RMS in float64; float64 reductions run through NumPy/BLAS, whose summation order can change with the BLAS build, CPU features and thread count, so results agree within the policy tolerance, not bitwise |

## UC-02 Einstein 1919 (`sdp_relativity.protocol`)

| TaskKind | What prevents bitwise reproduction |
|---|---|
| `relativity.limb_models` | closed-form limb models in float64; libm transcendental functions may differ in the last ulp across platforms |
| `relativity.transcription_audit` | exact comparisons of transcribed values; float parsing is correctly rounded, so no known source beyond library versions |
| `relativity.instrument_sweep` | the plate bootstrap draws from numpy.random.default_rng with the derived task seed (bitwise for a given NumPy version); the fits are float64 SciPy optimisation, whose iterates can differ in the last bits across BLAS builds and thread counts |
| `relativity.pooled` | float64 SciPy optimisation and quadrature; iterates and summation order depend on the BLAS build and thread count |
| `relativity.independent_agreement` | compares two float64 fits (Python and TypeScript) within the policy tolerance; the TypeScript fitter uses its own summation order by design |
| `relativity.synthesis` | aggregates upstream float64 results; inherits their nondeterminism and adds none |

## UC-01 stellar clusters (`sdp_stellar.protocol`)

| TaskKind | What prevents bitwise reproduction |
|---|---|
| `stellar.prepare` | quality cuts and feature scaling in float64; NumPy reductions can change summation order across NumPy versions and CPU SIMD paths |
| `stellar.search` | HDBSCAN/DBSCAN from scikit-learn on float64 features: distance kernels go through BLAS and ties in the mutual-reachability tree are broken by row order, so labels can differ across scikit-learn versions and BLAS builds; a GPU implementation is used only after its agreement record passed (STL-004) |
| `stellar.candidates` | membership, stability and CMD coherence from upstream labels in float64; inherits stellar.search and adds only reduction-order effects |
| `stellar.null` | proper motions and parallaxes are permuted with numpy.random.default_rng seeded from the derived task seed (bitwise for a given NumPy version), then clustered as in stellar.search |
| `stellar.injection` | synthetic members are drawn with numpy.random.default_rng seeded from the derived task seed, then clustered as in stellar.search |
| `stellar.recovery` | bootstrap intervals draw from numpy.random.default_rng with the derived task seed; bitwise for a given NumPy version |
| `stellar.synthesis` | aggregates upstream results; inherits their nondeterminism and adds none |

Investigation test kinds, registered by `sdp.kernel.science.investigation`, are outside these tables: the Investigate module owns their notes.

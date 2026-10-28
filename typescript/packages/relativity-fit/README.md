# @sdp/relativity-fit

The independent implementation of the UC-02 deflection fitter (REL-007, REL-G-08). It was written from the model and scenario definitions in [`docs/protocols/uc02-einstein-1919.md`](../../../docs/protocols/uc02-einstein-1919.md) and the fitter's docstring, not from the Python code, and imports nothing from the workspace (dependency-cruiser rule `relativity-fit-is-independent`).

```
node src/cli.ts bundle.json --out independent-fit.json
```

writes the free-amplitude alpha, its uncertainty and the log-likelihood for every instrument and fit scenario. The output is the `independent_fit` input of a UC-02 run; the protocol compares it scenario by scenario with its own fits.

`test/fit.test.ts` checks it against `test/fixtures/python-fits-seed7.json`, and `python/tests/relativity/test_independent.py` checks that fixture is still what the reference fitter produces, so neither side can drift silently.

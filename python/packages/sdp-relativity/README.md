# sdp-relativity

The UC-02 science module: a re-examination of the 1919 eclipse deflection from published tables (architecture spec §9). It imports only the kernel's domain and science seams and registers itself as the `uc02-einstein-1919` entry point in `sdp.protocols`.

See [`docs/protocols/uc02-einstein-1919.md`](../../../docs/protocols/uc02-einstein-1919.md) for the research question, the fit, the gates and how to run it. The independent second fitter is [`typescript/packages/relativity-fit`](../../../typescript/packages/relativity-fit).

No 1919 data is included. Synthetic bundles (`python -m sdp_relativity.synthetic`) carry a planted deflection and say so in their provenance.

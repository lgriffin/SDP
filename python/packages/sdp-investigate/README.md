# sdp-investigate

The field tests Investigate runs (Investigate §5). Each is registered as a TaskKind with a CPU
reference implementation, a NumericalPolicy and a null model, and reads evidence bundles only.

| Test | Question | Null model |
|---|---|---|
| `parallax-consistency` | Does the object's distance stand out from the field, within its errors? | Field parallax distribution, resampled with each object's own error |
| `cmd-coherence` | Does the object sit off the field's colour–magnitude sequence? | Colours shuffled among the field's objects |
| `long-baseline-motion` | Does the 1880–2020 position history agree with the Gaia proper motion? | Gaia prediction with propagated errors |
| `variability` | Is the object variable over the available baseline? | Constant source with the series' own noise |
| `photometric-corroboration` | Do infrared colours agree with the Gaia-based classification? | Field colour distribution (shuffled) |
| `astrometric-coherence` | Is there a co-moving group here? | Proper motions and parallaxes shuffled within magnitude bins |

Products the tests read are canonical CSV (see `sdp_investigate.formats`); the adapters that fetch
them convert archive formats into these.

# sdp-imviz

The Investigate plugin for [Jdaviz](https://github.com/spacetelescope/jdaviz) Imviz. It reads the
viewer's footprint (or a typed region) as the field, offers the test profiles in a directory, calls
`InvestigateField` on `sdp-api` (`POST /investigations`), and overlays the candidates as markers. The
results are on `plg.results`, in the pattern of `imviz.get_catalog_source_results()`.

It is a driving adapter and nothing else (INV-012): no analysis, no archive calls, no kernel import.
Jdaviz keeps its own environment, so this package has its own lock file and is not part of the
`python/` workspace.

```bash
cd viewer/sdp-imviz
uv sync
uv run jupyter lab   # or any notebook front end
```

```python
import jdaviz
from sdp_imviz import attach

viz = jdaviz.App()  # or jdaviz.Imviz()
viz.load("field.fits")
plg = attach(viz, api="http://127.0.0.1:8000", profiles_dir="profiles/")
plg.profile = "stellar-field@1.0.0"
plg.investigate()  # footprint of the image viewer → InvestigateField
plg.results  # the run: set id, stages, candidates with positions
```

Every result is EXPLORATORY. Triage (park, close, promote) stays on the `sdp` CLI, which signs.

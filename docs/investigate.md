<!-- Companion to docs/architecture.md. Source: the Claude Doc "SDP — Investigate: Field to Candidate on the STScI Ecosystem" (https://claude.ai/artifact/P5NSns1hRWGK4i1kWSgfgN), transcribed 2026-10-28. -->

# SDP — Investigate: Field to Candidate on the STScI Ecosystem

2026-10-28 · Leigh

Open a field, select Investigate: the platform identifies the objects in it, retrieves what the archives already know about them, runs the tests you chose, and opens one investigation per candidate. It does this on the Python ecosystem that STScI and Astropy already maintain, which SDP finds, binds and integrates behind its ports rather than rebuilds.

## 1. The Investigate interaction

Investigate is one driving use case, `InvestigateField`, that runs four stages over a declared field under a versioned test profile and ends with one open investigation per candidate. The researcher starts it from the field viewer or the CLI and watches it fill, stage by stage, the way a dossier fills.

**What the researcher does**

1. Open a field. A `Field` is a sky region (cone, box or polygon) with an optional set of images and their WCS, and an epoch. It comes from the viewer's current footprint, from the CLI (`sdp investigate --region "icrs;circle(120.0,-10.0,0.5)"`), or from the researcher's own frames through the read-only local observation store.
1. Select Investigate and pick a profile. A profile is a versioned YAML document naming the tests to run, their thresholds and null models, and which archives to consult; its hash is recorded before anything runs.
1. Watch the four stages complete. Each stage emits domain events and writes artifacts, so progress shows in the viewer, the notebook and the evidence UI, and a failed stage leaves everything before it intact.
1. Triage the candidates. Each candidate has its own investigation and dossier; the researcher promotes, parks or closes them, and a promotion opens a predeclared experiment in the kernel.

Identify and Retrieve are the only stages that reach archives; Test reads the evidence bundles they produced, and Candidates is where exploration ends and the kernel's predeclared path can begin.

| Stage | Consumes | Produces | Runs through |
|---|---|---|---|
| Identify | Field, images, Gaia cone | `ObjectSet` with one identification record per object | `SourceDetector`, `ArchiveGateway`, `CrossMatchService`, `ObjectResolver` |
| Retrieve | `ObjectSet`, profile's archive list | Catalogue rows and observation indices per object, each a registered `DatasetArtifact` | `ArchiveGateway`, `ObservationArchiveGateway` |
| Test | Per-object evidence bundles, profile's tests | `TestResult` per object per test, null-model results per field | `ComputeExecutor` over registered `TaskKind`s |
| Candidates | Test results, ranking policy | One `Investigation` per candidate with a dossier; the set's manifest | Kernel application layer, evidence UI, viewer overlay |

## 2. Exploratory versus confirmatory

Investigate is exploratory by construction: it is how candidates are found, never how a claim is verified, and the kernel enforces the difference rather than leaving it to good intentions. An investigation never carries a `Verdict`; it carries results labelled `EXPLORATORY`, a full provenance record, and a count of how many candidates were examined.

**What keeps exploration honest**

- The profile is frozen before the run. Tests, thresholds and null models are declared and hashed first, so a run cannot be tuned against its own output; a changed profile is a new version and a new run.
- The multiplicity is recorded. The investigation-set manifest records every object examined and every test applied, so false-discovery control can be applied at set level and a reviewer can see how many draws preceded a candidate.
- Null fields run alongside. Each profile runs on shuffled and synthetic versions of the same field, giving an empirical false-positive rate for that profile on that field, not a textbook one.
- Nothing is deleted. Parked and closed investigations keep their dossiers and reasons; the set is as much evidence as the candidates.

**Promotion.** A candidate becomes a confirmatory claim only through `Experiment`, which requires a frozen predeclaration naming evidence the investigation did not use: a different archive, a later Gaia release, an independent epoch, or a held-out region. Promotion therefore moves a candidate from the exploratory ledger to the predeclaration ledger, and the architecture specification's gates and verdicts apply from that point unchanged.

| Investigation state | Meaning | Leaves |
|---|---|---|
| Open | Candidate created by a run; dossier assembled | Triage |
| Parked | Interesting, no confirmatory evidence available yet | Reason recorded; revisited when a new release or archive lands |
| Promoted | A confirmatory predeclaration has been frozen | Linked `Experiment` id; the investigation itself is read-only from here |
| Closed | Rejected at triage or failed confirmation | Reason recorded; retained in the set |

## 3. Stage 1 — Identify

Identify turns a field into an `ObjectSet` in which every object says how it was found; two routes exist, catalogue-first and image-first, and a profile names which one leads.

**Field definition.** The region is an Astropy `regions` sky region; images carry `astropy.wcs` or `gwcs` geometry and are aligned with `reproject` when more than one frame is present. The researcher's own frames enter through the read-only local observation store, never copied back.

**Catalogue-first (default for stellar structure).** A bounded Gaia cone over the region through `ArchiveGateway`, with the profile's quality cuts recorded rather than applied silently; every object is a Gaia source key with its canonical astrometry row. This is the route the stellar module's predeclared regions use.

**Image-first (for the researcher's own frames or archive cutouts).** Background estimation and detection with `photutils` (`Background2D`, `DAOStarFinder` or segmentation through `SourceFinder` and `SourceCatalog`), WCS refinement against Gaia with `tweakwcs`, then cross-identification of each detection with Gaia within a declared radius through `CrossMatchService`. Detections with no Gaia counterpart stay in the set, flagged, because an unmatched source is itself a candidate.

**Resolution.** Known objects are resolved through `ObjectResolver` (SIMBAD by name or position) to attach object type and bibcodes, so a rediscovery is recognised at stage one rather than at publication.

| Output field | Content |
|---|---|
| `object_key` | Gaia source key, or a field-local key for unmatched detections |
| `identification_method` | `catalogue`, `detection`, `resolved` |
| `pixel_xy`, `sky_icrs` | Both kept when an image was involved; the WCS artifact id with them |
| `match_radius_arcsec`, `match_distance_arcsec` | Declared and measured |
| `resolver_record` | SIMBAD main id, object type, bibcodes, or null |
| `source_artifacts` | Image, catalogue chunk and WCS artifacts that produced this row |

**Ports introduced here:** `SourceDetector` (adapter `PhotutilsDetector`, fake `PlantedDetector`), `WcsRefiner` (adapter `TweakwcsRefiner`), `ObjectResolver` (adapter `SimbadResolver`, fake backed by a fixture table). Each gets the contract suite and fake the specification requires of every driven port.

## 4. Stage 2 — Retrieve

Retrieve gathers what the archives already hold about each identified object, bounded by the field and the object list, and registers every row and product as a `DatasetArtifact` before any test may read it. The point of the archive list is the time baseline: Harvard's plates, the digitised Schmidt surveys and Gaia's epochs together cover more than a century for bright stars.

| Archive | Holds | Reach back | Access | SDP adapter |
|---|---|---|---|---|
| [ESA Gaia](https://gea.esac.esa.int/archive/) | Astrometry, photometry; epoch astrometry and photometry arrive with DR4 on 2 Dec 2026 | 2014 onward | `astroquery.gaia` (TAP) | `GaiaTapArchiveAdapter` |
| [CDS VizieR and SIMBAD](https://vizier.cds.unistra.fr/) | Published catalogues; object identifications, types and bibcodes | Literature since the 19th century | `astroquery.vizier`, `astroquery.simbad` | `VizierArchiveAdapter`, `SimbadResolver` |
| [MAST observations](https://astroquery.readthedocs.io/en/stable/mast/mast_obsquery.html) | HST (1990–), JWST (2022–), TESS (2018–), Kepler and K2, GALEX, IUE (1978–96), the Digitized Sky Survey plates, Pan-STARRS, the Hubble Source Catalog | 1950s plates onward; space missions from 1978 | `astroquery.mast` `Observations.query_region`, `query_criteria`, `get_product_list`, `download_products`; cloud URIs through `get_cloud_uris`; cutouts through `astrocut` | `MastObservationGateway`, `MastArchiveAdapter` |
| [IRSA](https://irsa.ipac.caltech.edu/) | 2MASS, WISE and NEOWISE, ZTF light curves | 1997 onward | `astroquery.ipac.irsa` | `IrsaArchiveAdapter` |
| [DASCH](https://dasch.cfa.harvard.edu/data-access/) | 429,274 scanned Harvard plates: B-band light curves, source catalogues, plate mosaics and cutouts; DR7 released 29 Dec 2024, all access restrictions lifted | about 1880 to 1990 | [`daschlab`](https://daschlab.readthedocs.io/)[ 1.0](https://daschlab.readthedocs.io/) over the Starglass web APIs | `DaschPlateGateway` |
| Historical record | Papers, tables, scanned reductions for the Einstein examination | 1919 onward | DOI, document registry | `HistoricalSourceAdapter` |

**The ****`ObservationArchiveGateway`**** port.** Catalogue rows already come through `ArchiveGateway`; observations need a second port because the unit is a product, not a row:

```python
class ObservationArchiveGateway(Protocol):
    def search(self, target: Field | ObjectKey, criteria: ObservationCriteria) -> ObservationIndex: ...
        # metadata only: mission, instrument, epoch, product type, size, licence, cloud URI if any
    def fetch(self, product: ProductRef) -> DatasetArtifact: ...
        # bytes from MAST, a cloud URI or DASCH; checksum of what was actually read
    def cutout(self, field: Field, size: Quantity) -> DatasetArtifact: ...
        # astrocut for MAST, the DR7 cutout endpoint for DASCH
```

**Rules carried from the data-source layer.** Idempotency keys on every query; checkpointed, resumable fetches; licence and citation from the archive descriptor or curation is blocked; the rendered query stored on the `SourceRef`; cassette-recorded contract tests so CI never calls a live archive; one weekly live smoke per adapter for schema drift. Retrieve adds one rule of its own: an observation index is cheap and is always fetched for every object, while products are fetched only when a test in the profile declares it needs them.

**Per-object evidence bundle.** Retrieve ends with an `EvidenceBundle` per object: the canonical catalogue rows by source, the observation index, the artifact ids of any products fetched, and the archive releases consulted. The Test stage reads bundles, never archives.

## 5. Stage 3 — Test

A test is a registered `TaskKind`: a CPU reference implementation, a `NumericalPolicy`, declared inputs from the evidence bundle, named outputs, and a null model; a profile selects tests by name and version, and nothing outside the registry can run. The registry is the same mechanism the science modules use, so a test written for Investigate is reusable in a predeclared experiment without change.

| Test | Question it answers | Inputs | Null model | Implementation |
|---|---|---|---|---|
| Astrometric coherence | Is there a co-moving group here? | Gaia five-parameter astrometry with its correlation matrix | Shuffled proper motions and parallaxes within magnitude bins | `ClusterSearchAlgorithm` adapters (scikit-learn, hdbscan; cuML on the DGX Spark) |
| Parallax consistency | Do members share a distance within their errors? | Parallax and error | Field distribution | NumPy and SciPy, pure function |
| Colour-magnitude coherence | Do members form a sequence tighter than the field? | G, BP−RP | Field comparison sample | `astropy.stats`, pure function |
| Long-baseline motion | Does the 1880–2020 position history agree with the Gaia proper motion? | DASCH and DSS positions, Gaia astrometry | Gaia prediction with propagated errors | Astropy coordinates, pure function |
| Variability | Is the object variable over the available baseline? | DASCH, ZTF and TESS light curves | Constant source with the series' own noise | `astropy.timeseries` (Lomb–Scargle), `lightkurve` for Kepler and TESS products |
| Photometric corroboration | Do 2MASS and Pan-STARRS colours agree with the Gaia-based classification? | Multi-band photometry | Field colour distribution | Pure function |
| Null field and injection | What is this profile's false-positive rate on this field? | Shuffled field, synthetic clusters or variables | — | Stellar module generators |

**Selection and recording.** The profile is YAML, versioned and hashed; the hash and every test's name, version and `NumericalPolicy` go into the investigation-set manifest. Each result is a `TestResult` with the statistic, its uncertainty, a p-value or posterior summary, the effect size and the seed; interpretation is left to ranking and triage, never baked into the test. All tests run through `ComputeExecutor`, CPU by default, with the GPU adapter admitted only after its agreement test in the same release.

**Tests do not touch archives.** They read evidence bundles only, so a test is reproducible from the registered artifacts alone and the Retrieve stage can be replayed from cassettes in CI.

## 6. Stage 4 — Candidate investigations

Every candidate that passes the profile's ranking threshold becomes an `Investigation`, an aggregate of its own with a dossier, a state and a provenance chain back to the field, the profile and the archive releases. A candidate is either one object or one structure with members, and the two are not conflated: a co-moving group is one investigation whose members are listed, not ten.

**The ****`Investigation`**** aggregate**

| Field | Content |
|---|---|
| `investigation_id`, `set_id`, `field_id` | Identity and the run it came from |
| `candidate` | `object_key`, or `structure_id` with member keys and membership probabilities |
| `evidence_bundle_ids` | The bundles the tests read |
| `test_results` | Every `TestResult` for this candidate, including the ones that argued against it |
| `rank` | Score and the ranking policy version |
| `state` | Open, Parked, Promoted, Closed, with reason and actor |
| `notes` | Researcher annotations, appended, never edited |
| `links` | Dossier id; `Experiment` id once promoted; SIMBAD id if resolved |

**The dossier** is the same artifact kind the specification uses for experiments, assembled by `AssembleDossier`: field cutouts with the candidate marked, the canonical catalogue rows by source, the observation index with reach-back dates, light curves where fetched, every test result with its null comparison, the set-level false-positive rate, and the provenance graph. It renders in the evidence UI and exports as an RO-Crate draft.

**Triage** happens in the evidence UI or the notebook, and each transition is a domain event: `InvestigationOpened`, `InvestigationParked`, `InvestigationPromoted`, `InvestigationClosed`. Promotion is the one transition with a precondition the kernel checks: a frozen confirmatory predeclaration exists in the ledger and names at least one evidence source the investigation did not use.

**Ranking** reuses the stellar module's `CandidateRanker`: membership, coherence, stability across the profile's hyperparameter grid, and novelty from the resolver and benchmark cross-match. The ranking policy is versioned and recorded like a test; it never silently changes between runs.

## 7. Find, bind, integrate — the ecosystem map

SDP builds the kernel, the dialects, the test registry, the investigation aggregate and the evidence UI, and nothing else: every other capability already exists in [STScI's data analysis tools ecosystem](https://jwst-docs.stsci.edu/jwst-post-pipeline-data-analysis) or the Astropy project and is bound behind a port. The table is the inventory; the binding column is the architectural decision for each row.

| Need | Package | Maintained by | SDP port or place | Binding |
|---|---|---|---|---|
| Catalogue queries | `astroquery` (gaia, vizier, simbad, ipac.irsa, mast.Catalogs) | Astropy project; the MAST module by STScI's archive team | `ArchiveGateway`, `ObjectResolver` | Adapter with cassettes |
| Observation search, download, cloud access | `astroquery.mast` Observations and MastMissions; `astrocut` for cutouts | STScI (MAST) | `ObservationArchiveGateway` | Adapter with cassettes |
| Service discovery (the "find") | `pyvo` registry, TAP, SIA, SSA and cone search | Astropy and the IVOA | `SourceRegistry`: which services cover a field | Adapter |
| Historical plates | [`daschlab`](https://daschlab.readthedocs.io/) over the Starglass APIs | Harvard–Smithsonian CfA | `ObservationArchiveGateway` (DASCH) | Adapter with cassettes |
| Time-domain products | `lightkurve` (Kepler, TESS); `astropy.timeseries` | Community; Astropy | Retrieval through the gateway, analysis as `TaskKind`s | Adapter for retrieval, library for analysis |
| Sky regions, WCS, reprojection | `regions`, `astropy.wcs`, `gwcs`, `reproject` | Astropy; `gwcs` developed at STScI | `Field` value object | Allow-listed domain library |
| Source detection and photometry | `photutils` | Astropy-coordinated, STScI-led | `SourceDetector` | Adapter, so a planted fake exists |
| WCS refinement, HST alignment | `tweakwcs`, `drizzlepac` | STScI | `WcsRefiner` | Adapter |
| Spectra (later) | `specutils`, `specreduce` | Astropy-coordinated, STScI-led | Future `SpectrumGateway` | Adapter |
| Data formats | `astropy.io.fits`, `asdf`, `stdatamodels` | Astropy; `asdf` and `stdatamodels` at STScI | Parsers behind `DatasetArtifact` media types | Adapter, parsed in a subprocess |
| Field viewer | [`jdaviz`](https://github.com/spacetelescope/jdaviz) Imviz, with its Catalogs and Subset plugins | STScI | `FieldViewer` driving adapter: an Imviz tray plugin | Plugin that calls `InvestigateField` |
| Units, constants, statistics, coordinates, time | `astropy.units`, `constants`, `stats`, `coordinates`, `time` | Astropy | Domain | Allow-listed domain library |
| Synthetic photometry, PSFs (later) | `synphot`, `stpsf` | STScI | Future photometric checks | Adapter |
| Calibration pipelines | `jwst`, `romancal`, `crds` | STScI | Out of scope: SDP consumes calibrated products | None |

**Not built by SDP:** viewers, archive clients, FITS or ASDF parsers, WCS, detection and photometry, PSF models, calibration pipelines. **Built by SDP:** the evidence kernel, predeclaration and verdict ledger, source dialects, the test registry, the investigation aggregate, and the dossier and provenance UI.

All packages above are BSD-3 or equivalent, compatible with SDP's GPL-3.0 licence; the licence of each binding is recorded in its adapter's metadata and in the release SBOM.

## 8. Binding rules

An ecosystem package is bound once, behind one port, with a fake, a contract suite and a pinned version; it is never imported from the domain, the application layer or a science module, and the architecture test fails the build if it is. The only exception is a short allow-list of Astropy core modules (`units`, `constants`, `coordinates`, `time`, `stats`, `wcs`, `regions`) that the domain may treat as libraries because they are value semantics, not I/O.

**Every binding has**

1. A port in `sdp.kernel.ports` and an adapter package in `sdp.adapters.<name>`.
1. A fake in `sdp.testing` that passes the same contract suite as the real adapter.
1. Recorded responses for CI: cassettes for network clients, fixture FITS for `photutils` and `tweakwcs`, a fixture plate cutout for DASCH.
1. A pinned version in `uv.lock`, an ARM64 wheel confirmed in the Phase 0 spike, and a `Dialect` if it returns tabular data.
1. A weekly live smoke that calls the real service once and fails on a changed column, parameter or return type.
1. No fork. Fixes go upstream; a needed change that upstream will not take is an adapter-level workaround with an ADR.

**Jdaviz as the field viewer.** The viewer is Imviz, running in JupyterLab on the DGX Spark, with one SDP plugin registered through `tray_registry` and built on `PluginTemplateMixin`, following the [Jdaviz plugin guide](https://jdaviz.readthedocs.io/en/latest/dev/new_plugin.html). The plugin reads the current viewer footprint or a selected subset as the `Field`, offers the profile list, calls `InvestigateField` through the API, and overlays candidates with the same machinery Imviz's own Catalogs plugin uses; its results are reachable from the notebook as `plg.results`, in the pattern of `imviz.get_catalog_source_results()`. The plugin holds no analysis logic and no archive calls: it is a driving adapter, and the fakes make it testable without a running kernel. Jdaviz keeps its own environment, as its installation advice recommends, so its dependency tree never enters the kernel's lock file.

**Where the heavy work stays.** Detection, cross-matching and tests run in the kernel's runner, not in the notebook process; the notebook and the viewer only start runs and show results, which keeps a multi-hour Investigate resumable from checkpoints after the browser closes.

## 9. Changes applied to the architecture specification

The architecture specification stays the single authority; this document adds one use case and the bindings it needs, and the following deltas were applied there so the two never disagree.

| Section | Change |
|---|---|
| §1 Preface | A vision paragraph up front: open a field, select Investigate, with a link to this document |
| §3 External systems | Rows added for SIMBAD, MAST observations (HST, JWST, TESS, Kepler, GALEX, IUE, DSS), IRSA time-domain products and DASCH |
| §5 Domain model | `Field` value object; `Investigation` aggregate with its four states; events `InvestigationOpened`, `InvestigationParked`, `InvestigationPromoted`, `InvestigationClosed`; the invariant that promotion needs a frozen confirmatory predeclaration |
| §6 Ports | Driving port `InvestigateField`; driven ports `ObservationArchiveGateway`, `SourceDetector`, `ObjectResolver`, `WcsRefiner`, `SourceRegistry`; the Imviz plugin listed as a driving adapter |
| §8 Data-source layer | The gateway split (rows through `ArchiveGateway`, products through `ObservationArchiveGateway`) and DASCH as a registered source |
| §12 Requirements | The INV table below is referenced as a companion set compiled from `requirements/investigate/*.ears` |
| §17 Roadmap | Investigate on one predeclared field becomes the exit criterion of the data-layer phase; the Imviz plugin lands with the stellar slice |

No decision from §19 of the specification changes: the language split, the runner, GraphQL for queries, PROV and RO-Crate, and the DR3 pin all hold, and Investigate runs on them.

## 10. EARS requirements for Investigate (INV)

Thirteen requirements, kept as `requirements/investigate/*.ears` in the same grammar as the rest of the platform and compiled by the harness into the traceability matrix; the verification column names the test tier from the specification's §11.

| ID | Pattern | Requirement | Verification |
|---|---|---|---|
| INV-001 | Ubiquitous | The system shall run Investigate only over a declared `Field` and a versioned, hashed test profile. | T1, T4 |
| INV-002 | Event | When Investigate starts, the system shall record the field, the profile hash, the archive releases consulted, the code revision and the seed in an investigation-set manifest. | T4 |
| INV-003 | Ubiquitous | Every identified object shall carry an identification record naming the method, the image or catalogue it came from, and the matched Gaia source key or null. | T1 |
| INV-004 | Event | When objects are identified, the system shall retrieve catalogue rows and observation indices only through `ArchiveGateway` and `ObservationArchiveGateway`. | T0, T3 |
| INV-005 | Ubiquitous | Every retrieved row set and product shall be registered as a `DatasetArtifact` with source release, licence and citation before any test reads it. | T3, T4 |
| INV-006 | Ubiquitous | Every test in a profile shall be a registered `TaskKind` with a CPU reference implementation and a declared null model. | T1 |
| INV-007 | Event | When tests complete, the system shall open one `Investigation` per candidate above the ranking threshold and record the number of objects and tests examined for the set. | T4 |
| INV-008 | Ubiquitous | Investigation results shall be labelled `EXPLORATORY` and shall not carry a `Verdict`. | T1 |
| INV-009 | Unwanted | If an investigation is promoted without a frozen confirmatory predeclaration that names evidence the investigation did not use, then the kernel shall refuse the promotion and record the refusal. | T1, T4 |
| INV-010 | Ubiquitous | Parked and closed investigations shall be retained with their dossiers and reasons. | T4 |
| INV-011 | Unwanted | If a bound ecosystem package is imported outside `sdp.adapters` or the domain-library allow-list, then the architecture test shall fail the build. | T0 |
| INV-012 | Ubiquitous | The field viewer plugin shall contain no analysis logic or archive calls; it shall call `InvestigateField` and render its results. | T0, T4 with fakes |
| INV-013 | Optional | Where a cloud URI is available for a MAST product, the system shall record the URI and the checksum of the bytes actually read. | T3 |

## 11. Risks specific to ecosystem integration

Binding to a living ecosystem trades building effort for drift and dependency risk; each row names the control already in place.

| Risk | Impact | Mitigation |
|---|---|---|
| API churn in `astroquery` or `jdaviz` | Adapters or the plugin stop working between releases | Pinned versions, cassettes, weekly live smoke, no fork; the plugin follows the published plugin guide only |
| Jdaviz's dependency weight on ARM64 | Viewer unavailable or conflicting with kernel packages | Separate Jdaviz environment; wheels confirmed in the Phase 0 spike; the CLI route never depends on the viewer |
| Archive rate limits and logins | Retrieve stalls on large fields | Index first, products on demand; descriptor-declared limits honoured; MAST tokens only for proprietary data and only in Podman secrets |
| DASCH volume | Hundreds of terabytes upstream | Cutouts and light curves only, by object; never bulk; sizes recorded per fetch |
| Exploratory results leaking into claims | Overstated discoveries | INV-008 and INV-009; set-level multiplicity recorded; empirical null rate per field |
| Gaia DR4 landing mid-phase (2 Dec 2026) | Dialect and epoch semantics change | Release pinning; DR4 as a new dialect and a cross-release investigation, not a silent switch |
| Unmatched detections treated as discoveries | Artefacts become candidates | Flagged at Identify; image artefact tests in the profile; resolver and benchmark cross-match before any novelty label |
| Licence drift in a dependency | Redistribution problem at release | Licence recorded per adapter; SBOM check at release |

## 12. Sources

- [STScI JWST post-pipeline data analysis tools](https://jwst-docs.stsci.edu/jwst-post-pipeline-data-analysis) — the ecosystem list: Astropy, glue, ginga, photutils, specutils, specreduce, jdaviz, gwcs
- [Jdaviz repository](https://github.com/spacetelescope/jdaviz) and [Creating a new plugin](https://jdaviz.readthedocs.io/en/latest/dev/new_plugin.html) — `tray_registry`, `PluginTemplateMixin`, the Catalogs plugin as a reference
- [Imviz plugins](https://github.com/spacetelescope/jdaviz/blob/v3.4.0/docs/imviz/plugins.rst) — catalog search results exposed through `get_catalog_source_results()`
- [astroquery.mast Observations](https://astroquery.readthedocs.io/en/stable/api/astroquery.mast.ObservationsClass.html) and [Observation queries](https://astroquery.readthedocs.io/en/stable/mast/mast_obsquery.html) — `query_region`, `query_criteria`, `get_product_list`, `download_products`, cloud URIs
- [astroquery.mast MastMissions](https://astroquery.readthedocs.io/en/latest/api/astroquery.mast.MastMissionsClass.html) — mission-specific product search
- [DASCH data access](https://dasch.cfa.harvard.edu/data-access/), [DR7](https://dasch.cfa.harvard.edu/dr7), [data products](https://dasch.cfa.harvard.edu/dr7/data-products), [web APIs](https://dasch.cfa.harvard.edu/dr7/web-apis), [`daschlab`](https://daschlab.readthedocs.io/) and [daschlab on PyPI](https://pypi.org/project/daschlab/)
- [STPSF](https://stpsf.readthedocs.io/en/stable/_modules/stpsf.html) — the current name of the JWST PSF package
- [ESA Gaia news](https://www.cosmos.esa.int/web/gaia/news) — DR4 planned for 2 December 2026 with epoch data
- SDP — Architecture Specification v0.3.0 (companion document, §19 decisions accepted 28 October 2026)


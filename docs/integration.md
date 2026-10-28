# Integration Architecture v0.4.0

The companion document "SDP — Integration Architecture v0.4.0" extends the architecture specification v0.3.0 and the Investigate companion (`docs/investigate.md`). It turns the archive adapters into a capability-driven integration layer. ADR-017 to ADR-022 were accepted by Leigh on 28 October 2026, with Stage A in Phase 3.

The goal it serves is Investigate: open a field, find what the archives hold about it, and assemble evidence. v0.4.0 adds five things to that path.

1. **Sources say what they can do.** A `SourceCapabilityDescriptor` is read from RegTAP, VOSI and ObsCore, or hand-written for a native service. `match` explains, field by field, why each source can or cannot serve an `EvidenceRequirement` (§2, ADR-017).
2. **Products keep their meaning.** A `ScientificDataProduct` is the raw bytes plus an ObsCore 1.1-shaped envelope filled by a per-mission dialect. A `SemanticValidationReport` must pass before anything is curated, and previews are never science (§3, ADR-018).
3. **Identities are probabilities.** `AssociationService` turns cross-match candidates into competing `ObjectHypothesis`es with posteriors. It keeps every candidate, and only a ledger entry accepts an identity (§4, ADR-019).
4. **Acquisition is planned.** `PlanInvestigation`, `ApprovePlan` and `ExecutePlan` handle acquisition. The `AcquisitionPlan` is content-addressed, and above the budget threshold its approval is a signed ledger entry. Nothing is fetched that the plan does not name (§5, ADR-020).
5. **Adapters are admitted per product type.** An adapter must pass contract (T3), operational (T5), semantic (T5s) and scientific (T8) conformance. The resulting `AdapterCompatibilityRecord` goes in every manifest, and the harness checks it before a verdict (§6, ADR-021).

JWST through a mission-aware MAST adapter is the first acceptance case (§7, ADR-022).

## Stages and where they land

| Stage | Content | Status |
|---|---|---|
| Kernel | Value objects, ports and fakes, five schemas and manifest 1.2.0, INT-001 to INT-016, ADR-017 to ADR-022 | This change |
| D Association | `BayesFactorAssociation` (CPU reference) and `LikelihoodRatioAssociation` in `sdp.adapters.association`; planted-truth fixture; agreement test (INT-009, 010, 012) | Next |
| B Product model | `ProductNormalizer` (extends Investigate's `ProductDialect`), `ProductValidator`, the reference-record registry and T5s tier, compatibility records in the manifest, harness `admit` and `claims` | Next |
| C Capability registry | `PostgresCapabilityRegistry` (migration 0004), VO discovery over the archive `Transport`, replacing the Investigate `SourceRegistry` | After Investigate's VO registry adapter |
| A JWST ingestion | JWST dialect on Investigate's `MastObservationGateway` (ObsTAP), one public level-2 or level-3 product with full provenance | After Investigate's MAST adapter |
| E Planning | The three driving ports wrapping Investigate's Retrieve stage; `EvidenceRequirement`s declared by every registered test; harness `rederive-plan` | After Investigate's adapters and stellar wiring |
| Demonstration | The §7 JWST field, discover to dossier, reproduced from the plan artifact | Needs one live recording run |

## Where the code is

| Concept | Module |
|---|---|
| Descriptors, matching, product envelope, plans, budgets, approvals, compatibility records | `sdp.kernel.domain.integration` |
| `Detection`, `CatalogueSource`, `ObjectHypothesis`, `Association`, `AssociationGraph`, review states | `sdp.kernel.domain.association` |
| `CapabilityRegistry`, `CapabilityDiscoveryService`, `AssociationService`, `ProductNormalizer`, `ProductValidator`, `PlanInvestigation`, `ApprovePlan`, `ExecutePlan` | `sdp.kernel.ports.integration` |
| Fakes `InMemoryCapabilityRegistry`, `FixtureDiscovery`, `PlantedAssociations` | `sdp.testing.fakes.integration` |
| Contract suites | `sdp.testing.contracts.integration` |
| Synthetic fixtures (descriptors, envelopes, plans, a planted association field) | `sdp.testing.integration` |
| Schemas | `contracts/schemas/{capability-descriptor,scientific-data-product,association-graph,acquisition-plan,adapter-compatibility}.schema.json` |
| Requirements | `requirements/integration/integration.ears` |

Deviations D30 to D33 in `docs/spec-deviations.md` record where the code differs from the document's wording.

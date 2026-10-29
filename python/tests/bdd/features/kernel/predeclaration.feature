Feature: Predeclaration and hypotheses
  A researcher states what would count as success before looking at any data. The kernel freezes
  that predeclaration into the signed ledger, and an experiment may only run against a frozen one.
  These scenarios drive the kernel through its driving ports, wired to the in-memory fakes of
  sdp.testing; nothing the kernel owns is replaced.

  Rule: KRN-001: The kernel shall register versioned hypotheses and record every edit as a new version that retains every prior version.

    Scenario: Editing a hypothesis adds a version and keeps the first
      Given the hypothesis "H-1" is registered as "light bends"
      When the researcher edits "H-1" to "light bends by 1.75 arcsec at the limb"
      Then "H-1" has versions 1 and 2 in order
      And the current version of "H-1" is 2

  Rule: KRN-002: When a predeclaration is frozen, the kernel shall compute its hash over canonical JSON and append a signed ledger entry before returning.

    Scenario: Freezing signs the canonical hash into the ledger
      Given a predeclaration for the synthetic linear protocol
      When "leigh" freezes the predeclaration
      Then the frozen hash is the SHA-256 of the predeclaration's canonical JSON
      And the ledger holds one predeclaration entry for that hash signed by "leigh"
      And the ledger verifies

    Scenario: Freezing the same predeclaration twice records it once
      Given a predeclaration for the synthetic linear protocol
      When "leigh" freezes the predeclaration
      And "leigh" freezes the predeclaration
      Then the ledger holds one predeclaration entry for that hash signed by "leigh"

  Rule: KRN-003: If an experiment references a predeclaration hash absent from the ledger, then the kernel shall refuse to start it and record the refusal.

    Scenario: An experiment against an unfrozen predeclaration is refused
      Given a predeclaration for the synthetic linear protocol
      And an experiment defined against the predeclaration without freezing it
      When the researcher runs the experiment
      Then the run is refused because the predeclaration is not frozen
      And the experiment is stored as refused, naming the missing hash
      And no compute task was executed

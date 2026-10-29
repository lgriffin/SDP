Feature: Investigate a field, then triage its candidates
  A scientist points Investigate at a field with a frozen test profile. Every candidate above the
  ranking threshold opens an exploratory investigation, which can be parked, closed or promoted, but
  never verified. These scenarios run the synthetic field (sixty catalogue objects, five planted
  co-moving stars) through the kernel's driving ports, wired to the in-memory fakes of sdp.testing.

  Background:
    Given the synthetic field has been investigated

  Rule: INV-007: When tests complete, the system shall open one Investigation per candidate above the ranking threshold and record the number of objects and tests examined for the set.

    Scenario: Each planted star opens one investigation and the set records what was examined
      Then one open investigation exists for each planted star
      And the set records 60 objects examined, 1 test applied and 5 candidates opened

  Rule: INV-008: The kernel shall label every investigation result EXPLORATORY and give it no Verdict.

    Scenario: Investigations, their test results and their dossiers are exploratory
      Then every investigation and every test result is labelled "EXPLORATORY"
      And every investigation dossier is labelled "EXPLORATORY"
      And no verdict was issued

  Rule: INV-009: If an investigation is promoted without a frozen confirmatory predeclaration that names evidence the investigation did not use, then the kernel shall refuse the promotion and record the refusal.

    Scenario: Promotion without a predeclaration is refused and recorded
      When "leigh" promotes the first investigation without a predeclaration
      Then the promotion is refused
      And 1 promotion refusal is recorded against the first investigation
      And the first investigation's state is "open"

    Scenario: Promotion on evidence the investigation already used is refused
      Given a frozen confirmatory predeclaration names the release "gaia:DR3"
      When "leigh" promotes the first investigation under that predeclaration
      Then the promotion is refused because "INV-009"
      And 1 promotion refusal is recorded against the first investigation

    Scenario: Promotion on fresh evidence is accepted
      Given a frozen confirmatory predeclaration names the release "gaia:DR4"
      When "leigh" promotes the first investigation under that predeclaration
      Then the first investigation's state is "promoted"

  Rule: INV-010: The kernel shall retain parked and closed investigations with their dossiers and reasons.

    Scenario: Parked and closed investigations keep their dossiers and reasons
      When "leigh" parks the first investigation because "revisit when Gaia DR4 lands"
      And "leigh" closes the second investigation because "artefact at triage"
      Then the set still holds 5 investigations
      And the first investigation is "parked" because "revisit when Gaia DR4 lands"
      And the second investigation is "closed" because "artefact at triage"
      And the first and second investigations keep their dossiers

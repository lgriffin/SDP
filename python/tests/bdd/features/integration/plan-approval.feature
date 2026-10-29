Feature: Acquisition plan approval and execution
  An investigation's acquisitions are planned before anything is fetched. A plan below its budget's
  approval threshold is approved automatically; above it, only a signed ledger approval of that plan
  by the approving actor will do. The executor fetches only what an approved plan names. The archives
  are the fakes of sdp.testing.

  Rule: INT-014: If a plan exceeds the profile's AcquisitionBudget threshold, then the system shall require a signed ledger approval before execution.

    Scenario: A plan below the threshold is approved automatically
      Given an investigation planned with an approval threshold of 50000000 bytes
      When "leigh" approves the plan
      Then the plan is "auto_approved"
      And the event "PlanApproved" records the decision "auto_approved"

    Scenario: A plan above the threshold is refused without a signed approval
      Given an investigation planned with an approval threshold of 1000 bytes
      When "leigh" approves the plan
      Then the approval is refused because "a signed ledger approval is required"
      And nothing has been acquired

    Scenario: A ledger approval of another plan approves nothing
      Given an investigation planned with an approval threshold of 1000 bytes
      And "leigh" has signed a ledger approval of another plan
      When "leigh" approves the plan with that ledger entry
      Then the approval is refused because "does not approve plan"

    Scenario: A signed ledger approval of this plan by the approving actor approves it
      Given an investigation planned with an approval threshold of 1000 bytes
      And "leigh" has signed a ledger approval of the plan
      When "leigh" approves the plan
      Then the plan is "approved" under that ledger entry

    Scenario: A plan above the threshold is not executed until it is approved
      Given an investigation planned with an approval threshold of 1000 bytes
      When "leigh" approves the plan
      And the plan is executed
      Then the execution is refused because "not approved"
      And nothing has been acquired

    Scenario: A plan above the threshold runs once a signed approval approves it
      Given an investigation planned with an approval threshold of 1000 bytes
      And "leigh" has signed a ledger approval of the plan
      And "leigh" approves the plan
      When the plan is executed
      Then every plan item was acquired

  Rule: INT-015: If an acquisition is requested that no approved plan item names, then the executor shall refuse it and record the refusal.

    Scenario: An unapproved plan acquires nothing
      Given an investigation planned with an approval threshold of 50000000 bytes
      When the plan is executed
      Then the execution is refused because "not approved"
      And nothing has been acquired

    Scenario: A product that appeared after planning is refused and the refusal recorded
      Given an investigation planned with an approval threshold of 50000000 bytes
      And "leigh" approves the plan
      And the archive lists a light curve "lc-late" that the plan does not name
      When the plan is executed
      Then "lc-late" was not fetched
      And the event "PlanExecuted" records the refusal of "lc-late" at "fake-plates"
      And every plan item was acquired

Feature: Experiment lifecycle
  An experiment runs its compute plan task by task. Each finished task leaves a checkpoint, and a
  failing task leaves enough behind to diagnose it. The compute executor is the in-memory fake.

  Rule: KRN-005: When a compute task completes, the kernel shall write a checkpoint and emit CheckpointWritten.

    Scenario: Every task of a completed run leaves a checkpoint
      Given a frozen predeclaration for the synthetic linear protocol
      When the researcher defines and runs an experiment
      Then the experiment is completed
      And the tasks "fit" and "residuals" have stored checkpoints
      And the event "CheckpointWritten" was emitted 2 times

  Rule: KRN-007: If a compute task fails, then the kernel shall preserve the manifest, diagnostics and partial outputs and mark the experiment Failed.

    Scenario: A failing task keeps the manifest, its diagnostics and the finished task's output
      Given a frozen predeclaration for the synthetic linear protocol
      When the researcher defines and runs an experiment in which the task "residuals" fails
      Then the experiment is failed at the task "residuals"
      And the failure carries a traceback
      And the tasks "fit" have stored checkpoints
      And the manifest records the failed state and the failing task

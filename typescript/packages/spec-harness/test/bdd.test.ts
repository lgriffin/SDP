import { describe, expect, it } from "vitest";

import { FeatureDirectory, EarsDirectory } from "../src/adapters/fs.ts";
import { checkFeatures } from "../src/bdd.ts";
import { compileEars } from "../src/ears.ts";
import { compileRequirements } from "../src/harness.ts";
import { repo } from "./helpers.ts";

const { requirements } = compileEars([
  {
    path: "requirements/x.ears",
    text: `capability: X
owner: kernel
requirements:
  - id: KRN-900
    pattern: ubiquitous
    text: "The kernel shall work."
    verification: test
    status: approved
  - id: KRN-901
    pattern: ubiquitous
    text: "The kernel shall rest."
    verification: test
    status: retired
`,
  },
]);
const check = (text: string) => checkFeatures([{ path: "f.feature", text }], requirements);
const messages = (text: string): string[] => check(text).problems.map((p) => p.message);

describe("[TST-010] Gherkin Rules bound to requirements", () => {
  it("[TST-010] binds every feature in the repository to its requirement text", async () => {
    const compiled = await compileRequirements(new EarsDirectory(`${repo}/requirements`));
    const report = checkFeatures(await new FeatureDirectory(`${repo}/python/tests/bdd/features`).featureFiles(), compiled.requirements);
    expect(report.problems).toEqual([]);
    expect(report.rules.length).toBeGreaterThanOrEqual(5);
  });

  it("[TST-010] accepts a Rule titled with the exact requirement text and counts its scenarios", () => {
    const report = check("Feature: F\n  Rule: KRN-900: The kernel shall work.\n    Scenario: one\n    Example: two\n");
    expect(report.problems).toEqual([]);
    expect(report.coverage).toEqual({ "KRN-900": 2 });
  });

  it("[TST-010] rejects a Rule whose text drifts from the .ears file", () => {
    expect(messages("Feature: F\n  Rule: KRN-900: The kernel shall work hard.\n    Scenario: one\n")).toEqual([
      'Rule text for KRN-900 differs from requirements/x.ears: expected "The kernel shall work."',
    ]);
  });

  it("[TST-010] rejects a Rule without an id, with an unknown id, or with a retired one", () => {
    expect(messages("Feature: F\n  Rule: the kernel works\n    Scenario: one\n")).toEqual([
      'Rule "the kernel works" is not titled "<ID>: <requirement text>"',
    ]);
    expect(messages("Feature: F\n  Rule: KRN-999: The kernel shall work.\n    Scenario: one\n")).toEqual([
      "Rule names KRN-999, which no .ears file defines",
    ]);
    expect(messages("Feature: F\n  Rule: KRN-901: The kernel shall rest.\n    Scenario: one\n")).toEqual([
      "Rule names KRN-901, which is retired",
    ]);
  });

  it("[TST-010] rejects a Rule with no scenario and a scenario outside any Rule", () => {
    expect(messages("Feature: F\n  Scenario: loose\n  Rule: KRN-900: The kernel shall work.\n")).toEqual([
      'scenario "loose" is not under a Rule',
      "Rule KRN-900 has no scenario, so it verifies nothing",
    ]);
  });

  it("[TST-010] rejects a file with no Feature and ignores commented-out lines", () => {
    expect(messages("# Rule: KRN-999: nothing\n  Rule: KRN-900: The kernel shall work.\n    Scenario: one\n")).toEqual([
      "no Feature: line",
    ]);
  });
});

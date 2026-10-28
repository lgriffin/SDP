import { describe, expect, it } from "vitest";

import { parseJUnit } from "../src/junit.ts";

const pytest = `<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest" tests="3">
<testcase classname="tests.usecase.test_predeclaration" name="test_freeze_appends_signed_ledger_entry">
  <properties><property name="req" value="KRN-002"/><property name="tier" value="T4"/></properties></testcase>
<testcase classname="tests.unit.test_x" name="test_broken"><properties><property name="req" value="KRN-009"/></properties><failure message="boom"/></testcase>
<testcase classname="tests.unit.test_x" name="test_skipped"><skipped/></testcase>
</testsuite></testsuites>`;

const vitest = `<?xml version="1.0" encoding="UTF-8" ?><testsuites name="vitest tests"><testsuite name="test/contracts.test.ts">
<testcase classname="test/contracts.test.ts" name="[CTR-003] fixtures &gt; [CTR-003] accepts valid predeclaration [gate:SYN-G-01] [tier:T6]"></testcase>
</testsuite></testsuites>`;

describe("JUnit from both toolchains", () => {
  it("reads pytest requirement, tier and outcome properties", () => {
    const results = parseJUnit(pytest, "junit-python.xml");
    expect(results.map((r) => [r.name, r.outcome, r.requirements, r.tiers])).toEqual([
      ["test_freeze_appends_signed_ledger_entry", "passed", ["KRN-002"], ["T4"]],
      ["test_broken", "failed", ["KRN-009"], []],
      ["test_skipped", "skipped", [], []],
    ]);
  });

  it("reads vitest markers from titles", () => {
    const [result] = parseJUnit(vitest, "junit-ts.xml");
    expect(result?.requirements).toEqual(["CTR-003"]);
    expect(result?.gates).toEqual(["SYN-G-01"]);
    expect(result?.tiers).toEqual(["T6"]);
  });
});

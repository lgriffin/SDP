import { describe, expect, it } from "vitest";

import type { GateCheck, LedgerEntry } from "../src/lib/ports.ts";
import { FIXTURES } from "../src/lib/testing/fixtures.ts";
import {
  chainProblems,
  decisiveChecks,
  describeGate,
  formatNumber,
  isSelfApproval,
  orderChecks,
  shortHash,
  verdictTone,
} from "../src/lib/view.ts";

const check = (gateId: string, severity: string, outcome: string): GateCheck => ({
  gateId,
  finding: "f",
  comparator: "lt",
  threshold: 1,
  severity,
  oracleIds: [],
  oraclesVerified: true,
  outcome,
});

describe("verdict explanation", () => {
  const checks = [
    check("G-03", "warning", "fail"),
    check("G-01", "blocking", "pass"),
    check("G-02", "blocking", "fail"),
    check("G-04", "blocking", "missing"),
  ];

  it("orders blocking gates first, failures before passes", () => {
    expect(orderChecks(checks).map((c) => c.gateId)).toEqual(["G-02", "G-04", "G-01", "G-03"]);
  });

  it("names the gates that decided each verdict", () => {
    expect(decisiveChecks(checks, "REJECTED").map((c) => c.gateId)).toEqual(["G-02"]);
    expect(decisiveChecks(checks, "INCONCLUSIVE").map((c) => c.gateId)).toEqual(["G-04"]);
    expect(decisiveChecks(checks, "CANDIDATE").map((c) => c.gateId)).toEqual(["G-03"]);
  });

  it("the fixture rejection is decided by its failing blocking gate", () => {
    const rejected = FIXTURES.experiment["exp-000002"];
    expect(rejected?.verdict?.status).toBe("REJECTED");
    expect(decisiveChecks(rejected?.checks ?? [], "REJECTED").map((c) => c.gateId)).toEqual(["SYN-G-01"]);
  });

  it("tones verdicts", () => {
    expect(verdictTone("VERIFIED")).toBe("good");
    expect(verdictTone("REJECTED")).toBe("bad");
    expect(verdictTone("CANDIDATE")).toBe("warn");
    expect(verdictTone(null)).toBe("neutral");
  });
});

describe("formatting", () => {
  it("formats numbers readably", () => {
    expect(formatNumber(0.05)).toBe("0.05");
    expect(formatNumber(0.00304683)).toBe("0.003047");
    expect(formatNumber(2.19e-4)).toBe("2.190e-4");
    expect(formatNumber(42)).toBe("42");
    expect(formatNumber(true)).toBe("true");
    expect(formatNumber(null)).toBe("—");
  });

  it("describes a gate as a criterion", () => {
    expect(describeGate({ ...check("G", "blocking", "pass"), finding: "rms", comparator: "le", threshold: 0.5, unit: "arcsec" })).toBe(
      "rms ≤ 0.5 arcsec",
    );
  });

  it("shortens hashes but keeps their algorithm", () => {
    expect(shortHash("sha256:" + "a".repeat(64))).toBe("sha256:aaaaaaaaaaaa…");
    expect(shortHash(null)).toBe("—");
    expect(shortHash("short")).toBe("short");
  });
});

describe("ledger view", () => {
  it("finds the fixture chain intact", () => {
    expect(chainProblems(FIXTURES.ledger)).toEqual([]);
  });

  it("reports gaps and missing links", () => {
    const [first, second] = FIXTURES.ledger as [LedgerEntry, LedgerEntry];
    expect(chainProblems([first, { ...second, sequence: 2, previousEntryHash: null }]).map((p) => p.message)).toEqual([
      "sequence 2 at position 1",
      "no previous entry hash",
    ]);
  });

  it("flags self-approval from the entry payload", () => {
    const approval = FIXTURES.ledger[1] as LedgerEntry;
    expect(isSelfApproval(approval)).toBe(false);
    expect(isSelfApproval({ ...approval, payload: { self_approval: true } })).toBe(true);
  });
});

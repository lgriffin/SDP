// The exact records of the guard, the manifest reader, the JUnit reader and the ratchet: each rule at
// its edge, each refusal message and each ordering (TST-002, CTR-004, TST-001, TST-004).
import { describe, expect, it } from "vitest";

import { MANIFEST_READERS, readManifest } from "../src/conform.ts";
import { guard, parseNameStatus, type FileChange } from "../src/guard.ts";
import { parseJUnit } from "../src/junit.ts";
import { ratchet } from "../src/ratchet.ts";

const APP = "python/packages/sdp-kernel/src/sdp/kernel/application";
const PORTS = "python/packages/sdp-kernel/src/sdp/kernel/ports";
const ADAPTERS = "python/packages/sdp-adapters/src/sdp/adapters";
const added = (path: string): FileChange => ({ status: "A", path });
const kinds = (...paths: string[]) => guard(paths.map(added)).triggers.map((t) => `${t.kind} ${t.subject}`);

describe("[TST-002] the behaviour-change guard", () => {
  it("[TST-002] a new use case, port or adapter module triggers it, and nothing else does", () => {
    expect(
      kinds(
        `${APP}/plan.py`,
        `${PORTS}/archive.py`,
        `${ADAPTERS}/gaia.py`,
        `${ADAPTERS}/mast/client.py`,
        "typescript/packages/spec-harness/src/ports.ts",
      ),
    ).toEqual([
      `use-case ${APP}/plan.py`,
      `port ${PORTS}/archive.py`,
      `adapter ${ADAPTERS}/gaia.py`,
      `adapter ${ADAPTERS}/mast/client.py`,
      "port typescript/packages/spec-harness/src/ports.ts",
    ]);
    expect(
      kinds(
        `${APP}/__init__.py`,
        `${APP}/sub/plan.py`,
        `${APP}/plan.pyc`,
        `x/${APP}/plan.py`,
        `${PORTS}/__init__.py`,
        `${PORTS}/archive.py.orig`,
        `vendored/${PORTS}/archive.py`,
        `${ADAPTERS}/__init__.py`,
        `${ADAPTERS}/mast/__init__.py`,
        `old/${ADAPTERS}/gaia.py`,
        `${ADAPTERS}/gaia.pyi`,
        "typescript/packages/spec-harness/src/ports.tsx",
        "x/typescript/packages/spec-harness/src/ports.ts",
      ),
    ).toEqual([]);
  });

  it("[TST-002] an addition, rename or copy triggers it; a modification or deletion does not", () => {
    const statuses: FileChange["status"][] = ["A", "M", "D", "R", "C"];
    const result = guard(statuses.map((status) => ({ status, path: `${APP}/plan.py` })));
    expect(result.triggers).toHaveLength(3);
  });

  it("[TST-002] a new gate triggers it with its predeclaration path; a kept gate does not", () => {
    const result = guard([], [{ path: "p/predeclaration.json", before: ["G-1"], after: ["G-1", "G-2"] }]);
    expect(result).toEqual({ triggers: [{ kind: "gate", subject: "G-2 in p/predeclaration.json" }], earsChanged: [], ok: false });
  });

  it("[TST-002] only an .ears file under requirements/ satisfies it", () => {
    const changes: FileChange[] = [added(`${APP}/plan.py`), { status: "M", path: "requirements/notes.md" }, { status: "M", path: "docs/x.ears" }];
    expect(guard(changes)).toMatchObject({ earsChanged: [], ok: false });
    expect(guard([...changes, { status: "M", path: "requirements/kernel/k.ears" }])).toMatchObject({
      earsChanged: ["requirements/kernel/k.ears"],
      ok: true,
    });
  });

  it("[TST-002] reads git name-status lines, trimmed, keeping a rename's destination", () => {
    expect(parseNameStatus("  A\tnew.py  \n\nR100\told.py\tmoved.py\n  \nD\tgone.py")).toEqual([
      { status: "A", path: "new.py" },
      { status: "R", path: "moved.py" },
      { status: "D", path: "gone.py" },
    ]);
  });
});

describe("[CTR-004] the manifest reader", () => {
  it.each([["text"], [null], [[1]], [7]])("[CTR-004] refuses %j as not a JSON object", (document) => {
    expect(readManifest(document)).toEqual({ ok: false, major: null, errors: [{ path: "", message: "a manifest is a JSON object" }] });
  });

  it("[CTR-004] refuses an unsupported major, naming the majors it reads", () => {
    expect(readManifest({ schema_version: "9.0.0" })).toEqual({
      ok: false,
      major: 9,
      errors: [{ path: "/schema_version", message: "major 9 is not supported; the harness reads majors 1 and 0" }],
    });
  });

  it("[CTR-004] refuses a supported major with no schema", () => {
    expect(readManifest({ schema_version: "0.9.0" })).toEqual({
      ok: false,
      major: 0,
      errors: [{ path: "/schema_version", message: "no schema exists for major 0" }],
    });
    expect([...MANIFEST_READERS.keys()]).toEqual([1]);
  });
});

describe("[TST-001] the JUnit reader", () => {
  it("[TST-001] reads a bare testsuite, falling back to the suite name, and counts bare failures and errors", () => {
    const xml = `<?xml version="1.0"?><testsuite name="suite-a">
      <testcase name="f"><failure/></testcase>
      <testcase name="e"><error/></testcase>
      <testcase classname="cls" name="[KRN-002] [KRN-001] [KRN-002] p [gate:REL-G-02] [tier:T9]">
        <properties><property name="gate" value="REL-G-01"/><property name="tier" value="T1"/></properties>
      </testcase>
    </testsuite>`;
    expect(parseJUnit(xml, "r.xml")).toEqual([
      { id: "suite-a::f", name: "f", classname: "suite-a", outcome: "failed", requirements: [], gates: [], tiers: [], report: "r.xml" },
      { id: "suite-a::e", name: "e", classname: "suite-a", outcome: "failed", requirements: [], gates: [], tiers: [], report: "r.xml" },
      {
        id: "cls::[KRN-002] [KRN-001] [KRN-002] p [gate:REL-G-02] [tier:T9]",
        name: "[KRN-002] [KRN-001] [KRN-002] p [gate:REL-G-02] [tier:T9]",
        classname: "cls",
        outcome: "passed",
        requirements: ["KRN-001", "KRN-002"],
        gates: ["REL-G-01", "REL-G-02"],
        tiers: ["T1", "T9"],
        report: "r.xml",
      },
    ]);
  });

  it("[TST-001] reads suites nested in suites", () => {
    const xml = `<testsuites><testsuite name="outer"><testsuite name="inner"><testcase name="t"/></testsuite></testsuite></testsuites>`;
    expect(parseJUnit(xml, "r.xml").map((r) => r.id)).toEqual(["inner::t"]);
  });
});

describe("[TST-004] the mutation ratchet", () => {
  it("[TST-004] raises marks, keeps the marks of packages not scored, and lists what is missing in order", () => {
    expect(ratchet({ "p/a": 95, "p/c": 70 }, { "p/a": 90, "p/z": 80, "p/b": 85 })).toEqual({
      ok: false,
      violations: [],
      highWater: { "p/a": 95, "p/z": 80, "p/b": 85, "p/c": 70 },
      missing: ["p/b", "p/z"],
      belowGoal: [{ pkg: "p/c", score: 70 }],
    });
  });

  it("[TST-004] lists violations in package order", () => {
    expect(ratchet({ "p/z": 50, "p/a": 50 }, { "p/z": 90, "p/a": 90 }).violations).toEqual([
      { pkg: "p/a", score: 50, highWater: 90 },
      { pkg: "p/z", score: 50, highWater: 90 },
    ]);
  });
});

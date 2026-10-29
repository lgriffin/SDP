// The .ears compiler's exact records: every problem message, every compiled field and default, and the
// rendered §12 tables, so a loosened check or a changed table fails a test (TST-001, TST-008).
import { describe, expect, it } from "vitest";

import { compileEars, REQUIREMENT_ID, renderRequirementsMarkdown, styleProblems, VAGUE, type Requirement } from "../src/ears.ts";

const HEAD = "capability: X\nowner: kernel\nrequirements:\n";
const ok = 'pattern: ubiquitous\n    text: "The kernel shall work."\n    verification: test\n    status: approved';

describe("[TST-008] requirement style", () => {
  it("[TST-008] text with no shall states only that it has none", () => {
    expect(styleProblems("The kernel works.")).toEqual(["text says shall 0 times; a requirement states exactly one obligation"]);
  });

  it("[TST-008] the named-system subject must end at shall and fit in six words", () => {
    expect(styleProblems("The kernel shall work.")).toEqual([]);
    expect(styleProblems("When asked, then the spec harness shall work.")).toEqual([]);
    expect(styleProblems("The kernel and the harness and the rest shall work.")).toEqual([
      'text does not name the system that shall act ("the <system> shall ...")',
    ]);
  });

  it("[TST-008] flags every vague word, with or without its -ly", () => {
    for (const word of ["adequate", "adequately", "sufficient", "sufficiently", "efficient", "efficiently", "appropriate", "appropriately"]) {
      expect(VAGUE.exec(`The kernel shall work ${word}.`)?.[0]).toBe(word);
    }
  });

  it("[TST-001] requirement ids are exactly three capitals, a hyphen and three digits", () => {
    expect(["KRN-001", "xKRN-001", "KRN-0012", "KR-001"].map((id) => REQUIREMENT_ID.test(id))).toEqual([true, false, false, false]);
  });
});

describe("[TST-001] compiled requirements", () => {
  it("[TST-001] compiles every field, with lists from scalars and defaults for what is absent", () => {
    const text =
      "capability: Kernel\nowner: kernel\nrequirements:\n" +
      `  - id: KRN-002\n    ${ok}\n    rationale: because\n    tiers: T1\n    gates: [G-1, 2]\n    traces: ~\n` +
      `  - id: KRN-001\n    ${ok.replace("approved", "retired")}\n`;
    const { requirements, problems } = compileEars([{ path: "requirements/k.ears", text }]);
    expect(problems).toEqual([]);
    const base: Omit<Requirement, "id" | "status" | "rationale" | "tiers" | "gates"> = {
      pattern: "ubiquitous",
      text: "The kernel shall work.",
      verification: "test",
      traces: [],
      capability: "Kernel",
      owner: "kernel",
      source: "requirements/k.ears",
    };
    expect(requirements).toEqual([
      { ...base, id: "KRN-001", status: "retired", rationale: "", tiers: [], gates: [] },
      { ...base, id: "KRN-002", status: "approved", rationale: "because", tiers: ["T1"], gates: ["G-1", "2"] },
    ]);
  });

  it("[TST-001] reads files in path order, so a duplicate names the earlier file", () => {
    const one = { path: "requirements/b.ears", text: `${HEAD}  - id: KRN-001\n    ${ok}\n` };
    const two = { path: "requirements/a.ears", text: `${HEAD}  - id: KRN-001\n    ${ok}\n` };
    expect(compileEars([one, two]).problems).toEqual([
      { source: "requirements/b.ears", id: "KRN-001", message: "duplicate id KRN-001 (also in requirements/a.ears)" },
    ]);
  });

  it("[TST-001] reports YAML errors, empty files and missing keys, then carries on", () => {
    const { problems } = compileEars([
      { path: "a.ears", text: "capability: [unclosed" },
      { path: "b.ears", text: "" },
      { path: "c.ears", text: "just a string" },
      { path: "d.ears", text: "owner: kernel\n" },
    ]);
    expect(problems.map((p) => [p.source, p.message.startsWith("not YAML: ") ? "not YAML" : p.message])).toEqual([
      ["a.ears", "not YAML"],
      ["b.ears", "empty .ears file"],
      ["c.ears", "empty .ears file"],
      ["d.ears", "missing capability"],
      ["d.ears", "missing requirements"],
    ]);
  });

  it("[TST-001] names an entry with no id ?, and every unknown pattern, status and verification", () => {
    const { requirements, problems } = compileEars([{ path: "x.ears", text: `capability: X\nrequirements:\n  - text: "The kernel shall work."\n` }]);
    expect(problems).toEqual([
      { source: "x.ears", message: "missing owner" },
      { source: "x.ears", id: "?", message: "id ? does not look like ABC-123" },
      { source: "x.ears", id: "?", message: "unknown pattern " },
      { source: "x.ears", id: "?", message: "unknown status " },
      { source: "x.ears", id: "?", message: "unknown verification " },
    ]);
    expect(requirements[0]).toMatchObject({ id: "?", owner: "", capability: "X", rationale: "", tiers: [], gates: [], traces: [] });
  });

  it("[TST-001] a file with no capability compiles it as empty", () => {
    const { requirements } = compileEars([{ path: "x.ears", text: `owner: kernel\nrequirements:\n  - id: KRN-001\n    ${ok}\n` }]);
    expect(requirements[0]?.capability).toBe("");
  });

  it("[TST-001] accepts every known status and verification", () => {
    const entries = ["draft", "approved", "retired"].flatMap((status, i) =>
      ["test", "analysis", "inspection", "demonstration"].map(
        (verification, j) =>
          `  - id: KRN-${i}${j}0\n    pattern: ubiquitous\n    text: "The kernel shall work."\n    verification: ${verification}\n    status: ${status}\n`,
      ),
    );
    expect(compileEars([{ path: "x.ears", text: HEAD + entries.join("") }]).problems).toEqual([]);
  });
});

describe("[TST-001] the requirements tables", () => {
  it("[TST-001] groups by capability in order, escapes cells and marks missing tiers", () => {
    const r = (id: string, capability: string, overrides: Partial<Requirement> = {}): Requirement => ({
      id,
      pattern: "ubiquitous",
      text: "The kernel shall work.",
      rationale: "",
      verification: "test",
      tiers: ["T1", "T2"],
      gates: [],
      status: "approved",
      traces: [],
      capability,
      owner: "kernel",
      source: "x.ears",
      ...overrides,
    });
    const md = renderRequirementsMarkdown([
      r("ZZZ-001", "Zeta", { text: "The a|b shall\nwork.", tiers: [] }),
      r("AAA-001", "Alpha", { pattern: "event", verification: "analysis", status: "draft" }),
      r("ZZZ-002", "Zeta"),
    ]);
    expect(md).toBe(
      [
        "# Requirements",
        "",
        "Compiled from `requirements/**/*.ears` by `spec-harness ears`. Do not edit.",
        "",
        "## Alpha",
        "",
        "| ID | Pattern | Requirement | Verification | Tiers | Status |",
        "|---|---|---|---|---|---|",
        "| AAA-001 | event | The kernel shall work. | analysis | T1, T2 | draft |",
        "",
        "## Zeta",
        "",
        "| ID | Pattern | Requirement | Verification | Tiers | Status |",
        "|---|---|---|---|---|---|",
        "| ZZZ-001 | ubiquitous | The a\\|b shall work. | test | — | approved |",
        "| ZZZ-002 | ubiquitous | The kernel shall work. | test | T1, T2 | approved |",
        "",
      ].join("\n"),
    );
  });
});

import { readdirSync, readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { EarsDirectory } from "../src/adapters/fs.ts";
import { compileEars, renderRequirementsMarkdown } from "../src/ears.ts";
import { compileRequirements } from "../src/harness.ts";
import { repo } from "./helpers.ts";

/** Every `- id:` entry under requirements/, counted independently of the compiler. */
const declaredIds = (dir: string): number =>
  readdirSync(dir, { recursive: true, encoding: "utf8" })
    .filter((f) => f.endsWith(".ears"))
    .reduce((n, f) => n + (readFileSync(`${dir}/${f}`, "utf8").match(/^\s*- id:/gm)?.length ?? 0), 0);

const file = (body: string): { path: string; text: string } => ({
  path: "requirements/x.ears",
  text: `capability: X\nowner: kernel\nrequirements:\n${body}`,
});
const req = (fields: Record<string, string>): string =>
  "  - " +
  Object.entries({ id: "KRN-900", pattern: "ubiquitous", text: "The kernel shall work.", verification: "test", status: "draft", tiers: "[T1]", ...fields })
    .map(([k, v]) => `${k}: ${v.startsWith("[") ? v : JSON.stringify(v)}`)
    .join("\n    ") +
  "\n";

describe("[TST-001] .ears compiler", () => {
  it("[TST-001] compiles every requirement in the repository without problems", async () => {
    const compiled = await compileRequirements(new EarsDirectory(`${repo}/requirements`));
    expect(compiled.problems).toEqual([]);
    expect(compiled.requirements).toHaveLength(declaredIds(`${repo}/requirements`));
    expect(compiled.requirements.length).toBeGreaterThanOrEqual(62);
    expect(renderRequirementsMarkdown(compiled.requirements)).toContain("| KRN-002 | event |");
  });

  it("rejects text that does not follow its EARS pattern", () => {
    const { problems } = compileEars([file(req({ pattern: "event", text: "The kernel shall work." }))]);
    expect(problems.map((p) => p.message)).toContain("text does not follow the event EARS template");
  });

  it("rejects duplicate ids, unknown statuses and malformed ids", () => {
    const { problems } = compileEars([
      file(req({}) + req({ status: "maybe" }) + req({ id: "kernel-1" })),
    ]);
    const messages = problems.map((p) => p.message).join("\n");
    expect(messages).toContain("duplicate id KRN-900");
    expect(messages).toContain("unknown status maybe");
    expect(messages).toContain("does not look like ABC-123");
  });
});

describe("[TST-008] strict EARS form", () => {
  const messages = (text: string, pattern = "ubiquitous"): string[] =>
    compileEars([file(req({ pattern, text }))]).problems.map((p) => p.message);

  it("[TST-008] accepts one obligation of a named system in each pattern", () => {
    expect(messages("The kernel shall sign every ledger entry.")).toEqual([]);
    expect(messages("When a run starts, the runner shall checkpoint each task.", "event")).toEqual([]);
    expect(messages("While a set is frozen, the kernel shall refuse edits.", "state")).toEqual([]);
    expect(messages("If a hash differs, then the harness shall block publication.", "unwanted")).toEqual([]);
    expect(messages("Where a GPU is present, the executor shall record its model.", "optional")).toEqual([]);
    expect(messages("The contracts toolchain shall, in both languages, accept every valid fixture.")).toEqual([]);
  });

  it("[TST-008] rejects a requirement with two obligations or none", () => {
    expect(messages("The kernel shall sign entries, and the harness shall verify them.")).toContain(
      "text says shall 2 times; a requirement states exactly one obligation",
    );
    expect(messages("The kernel signs entries.")).toContain("text says shall 0 times; a requirement states exactly one obligation");
  });

  it("[TST-008] rejects passive text that names no system", () => {
    expect(messages("Logs shall be structured.")).toContain('text does not name the system that shall act ("the <system> shall ...")');
    expect(messages("If a hash differs, then publication shall be blocked.", "unwanted")).toContain(
      'text does not name the system that shall act ("the <system> shall ...")',
    );
  });

  it.each(["should", "may", "appropriate", "as needed", "and/or", "robust", "TBD"])("[TST-008] rejects vague wording: %s", (word) => {
    expect(messages(`The kernel shall retry ${word} on failure.`)).toContain(`text uses vague or optional wording "${word}"`);
  });

  it("[TST-008] does not mistake words that only contain a vague word", () => {
    expect(messages("The kernel shall record the maybe_flag column and its etcetera field.")).toEqual([]);
  });
});

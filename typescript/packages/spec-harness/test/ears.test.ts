import { describe, expect, it } from "vitest";

import { EarsDirectory } from "../src/adapters/fs.ts";
import { compileEars, renderRequirementsMarkdown } from "../src/ears.ts";
import { compileRequirements } from "../src/harness.ts";
import { repo } from "./helpers.ts";

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
    expect(compiled.requirements).toHaveLength(62);
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

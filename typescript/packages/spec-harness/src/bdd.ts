// The BDD binding check (TST-010): a Gherkin Rule is a requirement, titled "<ID>: <its .ears text>".
// The features are data to this module; it checks them against the compiled requirements so a Rule can
// neither drift from the requirement it claims to verify nor exist without one (ESI.ts spec:audit).
import type { Requirement } from "./ears.ts";

export interface FeatureFile {
  path: string;
  text: string;
}

export interface BddScenario {
  name: string;
  line: number;
}

export interface BddRule {
  source: string;
  line: number;
  title: string;
  id?: string;
  scenarios: BddScenario[];
}

export interface BddProblem {
  source: string;
  line: number;
  message: string;
}

export interface BddReport {
  rules: BddRule[];
  problems: BddProblem[];
  /** requirement id -> number of scenarios that verify it */
  coverage: Record<string, number>;
}

const RULE = /^\s*Rule:\s*(.*\S)\s*$/;
const SCENARIO = /^\s*(Scenario|Example|Scenario Outline|Scenario Template):\s*(.*\S)\s*$/;
const FEATURE = /^\s*Feature:/;
const TITLE = /^([A-Z]{3}-\d{3}): (.+)$/;

/** The Rules and scenarios of one feature file. A Background or Examples block ends nothing. */
export function parseFeature(file: FeatureFile): { rules: BddRule[]; orphans: BddScenario[]; hasFeature: boolean } {
  const rules: BddRule[] = [];
  const orphans: BddScenario[] = [];
  let hasFeature = false;
  let current: BddRule | undefined;
  file.text.split(/\r?\n/).forEach((raw, index) => {
    const line = index + 1;
    if (raw.trimStart().startsWith("#")) return;
    if (FEATURE.test(raw)) hasFeature = true;
    const rule = RULE.exec(raw);
    if (rule) {
      const title = rule[1] ?? "";
      const id = TITLE.exec(title)?.[1];
      current = { source: file.path, line, title, scenarios: [], ...(id ? { id } : {}) };
      rules.push(current);
      return;
    }
    const scenario = SCENARIO.exec(raw);
    if (scenario) {
      const entry = { name: scenario[2] ?? "", line };
      if (current) current.scenarios.push(entry);
      else orphans.push(entry);
    }
  });
  return { rules, orphans, hasFeature };
}

export function checkFeatures(files: readonly FeatureFile[], requirements: readonly Requirement[]): BddReport {
  const byId = new Map(requirements.map((r) => [r.id, r]));
  const rules: BddRule[] = [];
  const problems: BddProblem[] = [];
  const coverage: Record<string, number> = {};
  if (files.length === 0) problems.push({ source: "", line: 0, message: "no .feature files, so no Rule verifies anything" });
  for (const file of [...files].sort((a, b) => a.path.localeCompare(b.path))) {
    const parsed = parseFeature(file);
    const problem = (line: number, message: string): void => {
      problems.push({ source: file.path, line, message });
    };
    if (!parsed.hasFeature) problem(1, "no Feature: line");
    for (const orphan of parsed.orphans) problem(orphan.line, `scenario "${orphan.name}" is not under a Rule`);
    for (const rule of parsed.rules) {
      rules.push(rule);
      const title = TITLE.exec(rule.title);
      if (!title || !rule.id) {
        problem(rule.line, `Rule "${rule.title}" is not titled "<ID>: <requirement text>"`);
        continue;
      }
      const requirement = byId.get(rule.id);
      if (!requirement) problem(rule.line, `Rule names ${rule.id}, which no .ears file defines`);
      else if (requirement.status === "retired") problem(rule.line, `Rule names ${rule.id}, which is retired`);
      else if (title[2] !== requirement.text)
        problem(rule.line, `Rule text for ${rule.id} differs from ${requirement.source}: expected "${requirement.text}"`);
      if (rule.scenarios.length === 0) problem(rule.line, `Rule ${rule.id} has no scenario, so it verifies nothing`);
      coverage[rule.id] = (coverage[rule.id] ?? 0) + rule.scenarios.length;
    }
  }
  return { rules, problems, coverage };
}

// The .ears compiler (ADR-005, §12): requirement files are data; the harness checks their grammar
// and compiles them into the tables, matrix seeds and marker lists the rest of the harness uses.
import { parse as parseYaml } from "yaml";

export const PATTERNS = {
  ubiquitous: /\bshall\b/,
  event: /^When\b.*\bshall\b/,
  state: /^While\b.*\bshall\b/,
  unwanted: /^If\b.*\bthen\b.*\bshall\b/,
  optional: /^Where\b.*\bshall\b/,
  complex: /^(While|When|Where|If)\b.*\bshall\b/,
} as const;

export type Pattern = keyof typeof PATTERNS;

/** One requirement, one obligation: the text says `shall` exactly once (TST-008). */
const SHALL = /\bshall\b/g;
/** The obligation has a named system as its subject: "the harness shall", "The kernel shall". */
const NAMED_SYSTEM = /\b[Tt]he [A-Za-z0-9][\w./'-]*(?: [\w./'-]+){0,5},? shall\b/;
/** Words that make a requirement untestable or optional (INCOSE GtWR R7, R8). */
export const VAGUE =
  /\b(should|may|might|could|appropriate(?:ly)?|adequate(?:ly)?|sufficient(?:ly)?|reasonable|robust|efficient(?:ly)?|flexible|user-friendly|quickly|as needed|if possible|where possible|and\/or|etc|TBD)\b/i;

/** The style problems of one requirement text, beyond its pattern template (TST-008). */
export function styleProblems(text: string): string[] {
  const problems: string[] = [];
  const shalls = text.match(SHALL)?.length ?? 0;
  if (shalls !== 1) problems.push(`text says shall ${shalls} times; a requirement states exactly one obligation`);
  if (!NAMED_SYSTEM.test(text)) problems.push('text does not name the system that shall act ("the <system> shall ...")');
  const vague = VAGUE.exec(text);
  if (vague) problems.push(`text uses vague or optional wording "${vague[0]}"`);
  return problems;
}
export type Status = "draft" | "approved" | "retired";
export type Verification = "test" | "analysis" | "inspection" | "demonstration";

const STATUSES: readonly Status[] = ["draft", "approved", "retired"];
const VERIFICATIONS: readonly Verification[] = ["test", "analysis", "inspection", "demonstration"];
export const REQUIREMENT_ID = /^[A-Z]{3}-\d{3}$/;

export interface Requirement {
  id: string;
  pattern: Pattern;
  text: string;
  rationale: string;
  verification: Verification;
  tiers: string[];
  gates: string[];
  status: Status;
  traces: string[];
  capability: string;
  owner: string;
  source: string;
}

export interface EarsFile {
  path: string;
  text: string;
}

export interface EarsProblem {
  source: string;
  id?: string;
  message: string;
}

export interface CompiledRequirements {
  requirements: Requirement[];
  problems: EarsProblem[];
}

const asStrings = (value: unknown): string[] =>
  Array.isArray(value) ? value.map((v) => String(v)) : value === undefined || value === null ? [] : [String(value)];

export function compileEars(files: readonly EarsFile[]): CompiledRequirements {
  const requirements: Requirement[] = [];
  const problems: EarsProblem[] = [];
  const seen = new Map<string, string>();
  for (const file of [...files].sort((a, b) => a.path.localeCompare(b.path))) {
    let doc: unknown;
    try {
      doc = parseYaml(file.text);
    } catch (error) {
      problems.push({ source: file.path, message: `not YAML: ${(error as Error).message}` });
      continue;
    }
    if (typeof doc !== "object" || doc === null) {
      problems.push({ source: file.path, message: "empty .ears file" });
      continue;
    }
    const record = doc as Record<string, unknown>;
    for (const key of ["capability", "owner", "requirements"]) {
      if (!(key in record)) problems.push({ source: file.path, message: `missing ${key}` });
    }
    const items = Array.isArray(record.requirements) ? (record.requirements as Record<string, unknown>[]) : [];
    for (const raw of items) {
      const id = String(raw.id ?? "?");
      const problem = (message: string): void => {
        problems.push({ source: file.path, id, message });
      };
      if (!REQUIREMENT_ID.test(id)) problem(`id ${id} does not look like ABC-123`);
      const previous = seen.get(id);
      if (previous) problem(`duplicate id ${id} (also in ${previous})`);
      seen.set(id, file.path);
      const pattern = String(raw.pattern ?? "") as Pattern;
      const text = String(raw.text ?? "");
      if (!(pattern in PATTERNS)) problem(`unknown pattern ${pattern}`);
      else if (!PATTERNS[pattern].test(text)) problem(`text does not follow the ${pattern} EARS template`);
      for (const message of styleProblems(text)) problem(message);
      const status = String(raw.status ?? "") as Status;
      if (!STATUSES.includes(status)) problem(`unknown status ${status}`);
      const verification = String(raw.verification ?? "") as Verification;
      if (!VERIFICATIONS.includes(verification)) problem(`unknown verification ${verification}`);
      requirements.push({
        id,
        pattern,
        text,
        rationale: String(raw.rationale ?? ""),
        verification,
        tiers: asStrings(raw.tiers),
        gates: asStrings(raw.gates),
        status,
        traces: asStrings(raw.traces),
        capability: String(record.capability ?? ""),
        owner: String(record.owner ?? ""),
        source: file.path,
      });
    }
  }
  requirements.sort((a, b) => a.id.localeCompare(b.id));
  return { requirements, problems };
}

const escapeCell = (text: string): string => text.replace(/\|/g, "\\|").replace(/\n/g, " ");

/** The §12 tables, grouped by capability, in the shape the spec prints. */
export function renderRequirementsMarkdown(requirements: readonly Requirement[]): string {
  const byCapability = new Map<string, Requirement[]>();
  for (const r of requirements) byCapability.set(r.capability, [...(byCapability.get(r.capability) ?? []), r]);
  const lines = ["# Requirements", "", "Compiled from `requirements/**/*.ears` by `spec-harness ears`. Do not edit.", ""];
  for (const [capability, rows] of [...byCapability].sort(([a], [b]) => a.localeCompare(b))) {
    lines.push(`## ${capability}`, "", "| ID | Pattern | Requirement | Verification | Tiers | Status |", "|---|---|---|---|---|---|");
    for (const r of rows) {
      lines.push(
        `| ${r.id} | ${r.pattern} | ${escapeCell(r.text)} | ${r.verification} | ${r.tiers.join(", ") || "—"} | ${r.status} |`,
      );
    }
    lines.push("");
  }
  return lines.join("\n");
}

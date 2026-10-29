// spec-harness <command>: ears, trace, conform, rederive, rederive-plan, admit, claims, guard, ratchet, report.
// Paths are relative to the current directory; CI runs it from the repository root.
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { parseArgs } from "node:util";

import { EarsDirectory, JsonFiles, JUnitFiles, LedgerFiles, ManifestFiles, PredeclarationFiles } from "./adapters/fs.ts";
import { renderRequirementsMarkdown } from "./ears.ts";
import { parseNameStatus, type GateSnapshot } from "./guard.ts";
import { admitAll, approvals, claimsAll, compileRequirements, conformAll, guardChanges, rederiveAll, rederivePlanAll, runTrace } from "./harness.ts";
import { ratchet } from "./ratchet.ts";
import { buildReleaseReport, renderReleaseReport, type Level, type TierStatus } from "./report.ts";
import { renderTraceabilityMarkdown } from "./trace.ts";

const write = (file: string, content: string): void => {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, content);
};
const json = (value: unknown): string => JSON.stringify(value, null, 2) + "\n";
const list = (value: string | undefined): string[] => (value ? value.split(",").map((s) => s.trim()).filter(Boolean) : []);

async function ears(argv: string[]): Promise<number> {
  const { values } = parseArgs({ args: argv, options: { root: { type: "string", default: "requirements" }, out: { type: "string" } } });
  const compiled = await compileRequirements(new EarsDirectory(values.root));
  for (const p of compiled.problems) console.error(`FAIL ${p.source}${p.id ? ` ${p.id}` : ""}: ${p.message}`);
  if (values.out) {
    write(path.join(values.out, "requirements.json"), json(compiled.requirements));
    write(path.join(values.out, "requirements.md"), renderRequirementsMarkdown(compiled.requirements));
  }
  console.log(`${compiled.requirements.length} requirements compiled, ${compiled.problems.length} problems`);
  return compiled.problems.length ? 1 : 0;
}

async function traceCommand(argv: string[]): Promise<number> {
  const { values } = parseArgs({
    args: argv,
    options: {
      requirements: { type: "string", default: "requirements" },
      junit: { type: "string", multiple: true, default: ["reports"] },
      experiments: { type: "string", default: "experiments" },
      ledger: { type: "string", default: "ledger" },
      tiers: { type: "string" },
      out: { type: "string", default: "reports/traceability" },
    },
  });
  const tiers = list(values.tiers);
  const { compiled, traceability } = await runTrace(
    {
      requirements: new EarsDirectory(values.requirements),
      tests: new JUnitFiles(values.junit),
      predeclarations: new PredeclarationFiles(values.experiments),
      ledger: new LedgerFiles(values.ledger),
    },
    tiers.length ? { tiers } : {},
  );
  write(path.join(values.out, "traceability.json"), json(traceability));
  write(path.join(values.out, "traceability.md"), renderTraceabilityMarkdown(traceability));
  for (const p of compiled.problems) console.error(`FAIL ${p.source}${p.id ? ` ${p.id}` : ""}: ${p.message}`);
  for (const v of traceability.violations) console.error(`FAIL ${v.kind}: ${v.message}`);
  for (const w of traceability.warnings) console.warn(`warn ${w.kind}: ${w.message}`);
  const s = traceability.summary;
  console.log(
    `${traceability.generatedFrom.tests} test results; covered ${s.covered}, failing ${s.failing}, untested ${s.untested}, ` +
      `out of scope ${s["out-of-scope"]}, draft ${s.draft}; ${traceability.violations.length} violations`,
  );
  return compiled.problems.length || traceability.violations.length ? 1 : 0;
}

async function conformCommand(argv: string[]): Promise<number> {
  const { positionals } = parseArgs({ args: argv, allowPositionals: true });
  const results = await conformAll(new ManifestFiles(positionals.length ? positionals : ["experiments"]));
  for (const r of results) {
    if (r.ok) console.log(`ok   ${r.path} (major ${r.major})`);
    else for (const e of r.errors) console.error(`FAIL ${r.path} ${e.path || "/"}: ${e.message}`);
  }
  console.log(`${results.length} manifests, ${results.filter((r) => !r.ok).length} non-conforming`);
  return results.every((r) => r.ok) ? 0 : 1;
}

async function rederiveCommand(argv: string[]): Promise<number> {
  const { values, positionals } = parseArgs({ args: argv, allowPositionals: true, options: { out: { type: "string" } } });
  const results = await rederiveAll(new ManifestFiles(positionals.length ? positionals : ["experiments"]));
  for (const r of results) {
    console.log(`${r.state.padEnd(11)} ${r.experimentId}: kernel ${r.kernelVerdict ?? "—"}, harness ${r.harnessVerdict} (${r.path})`);
    for (const d of r.disagreements) console.error(`  ${d.subject}: kernel ${JSON.stringify(d.kernel)} harness ${JSON.stringify(d.harness)}`);
  }
  if (values.out) write(values.out, json(results));
  return results.some((r) => r.state === "disagree") ? 1 : 0;
}

async function rederivePlanCommand(argv: string[]): Promise<number> {
  const { values, positionals } = parseArgs({ args: argv, allowPositionals: true, options: { out: { type: "string" } } });
  const results = await rederivePlanAll(new JsonFiles(positionals.length ? positionals : ["plans"]));
  for (const r of results) {
    console.log(`${r.state.padEnd(8)} ${r.planId}: ${r.itemsRederived ? "items re-derived" : "no candidates recorded; items not re-derived"} (${r.path})`);
    for (const d of r.disagreements) console.error(`  ${d.subject}: kernel ${JSON.stringify(d.kernel)} harness ${JSON.stringify(d.harness)}`);
  }
  if (values.out) write(values.out, json(results));
  console.log(`${results.length} plans, ${results.filter((r) => r.state === "disagree").length} disagreeing (INT-013)`);
  return results.some((r) => r.state === "disagree") ? 1 : 0;
}

async function admitCommand(argv: string[]): Promise<number> {
  const { positionals } = parseArgs({ args: argv, allowPositionals: true });
  const results = await admitAll(new ManifestFiles(positionals.length ? positionals : ["experiments"]));
  let blocked = 0;
  for (const r of results) {
    if (!r.unadmitted.length) continue;
    const verdict = r.verdict !== null;
    if (verdict) blocked++;
    for (const u of r.unadmitted) {
      const line = `${r.experimentId}: ${u.adapter} ${u.adapterVersion ?? "(no version)"} not admitted for ${u.productType} (${u.reason})`;
      if (verdict) console.error(`FAIL ${line}; the kernel issued ${r.verdict}`);
      else console.warn(`warn ${line}; no verdict may be issued`);
    }
  }
  console.log(`${results.length} manifests, ${blocked} with a verdict on an unadmitted adapter (INT-016)`);
  return blocked ? 1 : 0;
}

async function claimsCommand(argv: string[]): Promise<number> {
  const { values } = parseArgs({
    args: argv,
    options: {
      descriptors: { type: "string", multiple: true, default: ["capabilities/descriptors"] },
      records: { type: "string", multiple: true, default: ["capabilities/compatibility"] },
      out: { type: "string" },
    },
  });
  const checks = await claimsAll(new JsonFiles(values.descriptors), new JsonFiles(values.records));
  for (const c of checks) for (const r of c.removed) console.error(`FAIL ${c.sourceId}: claim ${r.productType} removed (${r.reason})`);
  if (values.out) for (const c of checks) write(path.join(values.out, `${c.sourceId}.json`), json(c.stripped));
  const removed = checks.reduce((n, c) => n + c.removed.length, 0);
  console.log(`${checks.length} descriptors, ${removed} claims without admission (INT-002)`);
  return removed ? 1 : 0;
}

function gitShow(ref: string, file: string): string | null {
  try {
    return execFileSync("git", ["show", `${ref}:${file}`], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] });
  } catch {
    return null;
  }
}

const gateIds = (text: string | null): string[] => {
  if (!text) return [];
  const doc = JSON.parse(text) as { gates?: { gate_id: string }[] };
  return (doc.gates ?? []).map((g) => g.gate_id);
};

async function guardCommand(argv: string[]): Promise<number> {
  const { values } = parseArgs({ args: argv, options: { base: { type: "string", default: "origin/main" }, head: { type: "string", default: "HEAD" } } });
  const range = `${values.base}...${values.head}`;
  const changes = parseNameStatus(execFileSync("git", ["diff", "--name-status", "-M", range], { encoding: "utf8" }));
  const mergeBase = execFileSync("git", ["merge-base", values.base, values.head], { encoding: "utf8" }).trim();
  const gates: GateSnapshot[] = changes
    .filter((c) => c.path.endsWith("predeclaration.json") && c.status !== "D")
    .map((c) => ({ path: c.path, before: gateIds(gitShow(mergeBase, c.path)), after: gateIds(gitShow(values.head, c.path)) }));
  const result = guardChanges(changes, gates);
  for (const t of result.triggers) console.log(`${t.kind}: ${t.subject}`);
  if (!result.ok) {
    console.error("FAIL this change adds behaviour (above) without changing any requirements/*.ears file (TST-002)");
    return 1;
  }
  console.log(result.triggers.length ? `ok: .ears changed (${result.earsChanged.join(", ")})` : "ok: no new use case, port, adapter or gate");
  return 0;
}

async function ratchetCommand(argv: string[]): Promise<number> {
  const { values } = parseArgs({
    args: argv,
    options: { scores: { type: "string" }, "high-water": { type: "string", default: "mutation/high-water.json" }, update: { type: "boolean", default: false } },
  });
  if (!values.scores) throw new Error("--scores <file> is required");
  const scores = JSON.parse(fs.readFileSync(values.scores, "utf8")) as Record<string, number>;
  const highWaterFile = values["high-water"];
  const highWater = fs.existsSync(highWaterFile) ? (JSON.parse(fs.readFileSync(highWaterFile, "utf8")) as Record<string, number>) : {};
  const result = ratchet(scores, highWater);
  for (const v of result.violations) console.error(`FAIL ${v.pkg}: ${v.score} is below high-water ${v.highWater} minus 2`);
  for (const m of result.missing) console.error(`FAIL ${m}: no mutation score in this run`);
  if (result.ok && values.update) write(highWaterFile, json(result.highWater));
  console.log(result.ok ? "mutation ratchet holds" : "mutation ratchet broken");
  return result.ok ? 0 : 1;
}

async function reportCommand(argv: string[]): Promise<number> {
  const { values } = parseArgs({
    args: argv,
    options: {
      version: { type: "string", default: "unreleased" },
      claim: { type: "string" },
      requirements: { type: "string", default: "requirements" },
      junit: { type: "string", multiple: true, default: ["reports"] },
      manifests: { type: "string", multiple: true, default: ["experiments"] },
      experiments: { type: "string", default: "experiments" },
      ledger: { type: "string", default: "ledger" },
      external: { type: "string", multiple: true, default: [] },
      mutation: { type: "string" },
      "high-water": { type: "string", default: "mutation/high-water.json" },
      evidence: { type: "string", multiple: true, default: [] },
      descriptors: { type: "string", multiple: true, default: ["capabilities/descriptors"] },
      records: { type: "string", multiple: true, default: ["capabilities/compatibility"] },
      out: { type: "string", default: "reports/release" },
    },
  });
  const tests = new JUnitFiles(values.junit);
  const ledger = new LedgerFiles(values.ledger);
  const { traceability } = await runTrace({
    requirements: new EarsDirectory(values.requirements),
    tests,
    predeclarations: new PredeclarationFiles(values.experiments),
    ledger,
  });
  const externalTiers = Object.fromEntries(values.external.map((e) => e.split("=") as [string, TierStatus]));
  const mutation = values.mutation
    ? ratchet(
        JSON.parse(fs.readFileSync(values.mutation, "utf8")) as Record<string, number>,
        fs.existsSync(values["high-water"]) ? (JSON.parse(fs.readFileSync(values["high-water"], "utf8")) as Record<string, number>) : {},
      )
    : undefined;
  const report = buildReleaseReport({
    version: values.version,
    claimedLevels: list(values.claim).map((l) => Number(l) as Level),
    tests: await tests.results(),
    externalTiers,
    traceability,
    rederivations: await rederiveAll(new ManifestFiles(values.manifests)),
    ...(mutation ? { mutation } : {}),
    approvals: await approvals(ledger),
    independentEvidence: values.evidence,
    claims: await claimsAll(new JsonFiles(values.descriptors), new JsonFiles(values.records)),
  });
  write(path.join(values.out, "release-report.json"), json(report));
  write(path.join(values.out, "release-report.md"), renderReleaseReport(report));
  for (const b of report.blocking) console.error(`FAIL ${b}`);
  console.log(report.ok ? `release ${report.version} is releasable` : `release ${report.version} is blocked`);
  return report.ok ? 0 : 1;
}

const COMMANDS: Record<string, (argv: string[]) => Promise<number>> = {
  ears,
  trace: traceCommand,
  conform: conformCommand,
  rederive: rederiveCommand,
  "rederive-plan": rederivePlanCommand,
  admit: admitCommand,
  claims: claimsCommand,
  guard: guardCommand,
  ratchet: ratchetCommand,
  report: reportCommand,
};

const [command, ...rest] = process.argv.slice(2);
const run = command ? COMMANDS[command] : undefined;
if (!run) {
  console.error(`usage: spec-harness <${Object.keys(COMMANDS).join("|")}> [options]`);
  process.exit(2);
}
process.exitCode = await run(rest);

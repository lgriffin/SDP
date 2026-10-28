// Mutation scores from Stryker's JSON reports, in the shape spec-harness ratchet reads (§11.5).
//   node typescript/tools/stryker-scores.mjs --out reports/mutation/typescript.json spec-harness relativity-fit
// Score = killed + timeout over every mutant that is not ignored, so uncovered code lowers it.
import fs from "node:fs";
import path from "node:path";

const args = process.argv.slice(2);
const outIndex = args.indexOf("--out");
if (outIndex < 0) throw new Error("--out <file> is required");
const out = args[outIndex + 1];
const packages = args.filter((_, i) => i !== outIndex && i !== outIndex + 1);

const scores = {};
for (const pkg of packages) {
  const file = path.join("typescript", "packages", pkg, "reports", "mutation", "mutation.json");
  const report = JSON.parse(fs.readFileSync(file, "utf8"));
  let detected = 0;
  let valid = 0;
  for (const f of Object.values(report.files)) {
    for (const m of f.mutants) {
      if (m.status === "Ignored" || m.status === "CompileError" || m.status === "RuntimeError") continue;
      valid += 1;
      if (m.status === "Killed" || m.status === "Timeout") detected += 1;
    }
  }
  scores[`typescript/${pkg}`] = valid ? Math.round((10000 * detected) / valid) / 100 : 0;
  console.log(`${pkg}: ${scores[`typescript/${pkg}`]}% of ${valid} mutants`);
}
fs.mkdirSync(path.dirname(out), { recursive: true });
fs.writeFileSync(out, JSON.stringify(scores, null, 2) + "\n");

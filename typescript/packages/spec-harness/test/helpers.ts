import fs from "node:fs";
import path from "node:path";

// Walk up to the workspace root rather than counting levels: Stryker runs the tests from a sandbox
// nested inside the package, where a fixed "../../../.." lands in the wrong directory.
const workspaceRoot = (dir: string): string => {
  for (let d = dir; ; d = path.dirname(d)) {
    if (fs.existsSync(path.join(d, "pnpm-workspace.yaml"))) return d;
    if (path.dirname(d) === d) throw new Error(`no pnpm-workspace.yaml above ${dir}`);
  }
};
export const repo = workspaceRoot(import.meta.dirname);
export const fixture = (name: string): unknown =>
  JSON.parse(fs.readFileSync(path.join(import.meta.dirname, "fixtures", name), "utf8"));
export const clone = <T>(value: T): T => structuredClone(value);

// Filesystem adapters for the harness ports: the primary transport is files on the NAS or in the repo.
import fs from "node:fs/promises";
import path from "node:path";

import type { EarsFile } from "../ears.ts";
import { parseJUnit, type TestResult } from "../junit.ts";
import type {
  DocumentSource,
  LedgerEntryDocument,
  LedgerSource,
  Located,
  ManifestSource,
  PredeclarationSource,
  RequirementSource,
  TestReportSource,
} from "../ports.ts";

export async function walk(target: string, accept: (file: string) => boolean): Promise<string[]> {
  const stat = await fs.stat(target).catch(() => null);
  if (!stat) return [];
  if (stat.isFile()) return accept(target) ? [target] : [];
  const out: string[] = [];
  for (const entry of (await fs.readdir(target, { withFileTypes: true })).sort((a, b) => a.name.localeCompare(b.name))) {
    if (entry.name === "node_modules" || entry.name.startsWith(".")) continue;
    out.push(...(await walk(path.join(target, entry.name), accept)));
  }
  return out;
}

const readJson = async (file: string): Promise<Located> => ({ path: file, document: JSON.parse(await fs.readFile(file, "utf8")) });

export class EarsDirectory implements RequirementSource {
  readonly root: string;
  constructor(root: string) {
    this.root = root;
  }
  async earsFiles(): Promise<EarsFile[]> {
    const files = await walk(this.root, (f) => f.endsWith(".ears"));
    return Promise.all(files.map(async (f) => ({ path: path.relative(process.cwd(), f), text: await fs.readFile(f, "utf8") })));
  }
}

export class JUnitFiles implements TestReportSource {
  readonly targets: readonly string[];
  constructor(targets: readonly string[]) {
    this.targets = targets;
  }
  async results(): Promise<TestResult[]> {
    const files = (await Promise.all(this.targets.map((t) => walk(t, (f) => f.endsWith(".xml"))))).flat();
    const all = await Promise.all(files.map(async (f) => parseJUnit(await fs.readFile(f, "utf8"), path.basename(f))));
    return all.flat();
  }
}

/** Manifests are `manifest.json` files (the kernel's logical path) or any JSON file named directly. */
export class ManifestFiles implements ManifestSource {
  readonly targets: readonly string[];
  constructor(targets: readonly string[]) {
    this.targets = targets;
  }
  async manifests(): Promise<Located[]> {
    const files = (
      await Promise.all(
        this.targets.map(async (t) =>
          (await fs.stat(t)).isFile() ? [t] : walk(t, (f) => path.basename(f) === "manifest.json"),
        ),
      )
    ).flat();
    return Promise.all(files.map(readJson));
  }
}

/** Every `*.json` file under the targets; a missing target holds none. */
export class JsonFiles implements DocumentSource {
  readonly targets: readonly string[];
  constructor(targets: readonly string[]) {
    this.targets = targets;
  }
  async documents(): Promise<Located[]> {
    const files = (await Promise.all(this.targets.map((t) => walk(t, (f) => f.endsWith(".json"))))).flat();
    return Promise.all(files.map(readJson));
  }
}

export class PredeclarationFiles implements PredeclarationSource {
  readonly root: string;
  constructor(root: string) {
    this.root = root;
  }
  async predeclarations(): Promise<Located[]> {
    return Promise.all((await walk(this.root, (f) => path.basename(f) === "predeclaration.json")).map(readJson));
  }
}

/** The ledger as JSON Lines files (one signed entry per line), as GitSignedLedger writes it. */
export class LedgerFiles implements LedgerSource {
  readonly root: string;
  constructor(root: string) {
    this.root = root;
  }
  async entries(): Promise<LedgerEntryDocument[]> {
    const files = await walk(this.root, (f) => f.endsWith(".jsonl"));
    const entries: LedgerEntryDocument[] = [];
    for (const f of files) {
      for (const line of (await fs.readFile(f, "utf8")).split("\n")) {
        if (line.trim()) entries.push(JSON.parse(line) as LedgerEntryDocument);
      }
    }
    return entries.sort((a, b) => a.sequence - b.sequence);
  }
}

// ProfileCatalogue: the versioned YAML test profiles in a directory (the same documents the Imviz
// plugin offers), plus every profile a past set ran under, so a scientist can rerun one. The kernel
// validates and hashes the document; here it is only read and passed on.
import fs from "node:fs/promises";
import path from "node:path";

import { parse } from "yaml";

import type { EvidenceSource, ProfileCatalogue, ProfileOption } from "../ports.ts";

function option(doc: unknown): ProfileOption | null {
  if (!doc || typeof doc !== "object") return null;
  const d = doc as Record<string, unknown>;
  if (typeof d.profile_id !== "string" || typeof d.version !== "string") return null;
  return { key: `${d.profile_id}@${d.version}`, description: typeof d.description === "string" ? d.description : "", document: d };
}

export class DirectoryProfileCatalogue implements ProfileCatalogue {
  readonly directory: string | undefined;
  readonly evidence: EvidenceSource;

  constructor(directory: string | undefined, evidence: EvidenceSource) {
    this.directory = directory;
    this.evidence = evidence;
  }

  async fromDirectory(): Promise<ProfileOption[]> {
    if (!this.directory) return [];
    let names: string[];
    try {
      names = (await fs.readdir(this.directory)).filter((n) => /\.ya?ml$/.test(n)).sort();
    } catch {
      return [];
    }
    const found: ProfileOption[] = [];
    for (const name of names) {
      const o = option(parse(await fs.readFile(path.join(this.directory, name), "utf8")));
      if (o) found.push(o);
    }
    return found;
  }

  async fromPastSets(): Promise<ProfileOption[]> {
    const found: ProfileOption[] = [];
    for (const summary of await this.evidence.investigationSets()) {
      const set = await this.evidence.investigationSet(summary.id);
      const o = option(set?.profile);
      if (o) found.push(o);
    }
    return found;
  }

  async profiles(): Promise<ProfileOption[]> {
    const byKey = new Map<string, ProfileOption>();
    for (const o of [...(await this.fromDirectory()), ...(await this.fromPastSets())]) {
      if (!byKey.has(o.key)) byKey.set(o.key, o);
    }
    return [...byKey.values()];
  }
}

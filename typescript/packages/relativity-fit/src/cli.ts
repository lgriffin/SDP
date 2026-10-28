#!/usr/bin/env node
// relativity-fit <bundle.json> [--out fit.json]: the independent alpha for every instrument and fit scenario.
import fs from "node:fs";
import { parseArgs } from "node:util";

import { fitBundle, type Bundle } from "./fit.ts";

const { values, positionals } = parseArgs({ allowPositionals: true, options: { out: { type: "string" }, nu: { type: "string", default: "4" } } });
const input = positionals[0];
if (!input) {
  console.error("usage: relativity-fit <bundle.json> [--out fit.json]");
  process.exit(2);
}
const bundle = JSON.parse(fs.readFileSync(input, "utf8")) as Bundle;
const pkg = JSON.parse(fs.readFileSync(new URL("../package.json", import.meta.url), "utf8")) as { name: string; version: string };
const result = {
  implementation: { name: pkg.name, version: pkg.version, language: "TypeScript", runtime: `node ${process.version}` },
  fits: fitBundle(bundle, Number(values.nu)),
};
const text = JSON.stringify(result, null, 1) + "\n";
if (values.out) fs.writeFileSync(values.out, text);
else process.stdout.write(text);

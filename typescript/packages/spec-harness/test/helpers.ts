import fs from "node:fs";
import path from "node:path";

export const repo = path.resolve(import.meta.dirname, "../../../..");
export const fixture = (name: string): unknown =>
  JSON.parse(fs.readFileSync(path.join(import.meta.dirname, "fixtures", name), "utf8"));
export const clone = <T>(value: T): T => structuredClone(value);

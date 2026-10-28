// Generate Zod schemas from contracts/schemas (CTR-001). CI reruns this and fails on any diff (CTR-002).
//
// Covers the JSON Schema subset the contracts use: type (incl. type arrays), properties, required,
// additionalProperties, enum, const, pattern, minLength, minimum, maximum, exclusiveMaximum,
// items, minItems, maxItems, uniqueItems, oneOf, anyOf, allOf, if/then and $ref (local, to
// another schema file, or to another file's $defs). Anything else fails generation loudly.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const schemasDir = path.resolve(here, "../../../../contracts/schemas");
const outDir = process.env.SDP_ZOD_OUT ? path.resolve(process.env.SDP_ZOD_OUT) : path.resolve(here, "../src/generated");

const KNOWN = new Set([
  "$schema", "$id", "$ref", "x-version", "x-unit", "title", "description", "$defs", "type", "properties", "required",
  "additionalProperties", "enum", "const", "pattern", "minLength", "minimum", "maximum", "exclusiveMaximum",
  "items", "minItems", "maxItems", "uniqueItems", "oneOf", "anyOf", "allOf", "if", "then", "format",
]);

const pascal = (s) => s.replace(/(^|[-_ ])(\w)/g, (_, __, c) => c.toUpperCase());
const moduleName = (file) => file.replace(/\.schema\.json$/, "");
const ident = (file) => pascal(moduleName(file)).replace(/^./, (c) => c.toLowerCase());

const files = fs.readdirSync(schemasDir).filter((f) => f.endsWith(".schema.json")).sort();
const docs = Object.fromEntries(files.map((f) => [f, JSON.parse(fs.readFileSync(path.join(schemasDir, f), "utf8"))]));

function rootName(file) {
  return docs[file].title ?? pascal(moduleName(file));
}

class FileGen {
  constructor(file) {
    this.file = file;
    this.doc = docs[file];
    this.imports = new Set();
    this.localDeps = new Map(); // def -> set of defs
  }

  ref(target, owner) {
    const [filePart, fragment] = target.split("#");
    if (filePart && filePart !== this.file) {
      if (!docs[filePart]) throw new Error(`${this.file}: unknown $ref ${target}`);
      this.imports.add(filePart);
      const name = fragment ? fragment.replace(/^\/\$defs\//, "") : rootName(filePart);
      return `${ident(filePart)}.${name}`;
    }
    if (!fragment?.startsWith("/$defs/")) throw new Error(`${this.file}: unsupported $ref ${target}`);
    const name = fragment.slice("/$defs/".length);
    if (owner) this.localDeps.get(owner)?.add(name);
    return name;
  }

  // Returns a Zod expression string for schema node `s`.
  expr(s, owner, depth = 1) {
    for (const key of Object.keys(s)) {
      if (!KNOWN.has(key)) throw new Error(`${this.file}: unsupported keyword ${key}`);
    }
    const pad = "  ".repeat(depth);
    let base;
    if (s.$ref) {
      base = this.ref(s.$ref, owner);
    } else if ("const" in s) {
      base = `z.literal(${JSON.stringify(s.const)})`;
    } else if (s.enum) {
      base = s.enum.every((v) => typeof v === "string")
        ? `z.enum(${JSON.stringify(s.enum)})`
        : `z.union([${s.enum.map((v) => `z.literal(${JSON.stringify(v)})`).join(", ")}])`;
    } else if (Array.isArray(s.type)) {
      const nonNull = s.type.filter((t) => t !== "null");
      const parts = nonNull.map((t) => this.typed({ ...s, type: t }, owner, depth));
      base = parts.length === 1 ? parts[0] : `z.union([${parts.join(", ")}])`;
      if (s.type.includes("null")) base += ".nullable()";
    } else if (s.type) {
      base = this.typed(s, owner, depth);
    } else if (s.oneOf || s.anyOf) {
      base = this.union(s.oneOf ?? s.anyOf, owner, depth);
    } else if (s.properties) {
      // properties without a type constrain objects only; other values pass, as in JSON Schema
      base = `z.unknown().superRefine(ifObject(${this.typed({ ...s, type: "object", additionalProperties: s.additionalProperties ?? true }, owner, depth)}))`;
      return base + this.trailers(s, owner, depth);
    } else {
      base = `z.unknown()`;
    }
    if (s.type && (s.oneOf || s.anyOf)) base += `.and(${this.union(s.oneOf ?? s.anyOf, owner, depth)})`;
    if (s.required && !s.properties) {
      base += `.superRefine(requireKeys(${JSON.stringify(s.required)}))`;
    }
    return base + this.trailers(s, owner, depth);
  }

  trailers(s, owner, depth) {
    const pad = "  ".repeat(depth);
    let out = "";
    for (const sub of s.allOf ?? []) out += `\n${pad}.superRefine(${this.refinement(sub, owner, depth + 1)})`;
    if (s.if) out += `\n${pad}.superRefine(${this.refinement(s, owner, depth + 1)})`;
    return out;
  }

  refinement(sub, owner, depth) {
    if (sub.if) {
      const cond = this.expr(sub.if, owner, depth);
      const then = this.expr(sub.then ?? {}, owner, depth);
      return `when(${cond}, ${then})`;
    }
    return `also(${this.expr(sub, owner, depth)})`;
  }

  union(options, owner, depth) {
    const nonNull = options.filter((o) => o.type !== "null" || Object.keys(o).length !== 1);
    const nullable = nonNull.length !== options.length;
    const parts = nonNull.map((o) => this.expr(o, owner, depth));
    let out = parts.length === 1 ? parts[0] : `z.union([${parts.join(", ")}])`;
    if (nullable) out += ".nullable()";
    return out;
  }

  typed(s, owner, depth) {
    const pad = "  ".repeat(depth);
    switch (s.type) {
      case "string": {
        let out = "z.string()";
        if (s.minLength !== undefined) out += `.min(${s.minLength})`;
        if (s.pattern) out += `.regex(new RegExp(${JSON.stringify(s.pattern)}, "u"))`;
        return out;
      }
      case "number":
      case "integer": {
        let out = s.type === "integer" ? "z.number().int()" : "z.number()";
        if (s.minimum !== undefined) out += `.gte(${s.minimum})`;
        if (s.maximum !== undefined) out += `.lte(${s.maximum})`;
        if (s.exclusiveMaximum !== undefined) out += `.lt(${s.exclusiveMaximum})`;
        return out;
      }
      case "boolean":
        return "z.boolean()";
      case "null":
        return "z.null()";
      case "array": {
        let out = `z.array(${s.items ? this.expr(s.items, owner, depth) : "z.unknown()"})`;
        if (s.minItems !== undefined) out += `.min(${s.minItems})`;
        if (s.maxItems !== undefined) out += `.max(${s.maxItems})`;
        if (s.uniqueItems) out += ".superRefine(uniqueItems)";
        return out;
      }
      case "object": {
        const props = s.properties ?? {};
        const required = new Set(s.required ?? []);
        const extra = s.additionalProperties;
        if (!Object.keys(props).length && extra && typeof extra === "object") {
          return `z.record(z.string(), ${this.expr(extra, owner, depth)})`;
        }
        const lines = Object.entries(props).map(([key, value]) => {
          const e = this.expr(value, owner, depth + 1);
          return `${pad}  ${JSON.stringify(key)}: ${e}${required.has(key) ? "" : ".optional()"},`;
        });
        let out = `z.object({\n${lines.join("\n")}\n${pad}})`;
        if (!Object.keys(props).length) out = "z.object({})";
        if (extra === false) out += ".strict()";
        else if (extra && typeof extra === "object") out += `.catchall(${this.expr(extra, owner, depth)})`;
        else out += ".passthrough()";
        const missing = [...required].filter((k) => !(k in props));
        if (missing.length) out += `.superRefine(requireKeys(${JSON.stringify(missing)}))`;
        return out;
      }
      default:
        throw new Error(`${this.file}: unsupported type ${s.type}`);
    }
  }

  generate() {
    const defs = this.doc.$defs ?? {};
    const bodies = new Map();
    for (const [name, schema] of Object.entries(defs)) {
      this.localDeps.set(name, new Set());
      bodies.set(name, this.expr(schema, name));
    }
    const ordered = [];
    const state = new Map();
    const visit = (name) => {
      if (state.get(name) === "done") return;
      if (state.get(name) === "busy") throw new Error(`${this.file}: recursive $defs (${name}) are not supported`);
      state.set(name, "busy");
      for (const dep of [...this.localDeps.get(name)].sort()) visit(dep);
      state.set(name, "done");
      ordered.push(name);
    };
    for (const name of Object.keys(defs).sort()) visit(name);

    const root = this.doc.type || this.doc.properties ? this.expr(this.doc, undefined) : undefined;
    const out = [
      `// Generated from contracts/schemas/${this.file} by scripts/generate-zod.mjs. Do not edit.`,
      `import { z } from "zod";`,
      `import { also, ifObject, requireKeys, uniqueItems, when } from "../refinements.ts";`,
    ];
    for (const dep of [...this.imports].sort()) out.push(`import * as ${ident(dep)} from "./${moduleName(dep)}.ts";`);
    out.push("");
    out.push(`export const SCHEMA_ID = ${JSON.stringify(this.doc.$id)};`);
    out.push(`export const SCHEMA_VERSION = ${JSON.stringify(this.doc["x-version"])};`);
    for (const name of ordered) {
      out.push("");
      if (defs[name].description) out.push(`/** ${defs[name].description} */`);
      out.push(`export const ${name} = ${bodies.get(name)};`);
      out.push(`export type ${name} = z.infer<typeof ${name}>;`);
    }
    if (root) {
      const name = rootName(this.file);
      out.push("");
      if (this.doc.description) out.push(`/** ${this.doc.description} */`);
      out.push(`export const ${name} = ${root};`);
      out.push(`export type ${name} = z.infer<typeof ${name}>;`);
    }
    out.push("// silence unused-import checks in files without refinements");
    out.push("void also; void ifObject; void requireKeys; void uniqueItems; void when;");
    return out.join("\n") + "\n";
  }
}

fs.rmSync(outDir, { recursive: true, force: true });
fs.mkdirSync(outDir, { recursive: true });
const index = [`// Generated by scripts/generate-zod.mjs. Do not edit.`];
for (const file of files) {
  fs.writeFileSync(path.join(outDir, `${moduleName(file)}.ts`), new FileGen(file).generate());
  index.push(`export * as ${ident(file)} from "./${moduleName(file)}.ts";`);
}
fs.writeFileSync(path.join(outDir, "index.ts"), index.join("\n") + "\n");
console.log(`generated ${files.length} modules into ${path.relative(process.cwd(), outDir)}`);

// Runtime helpers the generated schemas use for the JSON Schema keywords Zod has no direct form for.
import { z } from "zod";

type Refinement = (value: unknown, ctx: z.RefinementCtx) => void;

/** Every leaf issue, descending into union branches, so errors carry the deepest JSON path. */
export function leafIssues(error: z.ZodError): z.ZodIssue[] {
  const out: z.ZodIssue[] = [];
  for (const issue of error.issues) {
    if (issue.code === "invalid_union") {
      for (const branch of issue.unionErrors) out.push(...leafIssues(branch));
    }
    out.push(issue);
  }
  return out;
}

function copyIssues(error: z.ZodError, ctx: z.RefinementCtx): void {
  for (const issue of leafIssues(error)) {
    ctx.addIssue({ code: z.ZodIssueCode.custom, message: issue.message, path: issue.path });
  }
}

/** allOf: the value must also satisfy `schema`. */
export function also(schema: z.ZodTypeAny): Refinement {
  return (value, ctx) => {
    const result = schema.safeParse(value);
    if (!result.success) copyIssues(result.error, ctx);
  };
}

/** properties without a type: the constraints apply only when the value is an object. */
export function ifObject(schema: z.ZodTypeAny): Refinement {
  return (value, ctx) => {
    if (typeof value !== "object" || value === null || Array.isArray(value)) return;
    const result = schema.safeParse(value);
    if (!result.success) copyIssues(result.error, ctx);
  };
}

/** if/then: when the value satisfies `condition`, it must satisfy `then`. */
export function when(condition: z.ZodTypeAny, then: z.ZodTypeAny): Refinement {
  return (value, ctx) => {
    if (!condition.safeParse(value).success) return;
    const result = then.safeParse(value);
    if (!result.success) copyIssues(result.error, ctx);
  };
}

/** required without properties: each key must be present. */
export function requireKeys(keys: readonly string[]): Refinement {
  return (value, ctx) => {
    if (typeof value !== "object" || value === null || Array.isArray(value)) return;
    for (const key of keys) {
      if (!(key in value)) ctx.addIssue({ code: z.ZodIssueCode.custom, message: `${key} is required`, path: [key] });
    }
  };
}

/** uniqueItems, compared by JSON text. */
export function uniqueItems(value: unknown[], ctx: z.RefinementCtx): void {
  const seen = new Set<string>();
  value.forEach((item, index) => {
    const key = JSON.stringify(item);
    if (seen.has(key)) ctx.addIssue({ code: z.ZodIssueCode.custom, message: "items must be unique", path: [index] });
    seen.add(key);
  });
}

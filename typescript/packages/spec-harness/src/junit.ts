// JUnit reader for both toolchains (§11.2). pytest writes requirement markers as
// <property name="req" value="KRN-002"/> (junit_family=xunit1); vitest titles carry them as "[KRN-002]".
// Gate markers work the same way with name="gate" or "[gate:REL-G-01]".
import { XMLParser } from "fast-xml-parser";

export type Outcome = "passed" | "failed" | "skipped";

export interface TestResult {
  id: string;
  name: string;
  classname: string;
  outcome: Outcome;
  requirements: string[];
  gates: string[];
  tiers: string[];
  report: string;
}

const REQ_IN_TITLE = /\[([A-Z]{3}-\d{3})\]/g;
const GATE_IN_TITLE = /\[gate:([A-Z]{2,4}-G-\d{2})\]/g;
const TIER_IN_TITLE = /\[tier:([A-Za-z0-9-]+)\]/g;

const parser = new XMLParser({
  ignoreAttributes: false,
  attributeNamePrefix: "",
  isArray: (name) => ["testsuite", "testcase", "property", "failure", "error", "skipped"].includes(name),
});

type Node = Record<string, unknown>;
const list = (value: unknown): Node[] => (Array.isArray(value) ? (value as Node[]) : value ? [value as Node] : []);

function suites(node: Node): Node[] {
  const own = list(node.testsuite);
  return own.flatMap((suite) => [suite, ...suites(suite)]);
}

function matches(text: string, pattern: RegExp): string[] {
  return [...text.matchAll(pattern)].map((m) => m[1] ?? "");
}

export function parseJUnit(xml: string, report: string): TestResult[] {
  const doc = parser.parse(xml) as Node;
  const roots: Node[] = doc.testsuites ? [doc.testsuites as Node] : [{ testsuite: doc.testsuite }];
  const results: TestResult[] = [];
  for (const root of roots) {
    for (const suite of suites(root)) {
      for (const tc of list(suite.testcase)) {
        const name = String(tc.name ?? "");
        const classname = String(tc.classname ?? suite.name ?? "");
        const properties = list((tc.properties as Node | undefined)?.property);
        const prop = (key: string): string[] =>
          properties.filter((p) => p.name === key).map((p) => String(p.value ?? ""));
        const outcome: Outcome =
          list(tc.failure).length || list(tc.error).length ? "failed" : list(tc.skipped).length ? "skipped" : "passed";
        const unique = (items: string[]): string[] => [...new Set(items)].sort();
        results.push({
          id: `${classname}::${name}`,
          name,
          classname,
          outcome,
          requirements: unique([...prop("req"), ...matches(name, REQ_IN_TITLE)]),
          gates: unique([...prop("gate"), ...matches(name, GATE_IN_TITLE)]),
          tiers: unique([...prop("tier"), ...matches(name, TIER_IN_TITLE)]),
          report,
        });
      }
    }
  }
  return results;
}

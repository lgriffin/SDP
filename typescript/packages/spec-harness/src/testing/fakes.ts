// In-memory implementations of the harness ports, for tests (fakes, not mocks: TST-005).
import type { EarsFile } from "../ears.ts";
import type { TestResult } from "../junit.ts";
import type {
  LedgerEntryDocument,
  LedgerSource,
  Located,
  ManifestSource,
  PredeclarationSource,
  RequirementSource,
  TestReportSource,
} from "../ports.ts";

export class InMemoryRequirements implements RequirementSource {
  readonly files: EarsFile[];
  constructor(files: EarsFile[] = []) {
    this.files = files;
  }
  async earsFiles(): Promise<EarsFile[]> {
    return [...this.files];
  }
}

export class InMemoryTestReports implements TestReportSource {
  readonly items: TestResult[];
  constructor(items: TestResult[] = []) {
    this.items = items;
  }
  async results(): Promise<TestResult[]> {
    return [...this.items];
  }
}

export class InMemoryManifests implements ManifestSource {
  readonly items: Located[];
  constructor(items: Located[] = []) {
    this.items = items;
  }
  async manifests(): Promise<Located[]> {
    return [...this.items];
  }
}

export class InMemoryPredeclarations implements PredeclarationSource {
  readonly items: Located[];
  constructor(items: Located[] = []) {
    this.items = items;
  }
  async predeclarations(): Promise<Located[]> {
    return [...this.items];
  }
}

export class InMemoryLedger implements LedgerSource {
  readonly items: LedgerEntryDocument[];
  constructor(items: LedgerEntryDocument[] = []) {
    this.items = items;
  }
  async entries(): Promise<LedgerEntryDocument[]> {
    return [...this.items].sort((a, b) => a.sequence - b.sequence);
  }
}

export function testResult(partial: Partial<TestResult> & { name: string }): TestResult {
  return {
    id: `${partial.classname ?? "suite"}::${partial.name}`,
    classname: "suite",
    outcome: "passed",
    requirements: [],
    gates: [],
    tiers: [],
    report: "fake.xml",
    ...partial,
  };
}

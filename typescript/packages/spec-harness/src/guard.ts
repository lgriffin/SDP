// The behaviour-change guard (TST-002): a change that adds a use case, port, adapter or gate must also
// change a .ears file, so requirements move with the behaviour they describe.

export interface FileChange {
  status: "A" | "M" | "D" | "R" | "C";
  path: string;
}

export interface GateSnapshot {
  path: string;
  before: readonly string[];
  after: readonly string[];
}

export interface GuardTrigger {
  kind: "use-case" | "port" | "adapter" | "gate";
  subject: string;
}

export interface GuardResult {
  triggers: GuardTrigger[];
  earsChanged: string[];
  ok: boolean;
}

const RULES: { kind: GuardTrigger["kind"]; pattern: RegExp }[] = [
  { kind: "use-case", pattern: /^python\/packages\/sdp-kernel\/src\/sdp\/kernel\/application\/(?!__init__)[^/]+\.py$/ },
  { kind: "port", pattern: /^python\/packages\/sdp-kernel\/src\/sdp\/kernel\/ports\/(?!__init__)[^/]+\.py$/ },
  { kind: "adapter", pattern: /^python\/packages\/sdp-adapters\/src\/sdp\/adapters\/(?:.+\/)?(?!__init__)[^/]+\.py$/ },
  { kind: "port", pattern: /^typescript\/packages\/spec-harness\/src\/ports\.ts$/ },
];

export function guard(changes: readonly FileChange[], gates: readonly GateSnapshot[] = []): GuardResult {
  const triggers: GuardTrigger[] = [];
  for (const change of changes) {
    if (change.status !== "A" && change.status !== "R" && change.status !== "C") continue;
    for (const rule of RULES) if (rule.pattern.test(change.path)) triggers.push({ kind: rule.kind, subject: change.path });
  }
  for (const snapshot of gates) {
    const before = new Set(snapshot.before);
    for (const gate of snapshot.after) if (!before.has(gate)) triggers.push({ kind: "gate", subject: `${gate} in ${snapshot.path}` });
  }
  const earsChanged = changes.filter((c) => c.path.startsWith("requirements/") && c.path.endsWith(".ears")).map((c) => c.path);
  return { triggers, earsChanged, ok: triggers.length === 0 || earsChanged.length > 0 };
}

/** Parse `git diff --name-status` output. Renames and copies keep their destination path. */
export function parseNameStatus(text: string): FileChange[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const parts = line.split("\t");
      const status = (parts[0] ?? "M").charAt(0) as FileChange["status"];
      return { status, path: parts[parts.length - 1] ?? "" };
    });
}

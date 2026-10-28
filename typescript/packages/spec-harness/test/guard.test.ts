import { describe, expect, it } from "vitest";

import { guard, parseNameStatus } from "../src/guard.ts";

const kernel = "python/packages/sdp-kernel/src/sdp/kernel";

describe("[TST-002] behaviour changes need an .ears change", () => {
  it("[TST-002] fails a new use case without an .ears change", () => {
    const result = guard(parseNameStatus(`A\t${kernel}/application/reproduce.py\nM\tREADME.md`));
    expect(result.ok).toBe(false);
    expect(result.triggers).toEqual([{ kind: "use-case", subject: `${kernel}/application/reproduce.py` }]);
  });

  it("[TST-002] passes the same change when an .ears file changes too", () => {
    const result = guard(parseNameStatus(`A\t${kernel}/ports/telemetry.py\nM\trequirements/nfr/platform.ears`));
    expect(result.ok).toBe(true);
  });

  it("[TST-002] treats new adapters and new gates as behaviour", () => {
    const result = guard(
      parseNameStatus("A\tpython/packages/sdp-adapters/src/sdp/adapters/archives/gaia.py\nM\texperiments/uc02/predeclaration.json"),
      [{ path: "experiments/uc02/predeclaration.json", before: ["REL-G-01"], after: ["REL-G-01", "REL-G-10"] }],
    );
    expect(result.triggers.map((t) => t.kind)).toEqual(["adapter", "gate"]);
    expect(result.ok).toBe(false);
  });

  it("[TST-002] ignores edits, deletions and package markers", () => {
    const result = guard(parseNameStatus(`M\t${kernel}/application/experiments.py\nD\t${kernel}/ports/old.py\nA\t${kernel}/ports/__init__.py`));
    expect(result.triggers).toEqual([]);
    expect(result.ok).toBe(true);
  });

  it("follows renames to their destination", () => {
    expect(parseNameStatus("R100\told.py\tnew.py")).toEqual([{ status: "R", path: "new.py" }]);
  });
});

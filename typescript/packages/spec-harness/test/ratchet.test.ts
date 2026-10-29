import { describe, expect, it } from "vitest";

import { ratchet } from "../src/ratchet.ts";

describe("[TST-004] mutation-score ratchet", () => {
  it("[TST-004] allows a drop of up to 2 points below the high-water mark", () => {
    expect(ratchet({ kernel: 80 }, { kernel: 82 }).ok).toBe(true);
  });

  it("[TST-004] fails a drop of more than 2 points", () => {
    const result = ratchet({ kernel: 79.9 }, { kernel: 82 });
    expect(result.ok).toBe(false);
    expect(result.violations).toEqual([{ pkg: "kernel", score: 79.9, highWater: 82 }]);
  });

  it("[TST-004] raises the mark and never lowers it", () => {
    expect(ratchet({ kernel: 90, harness: 70 }, { kernel: 85 }).highWater).toEqual({ kernel: 90, harness: 70 });
    expect(ratchet({ kernel: 84 }, { kernel: 85 }).highWater).toEqual({ kernel: 85 });
  });

  it("[TST-004] fails when a tracked package has no score", () => {
    const result = ratchet({}, { kernel: 85 });
    expect(result.ok).toBe(false);
    expect(result.missing).toEqual(["kernel"]);
  });

  it("[TST-004] lists packages below the 90 goal, lowest first, without failing", () => {
    const result = ratchet({ kernel: 95, stellar: 70, harness: 89.9, ui: 70 }, {});
    expect(result.ok).toBe(true);
    expect(result.belowGoal).toEqual([
      { pkg: "stellar", score: 70 },
      { pkg: "ui", score: 70 },
      { pkg: "harness", score: 89.9 },
    ]);
    expect(ratchet({ kernel: 90 }, {}).belowGoal).toEqual([]);
    expect(ratchet({ kernel: 80 }, {}, 75).belowGoal).toEqual([]);
  });
});

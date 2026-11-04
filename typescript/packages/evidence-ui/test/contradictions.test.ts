// The survey-contradiction section's logic (INV-029): labels, rows, order and the summary sentence,
// on results the Python engine wrote for its synthetic field (src/lib/testing/contradictions.json).
import { describe, expect, it } from "vitest";

import {
  channelLabel,
  comparisonRows,
  contradictionResult,
  details,
  direction,
  positionResidual,
  summary,
  type ComparisonDoc,
} from "../src/lib/contradictions.ts";
import EXAMPLES from "../src/lib/testing/contradictions.json" with { type: "json" };
import type { TestResultDoc } from "../src/lib/view.ts";

const result = (name: keyof typeof EXAMPLES) => EXAMPLES[name] as unknown as TestResultDoc;

describe("survey contradictions [INV-029]", () => {
  it("[INV-029] finds the test's result among a star's results", () => {
    const other = { ...result("agrees"), test_name: "parallax-consistency" };
    expect(contradictionResult([other, result("moved")])).toBe(result("moved"));
    expect(contradictionResult([other])).toBeNull();
    expect(contradictionResult(null)).toBeNull();
  });

  it("[INV-029] names each channel the way a scientist says it", () => {
    expect(channelLabel("2mass:Ks")).toBe("2MASS Ks");
    expect(channelLabel("ps1:g")).toBe("Pan-STARRS g");
    expect(channelLabel("allwise:W1")).toBe("AllWISE W1");
    expect(channelLabel("2mass:position")).toBe("2MASS position");
    expect(channelLabel("image:jw02739-o001_i2d.fits:F200W", "image:NIRCAM")).toBe("NIRCAM F200W on jw02739-o001_i2d.fits");
    expect(channelLabel("image:hst_13926:position", "image:WFC3")).toBe("WFC3 position on hst_13926");
    expect(channelLabel("mystery:x")).toBe("mystery x");
  });

  it("[INV-029] puts the flagged comparison first and says how it differs", () => {
    const rows = comparisonRows(details(result("ks_excess")));
    expect(rows[0]).toMatchObject({ channel: "2mass:Ks", label: "2MASS Ks", verdict: "disagrees", why: "brighter than Gaia's colour predicts" });
    expect(rows[0]!.difference).toMatch(/^−0\.\d\d mag$/);
    expect(rows[0]!.z).toMatch(/^−\d+\.\dσ$/);
    expect(rows[0]!.when).toBe("1999.0 (17 yr before Gaia)");
    expect(rows[0]!.measured).toMatch(/^\d+\.\d\d mag, Gaia predicts \d+\.\d\d$/);
    expect(rows[0]!.expected).toMatch(/ mag \(from the field\)$/);
    const verdicts = rows.map((r) => r.verdict);
    expect(verdicts.lastIndexOf("disagrees")).toBeLessThan(verdicts.indexOf("agrees"));
    if (verdicts.includes("set aside")) expect(verdicts.lastIndexOf("agrees")).toBeLessThan(verdicts.indexOf("set aside"));
  });

  it("[INV-029] gives a moved star's offset in mas and its direction, after the field's offset", () => {
    const rows = comparisonRows(details(result("moved")));
    expect(rows[0]).toMatchObject({ channel: "2mass:position", verdict: "disagrees", why: "out of place" });
    expect(rows[0]!.difference).toMatch(/^\d+ mas east$/);
    const c = details(result("moved")).comparisons.find((x) => x.channel === "2mass:position")!;
    const [east, north] = positionResidual(c)!;
    expect(east).toBeGreaterThan(600);
    expect(Math.abs(north)).toBeLessThan(200);
  });

  it("[INV-029] names the reason a comparison was set aside", () => {
    const rows = comparisonRows(details(result("pair")));
    expect(rows.length).toBeGreaterThan(0);
    expect(rows.every((r) => r.verdict === "set aside" && r.why === "blended")).toBe(true);
    expect(summary(details(result("pair")))).toMatch(/^Nothing could be compared: all \d+ comparisons were set aside/);
  });

  it("[INV-029] says in one sentence what disagreed, by how much, and how to read it", () => {
    const said = summary(details(result("ks_excess")));
    expect(said).toMatch(/^2MASS Ks is 0\.\d\d mag brighter than Gaia's colour predicts, \d+\.\d times the expected scatter\. Reading: /);
    expect(summary(details(result("moved")))).toMatch(/^2MASS position is \d+ mas east out of place/);
    expect(summary(details(result("agrees")))).toMatch(/^Every survey agrees with Gaia within the expected scatter \(\d+ comparisons\)\.$/);
  });

  it("[INV-029] lists every disagreeing comparison when there is more than one", () => {
    const d = details(result("ks_excess"));
    const both = { ...d, flagged: ["2mass:Ks", "2mass:J"] };
    expect(summary(both)).toContain("2 comparisons disagree: 2MASS Ks, 2MASS J.");
  });

  it("[INV-029] calls an image magnitude instrumental and splits the budget it was given", () => {
    const c: ComparisonDoc = {
      channel: "image:hst:F656N",
      survey: "image:WFC3",
      kind: "band",
      counted: true,
      set_aside: null,
      epoch_gap_years: -1.33,
      reported_sigma: 0.004,
      calibration_sigma: 0.003,
      total_sigma: 0.005,
      z: 0.5,
      unit: "mag",
      observed_mag: -4.25,
      predicted_mag: -4.3,
      field_offset_mag: 0.05,
      residual_mag: 0.0,
    };
    const [row] = comparisonRows({ ...details(result("agrees")), comparisons: [c], flagged: [] });
    expect(row).toMatchObject({ label: "WFC3 F656N on hst", verdict: "agrees", why: "", z: "+0.5σ" });
    expect(row!.measured).toBe("-4.25 (instrumental), Gaia predicts -4.25");
    expect(row!.expected).toBe("0.005 mag (0.004 reported, 0.003 from the field)");
    expect(row!.when).toBe("2014.7 (1.3 yr before Gaia)");
  });

  it("[INV-029] reads directions from east and north", () => {
    expect(direction(100, 0)).toBe("east");
    expect(direction(-5, -100)).toBe("south");
    expect(direction(70, 70)).toBe("north-east");
    expect(direction(-70, 70)).toBe("north-west");
  });
});

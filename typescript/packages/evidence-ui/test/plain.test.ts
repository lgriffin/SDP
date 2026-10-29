import { describe, expect, it } from "vitest";

import { generated } from "@sdp/contracts";

import {
  coneRegion,
  experimentJourney,
  experimentNextStep,
  fieldLabel,
  gateRuler,
  gateSentence,
  humanBytes,
  humanDuration,
  humanTime,
  needsSignature,
  checkName,
  imageTitle,
  observedOn,
  productFile,
  starStatus,
  planBadge,
  planProgress,
  runJourney,
  setJourney,
  verdictSentence,
  waitingItems,
} from "../src/lib/plain.ts";
import type { Plan } from "../src/lib/ports.ts";
import { FIXTURES } from "../src/lib/testing/fixtures.ts";
import { formatNumber } from "../src/lib/view.ts";

const statuses = (state: string) => experimentJourney(state).map((s) => s.status);

describe("experiment journey", () => {
  it("marks where each state is on the path", () => {
    expect(statuses("defined")).toEqual(["done", "current", "todo", "todo", "todo"]);
    expect(statuses("validated")).toEqual(["done", "done", "done", "current", "todo"]);
    expect(statuses("published")).toEqual(["done", "done", "done", "done", "done"]);
  });

  it("stops a failed run at the run step and leaves the rest to come", () => {
    expect(statuses("failed")).toEqual(["done", "failed", "todo", "todo", "todo"]);
    expect(statuses("running")).toEqual(["done", "current", "todo", "todo", "todo"]);
  });
});

describe("next step", () => {
  it("[INV-015] offers buttons for unsigned commands and commands to copy for signed ones", () => {
    expect(experimentNextStep({ id: "e", state: "defined" }).kind).toBe("run");
    expect(experimentNextStep({ id: "e", state: "failed" }).kind).toBe("run");
    expect(experimentNextStep({ id: "e", state: "completed" }).kind).toBe("validate");
    expect(experimentNextStep({ id: "e", state: "validated" }).command).toContain("sdp dossier assemble e");
    expect(experimentNextStep({ id: "e", state: "validated", dossiers: [{ id: "d" }] }).command).toBe("sdp approve e --reviewer <your-signer-name>");
    expect(experimentNextStep({ id: "e", state: "approved", dossiers: [{ id: "d" }] }).command).toBe("sdp publish d");
    expect(experimentNextStep({ id: "e", state: "published" }).kind).toBe("none");
  });

  it("describes every verdict in words", () => {
    for (const v of ["VERIFIED", "REJECTED", "INCONCLUSIVE", "CANDIDATE"]) expect(verdictSentence(v)).not.toMatch(/does not know/);
  });
});

describe("gate ruler", () => {
  it("places the value and the threshold, shading the passing side", () => {
    const r = gateRuler({ value: 0.25, threshold: 0.5, comparator: "le" });
    expect(r).not.toBeNull();
    expect(r!.value).toBeLessThan(r!.threshold);
    expect([r!.passFrom, r!.passTo]).toEqual([0, r!.threshold]);
    const above = gateRuler({ value: 3, threshold: 2, comparator: "gt" })!;
    expect([above.passFrom, above.passTo]).toEqual([above.threshold, 100]);
    expect(above.value).toBeGreaterThan(above.threshold);
  });

  it("uses a log scale across decades and gives up on non-numbers", () => {
    expect(gateRuler({ value: 2e-4, threshold: 0.05, comparator: "lt" })!.log).toBe(true);
    expect(gateRuler({ value: "x", threshold: 1, comparator: "lt" })).toBeNull();
    expect(gateRuler({ value: null, threshold: 1, comparator: "lt" })).toBeNull();
  });

  it("keeps every position on the scale", () => {
    for (const [v, t] of [[0.003, 0.05], [0.9998, 0.05], [-2, 3], [5, 5]] as const) {
      const r = gateRuler({ value: v, threshold: t, comparator: "lt" })!;
      for (const x of [r.value, r.threshold]) expect(x >= 0 && x <= 100).toBe(true);
    }
  });

  it("reads a gate as a sentence", () => {
    expect(gateSentence({ finding: "residual_rms", comparator: "le", threshold: 0.5, unit: "arcsec" }, formatNumber)).toBe(
      "Residual rms must be at most 0.5 arcsec",
    );
  });
});

describe("plans", () => {
  it("fixture plans follow the acquisition-plan contract", () => {
    for (const p of FIXTURES.plans) {
      const parsed = generated.acquisitionPlan.AcquisitionPlan.safeParse(p.document);
      expect(parsed.success, parsed.success ? "" : JSON.stringify(parsed.error.issues)).toBe(true);
      expect(p.id).toBe(p.document.plan_id);
    }
  });

  it("needs a signature only above the threshold", () => {
    const sizes = FIXTURES.plans.map((p) => [p.document.cost.total_bytes, needsSignature(p)]);
    expect(sizes).toEqual([[13_000_000, false], [15_000_000, false], [1_290_000_000, true], [8_417_600, false]]);
  });

  it("puts drafted plans, runnable experiments and open candidates in the waiting list", () => {
    const items = waitingItems(FIXTURES.experiments, FIXTURES.investigationSets, { "invset-000001": 3 }, FIXTURES.plans);
    expect(items.map((i) => i.action)).toEqual(["Approve or refuse", "Needs a signed approval", "Run it again", "Run it", "Review"]);
  });

  it("[INV-018] lists an approved plan as waiting to download, running or stopped", () => {
    const [, small] = FIXTURES.plans as Plan[];
    const approved = { ...small!, state: "auto_approved" as const };
    const run = { state: "running" as const, started_at: "2026-10-29T22:00:00Z", finished_at: null, error: null };
    const items = (p: Plan) => waitingItems([], [], {}, [p]).map((i) => `${i.detail} / ${i.action}`);
    expect(items(approved)).toEqual(["Approved, not downloaded yet / Download"]);
    expect(items({ ...approved, run })).toEqual(["Downloading now / See progress"]);
    expect(items({ ...approved, run: { ...run, state: "failed", error: "504" } })).toEqual(["The download stopped before it finished / See why"]);
  });

  it("follows a set from plan to review", () => {
    const set = FIXTURES.investigationSets[0]!;
    const plan = FIXTURES.plans[0] as Plan;
    expect(setJourney(set, plan, 3).map((s) => `${s.label}:${s.status}`)).toEqual(["Plan:done", "Search:done", "Test:done", "Review:current"]);
    expect(setJourney(set, null, 0).map((s) => s.status)).toEqual(["done", "done", "done"]);
  });
});

describe("a plan's download", () => {
  const run = { state: "running" as const, started_at: "2026-10-29T22:00:00Z", finished_at: null, error: null };

  it("[INV-018] is running or stopped only while the plan is approved", () => {
    expect(planProgress({ state: "auto_approved", run })).toBe("running");
    expect(planProgress({ state: "approved", run: { ...run, state: "failed" } })).toBe("stopped");
    expect(planProgress({ state: "approved", run: { ...run, state: "finished" } })).toBeNull();
    expect(planProgress({ state: "approved", run: null })).toBeNull();
    expect(planProgress({ state: "executed", run })).toBeNull();
    expect(planProgress({ state: "drafted", run })).toBeNull();
    expect(planProgress({ state: "refused", run })).toBeNull();
    expect(planBadge({ state: "approved", run })).toEqual({ label: "Downloading", tone: "neutral" });
    expect(planBadge({ state: "approved", run: { ...run, state: "failed" } })).toEqual({ label: "Download stopped", tone: "bad" });
    expect(planBadge({ state: "executed", run })).toEqual({ label: "Downloaded", tone: "good" });
  });

  it("[INV-018] shows the stage it has reached, and where it stopped", () => {
    const steps = (stages: { stage: string; state: string }[] | null, stopped = false) => runJourney(stages, stopped).map((s) => s.status);
    expect(runJourney(null).map((s) => s.label)).toEqual(["Find the objects in the field", "Download and check the data", "Run the tests", "Rank the candidates"]);
    expect(steps(null)).toEqual(["current", "todo", "todo", "todo"]);
    expect(steps([{ stage: "identify", state: "completed" }, { stage: "retrieve", state: "pending" }])).toEqual(["done", "current", "todo", "todo"]);
    expect(steps([{ stage: "identify", state: "completed" }, { stage: "retrieve", state: "failed" }])).toEqual(["done", "failed", "todo", "todo"]);
    expect(steps([{ stage: "identify", state: "completed" }], true)).toEqual(["done", "failed", "todo", "todo"]);
  });
});

describe("formatting for people", () => {
  it("writes sizes, durations and times readably", () => {
    expect(humanBytes(512)).toBe("512 bytes");
    expect(humanBytes(15_000_000)).toBe("15 MB");
    expect(humanBytes(1_290_000_000)).toBe("1.29 GB");
    expect(humanDuration(30)).toBe("30 s");
    expect(humanDuration(5400)).toBe("1 h 30 min");
    expect(humanTime("2026-10-28T12:00:06+00:00")).toBe("28 Oct 2026, 12:00 UTC");
    expect(humanTime(null)).toBe("not yet");
  });

  it("names fields and builds the region the kernel parses", () => {
    expect(fieldLabel({ label: "Hyades" })).toBe("Hyades");
    expect(fieldLabel({ region: { ra_deg: 120, dec_deg: -10, radius_deg: 0.5 } })).toBe("RA 120°, Dec -10°, radius 0.5°");
    expect(coneRegion(120, -10, 0.5)).toBe("icrs;circle(120,-10,0.5)");
  });
});

describe("images of a field", () => {
  it("[INV-020] names checks, images, dates and what the image says about each star", () => {
    expect(checkName("has_wcs")).toBe("Sky coordinates (WCS)");
    expect(checkName("something.new")).toBe("something.new");
    expect(imageTitle({ instrument: "NIRCAM", filter_or_grating: "F090W" }, "x")).toBe("NIRCam F090W");
    expect(imageTitle({}, "jw/a_i2d.fits@cutout(1,2,40arcsec)")).toBe("a_i2d.fits@cutout(1,2,40arcsec)");
    expect(productFile("jw/a_i2d.fits@cutout(1,2,40arcsec)")).toBe("a_i2d.fits");
    expect(observedOn({ time: { start_mjd: 59750.97 } })).toBe("20 Jun 2022");
    expect(observedOn({})).toBeNull();
    expect(starStatus({ flagged: true, ambiguous: false, counterpart_px: null })).toBe("missing");
    expect(starStatus({ flagged: true, ambiguous: true, counterpart_px: [1, 2] })).toBe("displaced");
    expect(starStatus({ flagged: false, ambiguous: true, counterpart_px: [1, 2] })).toBe("ambiguous");
    expect(starStatus({ flagged: false, ambiguous: false, counterpart_px: [1, 2] })).toBe("matched");
  });
});

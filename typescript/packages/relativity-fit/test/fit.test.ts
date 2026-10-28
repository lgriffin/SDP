import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import fc from "fast-check";
import { describe, expect, it } from "vitest";

import { allScenarios, buildRows, fitBundle, fitRows, type Bundle, type InstrumentSet, type Scenario } from "../src/fit.ts";
import { STANDARD_PER_PROBABLE, erf, logGamma, normalQuantile } from "../src/stats.ts";

const here = path.dirname(fileURLToPath(import.meta.url));
const load = <T>(name: string): T => JSON.parse(fs.readFileSync(path.join(here, "fixtures", name), "utf8")) as T;

/** A noise-free instrument set: offsets, scale and rotation per plate, and a planted alpha. */
function planted(alpha: number, seed = 1): InstrumentSet {
  let state = seed;
  const rand = (): number => {
    state = (state * 48271) % 2147483647;
    return state / 2147483647;
  };
  const radius = 15.78;
  const positions = Array.from({ length: 8 }, () => {
    const r = radius * (1.6 + 4 * rand());
    const t = 2 * Math.PI * rand();
    return [r * Math.cos(t), r * Math.sin(t)] as const;
  });
  const plates = Array.from({ length: 4 }, (_, p) => {
    const [ax, ay, s, rot] = [rand() - 0.5, rand() - 0.5, 0.01 * (rand() - 0.5), 0.01 * (rand() - 0.5)];
    return {
      plate_id: `P${p}`,
      check_scale: s,
      check_rotation: rot,
      check_scale_sigma: 0.002,
      stars: positions.map(([x, y], i) => {
        const theta = Math.hypot(x, y);
        const g = radius / theta;
        return {
          star_id: String(i),
          x_arcmin: x,
          y_arcmin: y,
          dx_arcsec: ax + s * x + rot * y + (alpha * g * x) / theta,
          dy_arcsec: ay + s * y - rot * x + (alpha * g * y) / theta,
          error_arcsec: 0.1 + 0.1 * rand(),
          convention: "probable_error" as const,
        };
      }),
    };
  });
  return { instrument: "planted", solar_radius_arcmin: radius, plates };
}

describe("special functions", () => {
  it("derives the probable-error factor 1.4826 from the normal distribution", () => {
    expect(STANDARD_PER_PROBABLE).toBeCloseTo(1.482602218505602, 14);
    expect(normalQuantile(0.5)).toBeCloseTo(0, 15);
    expect(erf(0.5)).toBeCloseTo(0.5204998778130465, 15);
  });

  it("matches known values of log Gamma", () => {
    expect(logGamma(1)).toBeCloseTo(0, 12);
    expect(logGamma(2.5)).toBeCloseTo(Math.log(1.329340388179137), 12);
    expect(logGamma(0.5)).toBeCloseTo(0.5 * Math.log(Math.PI), 12);
  });
});

describe("fitter", () => {
  it("covers the sixteen predeclared fit scenarios", () => {
    expect(allScenarios()).toHaveLength(16);
  });

  it("recovers a planted alpha exactly from noise-free plates under every scenario", () => {
    fc.assert(
      fc.property(fc.double({ min: -1, max: 3, noNaN: true }), fc.integer({ min: 1, max: 1000 }), (alpha, seed) => {
        const data = planted(alpha, seed);
        for (const scenario of allScenarios()) {
          const fit = fitRows(buildRows(data, scenario), scenario.likelihood);
          expect(Math.abs(fit.alpha_arcsec - alpha)).toBeLessThan(1e-9);
        }
      }),
      { numRuns: 25 },
    );
  });

  it("converts probable errors only in the converted scenario", () => {
    const data = planted(1.75);
    const base: Scenario = { error_convention: "converted", plate_scale: "solved", error_correlation: "independent", likelihood: "gaussian" };
    const converted = fitRows(buildRows(data, base), "gaussian");
    const quoted = fitRows(buildRows(data, { ...base, error_convention: "probable_as_standard" }), "gaussian");
    expect(converted.sigma_alpha_arcsec / quoted.sigma_alpha_arcsec).toBeCloseTo(STANDARD_PER_PROBABLE, 10);
  });

  it("rejects a bundle it does not understand", () => {
    expect(() => fitBundle({ schema: "other", instrument_sets: [] })).toThrow("unsupported bundle schema");
  });
});

describe("independent implementation [tier:T8]", () => {
  it("[REL-007] agrees with the Python reference fitter on every instrument and scenario", () => {
    const bundle = load<Bundle>("bundle-seed7.json");
    const reference = load<{ fits: { instrument: string; scenario: Scenario; alpha_arcsec: number; sigma_alpha_arcsec: number; loglik: number }[] }>(
      "python-fits-seed7.json",
    );
    const mine = fitBundle(bundle);
    expect(mine).toHaveLength(reference.fits.length);
    for (const ref of reference.fits) {
      const match = mine.find((m) => m.instrument === ref.instrument && JSON.stringify(m.scenario) === JSON.stringify(ref.scenario));
      expect(match, `${ref.instrument} ${JSON.stringify(ref.scenario)}`).toBeDefined();
      expect(Math.abs((match?.alpha_arcsec ?? NaN) - ref.alpha_arcsec)).toBeLessThan(1e-6);
      expect(Math.abs((match?.sigma_alpha_arcsec ?? NaN) - ref.sigma_alpha_arcsec)).toBeLessThan(1e-6);
      expect(Math.abs((match?.loglik ?? NaN) - ref.loglik)).toBeLessThan(1e-6);
    }
  });
});

// The deflection fit, implemented from the UC-02 specification (§9 and docs/protocols/uc02-einstein-1919.md):
// per plate, dx = a_x + s x + r y + alpha g_x and dy = a_y + s y - r x + alpha g_y, where g is the
// response of a 1/b deflection at limb amplitude alpha. Scale and rotation are solved per plate or
// fixed from check plates (known linear terms subtracted, the rotation too when the plate's
// orientation is known, else solved), or the 1920 reduction's six constants per plate are solved (solved_affine:
// dx = a_x + s_xx x + s_xy y + alpha g_x, dy = a_y + s_yx x + s_yy y + alpha g_y); a constant no
// measurement touches (a plate measured in one coordinate only) is left out; errors are Gaussian or Student-t, independent or correlated within a plate
// through the check-plate scale uncertainty; probable errors are converted or used as quoted.
import { at, cholesky, column, forward, matrix, normal, set, solveSpd, type Matrix } from "./linalg.ts";
import { STANDARD_PER_PROBABLE, logGamma } from "./stats.ts";

export interface Star {
  star_id: string;
  x_arcmin: number;
  y_arcmin: number;
  dx_arcsec: number | null;
  dy_arcsec: number | null;
  error_arcsec: number;
  convention: "standard" | "probable_error";
}

export interface Plate {
  plate_id: string;
  check_scale?: number;
  /** null: the orientation is not known apart from the plate, so the fixed-scale fit solves it. */
  check_rotation?: number | null;
  /** Known linear terms in arcsec per arcmin, replacing check_scale: dx += xx x + xy y, dy += yx x + yy y. */
  check_terms?: { xx: number; xy: number; yx: number; yy: number };
  check_scale_sigma?: number;
  stars: Star[];
}

export interface InstrumentSet {
  instrument: string;
  solar_radius_arcmin: number;
  plates: Plate[];
}

export interface Bundle {
  schema: string;
  instrument_sets: InstrumentSet[];
}

export interface Scenario {
  error_convention: "probable_as_standard" | "converted";
  plate_scale: "solved" | "solved_affine" | "fixed_from_check_plates";
  error_correlation: "independent" | "per_plate";
  likelihood: "gaussian" | "student_t";
}

export const SCENARIO_VALUES = {
  error_convention: ["probable_as_standard", "converted"],
  plate_scale: ["solved", "solved_affine", "fixed_from_check_plates"],
  error_correlation: ["independent", "per_plate"],
  likelihood: ["gaussian", "student_t"],
} as const;

export function allScenarios(): Scenario[] {
  const out: Scenario[] = [];
  for (const error_convention of SCENARIO_VALUES.error_convention)
    for (const plate_scale of SCENARIO_VALUES.plate_scale)
      for (const error_correlation of SCENARIO_VALUES.error_correlation)
        for (const likelihood of SCENARIO_VALUES.likelihood) out.push({ error_convention, plate_scale, error_correlation, likelihood });
  return out;
}

export interface FitOutcome {
  alpha_arcsec: number;
  sigma_alpha_arcsec: number;
  loglik: number;
  iterations: number;
}

interface Rows {
  design: Matrix;
  y: Float64Array;
  cov: Matrix;
}

function sigmaOf(star: Star, scenario: Scenario): number {
  if (star.convention === "standard" || scenario.error_convention === "probable_as_standard") return star.error_arcsec;
  return star.error_arcsec * STANDARD_PER_PROBABLE;
}

export function buildRows(observations: InstrumentSet, scenario: Scenario): Rows {
  const model = scenario.plate_scale;
  const solved = model !== "fixed_from_check_plates";
  const perPlate = model === "solved" ? 4 : model === "solved_affine" ? 6 : 3;
  const all = perPlate * observations.plates.length + 1;
  type Row = { coeffs: Map<number, number>; y: number; variance: number; plate: number; along: number };
  const rows: Row[] = [];
  observations.plates.forEach((plate, p) => {
    const base = perPlate * p;
    for (const star of plate.stars) {
      const theta = Math.hypot(star.x_arcmin, star.y_arcmin);
      const ratio = observations.solar_radius_arcmin / theta;
      const sigma = sigmaOf(star, scenario);
      const axes: [number, number, number, number, number][] = [];
      // [measured, own coordinate, cross term, response, offset column]
      if (star.dx_arcsec !== null) axes.push([star.dx_arcsec, star.x_arcmin, star.y_arcmin, (ratio * star.x_arcmin) / theta, 0]);
      if (star.dy_arcsec !== null) axes.push([star.dy_arcsec, star.y_arcmin, -star.x_arcmin, (ratio * star.y_arcmin) / theta, 1]);
      for (const [measured, own, cross, response, offset] of axes) {
        const coeffs = new Map<number, number>([[base + offset, 1], [all - 1, response]]);
        let y = measured;
        if (model === "solved") {
          coeffs.set(base + 2, own);
          coeffs.set(base + 3, cross);
        } else if (model === "solved_affine") {
          coeffs.set(base + 2 + 2 * offset, star.x_arcmin);
          coeffs.set(base + 3 + 2 * offset, star.y_arcmin);
        } else {
          const scale = plate.check_scale ?? 0;
          const t = plate.check_terms ?? { xx: scale, xy: 0, yx: 0, yy: scale };
          y -= offset === 0 ? t.xx * star.x_arcmin + t.xy * star.y_arcmin : t.yx * star.x_arcmin + t.yy * star.y_arcmin;
          if (plate.check_rotation === null) coeffs.set(base + 2, cross);
          else y -= (plate.check_rotation ?? 0) * cross;
        }
        rows.push({ coeffs, y, variance: sigma * sigma, plate: p, along: own });
      }
    }
  });
  // Keep only the constants some measurement touches, and alpha last.
  const used = new Set<number>([all - 1]);
  for (const row of rows) for (const [j, v] of row.coeffs) if (v !== 0) used.add(j);
  const index = new Map<number, number>([...used].sort((a, b) => a - b).map((j, k) => [j, k]));
  const params = index.size;
  const n = rows.length;
  const design = matrix(n, params);
  const y = new Float64Array(n);
  const cov = matrix(n, n);
  rows.forEach((row, i) => {
    for (const [j, v] of row.coeffs) if (v !== 0) set(design, i, index.get(j) as number, v);
    y[i] = row.y;
    set(cov, i, i, row.variance);
  });
  if (scenario.error_correlation === "per_plate" && !solved) {
    for (let i = 0; i < n; i++)
      for (let j = 0; j < n; j++) {
        const ri = rows[i] as Row;
        const rj = rows[j] as Row;
        if (ri.plate !== rj.plate) continue;
        const tau = (observations.plates[ri.plate] as Plate).check_scale_sigma ?? 0;
        set(cov, i, j, at(cov, i, j) + tau * tau * ri.along * rj.along);
      }
  }
  return { design, y, cov };
}

const studentTLogDensity = (r: number, nu: number): number =>
  logGamma((nu + 1) / 2) - logGamma(nu / 2) - 0.5 * Math.log(nu * Math.PI) - ((nu + 1) / 2) * Math.log1p((r * r) / nu);

export function fitRows(rows: Rows, likelihood: Scenario["likelihood"], nu = 4): FitOutcome {
  const l = cholesky(rows.cov);
  let logDet = 0;
  for (let i = 0; i < l.rows; i++) logDet += Math.log(at(l, i, i));
  const a = forward(l, rows.design);
  const y = forward(l, column(rows.y)).data;
  const p = a.cols;
  const solve = (w?: Float64Array): { beta: Float64Array; info: Matrix } => {
    const { s, t } = normal(a, y, w);
    return { beta: solveSpd(s, t).data, info: s };
  };
  const residuals = (beta: Float64Array): Float64Array => {
    const r = new Float64Array(a.rows);
    for (let i = 0; i < a.rows; i++) {
      let fitted = 0;
      for (let j = 0; j < p; j++) fitted += at(a, i, j) * (beta[j] as number);
      r[i] = (y[i] as number) - fitted;
    }
    return r;
  };
  let { beta, info } = solve();
  let iterations = 0;
  if (likelihood === "student_t") {
    let converged = false;
    while (!converged) {
      iterations++;
      if (iterations > 500) throw new Error("IRLS did not converge in 500 iterations");
      const r = residuals(beta);
      const w = r.map((ri) => (nu + 1) / (nu + ri * ri));
      const next = solve(w);
      let change = 0;
      let size = 1;
      for (let j = 0; j < p; j++) {
        change = Math.max(change, Math.abs((next.beta[j] as number) - (beta[j] as number)));
        size = Math.max(size, Math.abs(next.beta[j] as number));
      }
      beta = next.beta;
      converged = change <= 1e-13 * size;
    }
    const r = residuals(beta);
    info = normal(a, y, r.map((ri) => (nu + 1) / (nu + ri * ri))).s;
  }
  const r = residuals(beta);
  const chi2 = r.reduce((acc, ri) => acc + ri * ri, 0);
  const loglik =
    likelihood === "gaussian"
      ? -0.5 * (chi2 + a.rows * Math.log(2 * Math.PI)) - logDet
      : r.reduce((acc, ri) => acc + studentTLogDensity(ri, nu), 0) - logDet;
  const unit = matrix(p, 1);
  unit.data[p - 1] = 1;
  const varianceAlpha = solveSpd(info, unit).data[p - 1] as number;
  return { alpha_arcsec: beta[p - 1] as number, sigma_alpha_arcsec: Math.sqrt(varianceAlpha), loglik, iterations };
}

export interface FitRecord extends FitOutcome {
  instrument: string;
  scenario: Scenario;
}

export function fitBundle(bundle: Bundle, nu = 4): FitRecord[] {
  if (bundle.schema !== "sdp-relativity/observations@1") throw new Error(`unsupported bundle schema ${bundle.schema}`);
  const out: FitRecord[] = [];
  for (const observations of bundle.instrument_sets)
    for (const scenario of allScenarios())
      out.push({ instrument: observations.instrument, scenario, ...fitRows(buildRows(observations, scenario), scenario.likelihood, nu) });
  return out;
}

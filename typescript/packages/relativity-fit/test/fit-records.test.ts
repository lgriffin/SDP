// The exact records of the fitter's parts: special-function values at known points and at their edges,
// the Cholesky factor and its refusal, the rows a plate becomes, and the Student-t fit's density,
// iteration count and non-convergence guard, so a loosened formula fails a test.
import { describe, expect, it } from "vitest";

import {
  buildRows,
  fitRows,
  type InstrumentSet,
  type Scenario,
} from "../src/fit.ts";
import {
  backward,
  cholesky,
  column,
  forward,
  matrix,
  set,
  type Matrix,
} from "../src/linalg.ts";
import {
  STANDARD_PER_PROBABLE,
  erf,
  logGamma,
  normalCdf,
  normalQuantile,
} from "../src/stats.ts";

const close = (actual: ArrayLike<number>, expected: number[], digits = 13) => {
  expect(actual.length).toBe(expected.length);
  expected.forEach((e, i) => expect(actual[i]).toBeCloseTo(e, digits));
};
const square = (rows: number[][]): Matrix => {
  const m = matrix(rows.length, rows.length);
  rows.forEach((row, i) => row.forEach((v, j) => set(m, i, j, v)));
  return m;
};

describe("special functions", () => {
  it.each([
    [0.5, 0.5204998778130465],
    [1, 0.8427007929497149],
    [-1.5, -0.9661051464753108],
    [1.99, 0.995111413199617],
    [1e-3, 0.0011283787909692365],
    [0, 0],
  ])("erf(%d) is %d", (z, value) => {
    expect(erf(z)).toBeCloseTo(value, 14);
  });

  it("the series erf refuses |z| of 2 or more, and only that", () => {
    for (const z of [2, -2, 2.5])
      expect(() => erf(z)).toThrow(
        new RangeError("series erf is used only for |z| < 2"),
      );
    expect(erf(1.999999)).toBeCloseTo(0.9953222443519261, 13);
  });

  it.each([
    [0.5, 0.6914624612740131],
    [-1, 0.15865525393145707],
    [0, 0.5],
  ])("normalCdf(%d) is %d", (x, value) => {
    expect(normalCdf(x)).toBeCloseTo(value, 14);
  });

  it.each([
    [0.75, 0.6744897501960817],
    [0.1, -1.2815515655446008],
    [0.9, 1.2815515655446008],
    [0.5, 0],
  ])("normalQuantile(%d) is %d", (p, value) => {
    expect(normalQuantile(p)).toBeCloseTo(value, 13);
  });

  it("a probable error is 0.6745 standard errors", () => {
    expect(STANDARD_PER_PROBABLE).toBeCloseTo(1 / 0.6744897501960817, 13);
  });

  it.each([
    [0.25, 1.2880225246980772],
    [0.1, 2.2527126517342055],
    [-0.5, 1.265512123484645],
    [3.5, 1.2009736023470738],
  ])("logGamma(%d) is %d, by reflection below one half", (x, value) => {
    expect(logGamma(x)).toBeCloseTo(value, 12);
  });
});

describe("linear algebra", () => {
  const a = square([
    [4, 2, 2],
    [2, 10, 7],
    [2, 7, 21],
  ]);

  it("factors a positive-definite matrix into its exact lower triangle", () => {
    const l = cholesky(a);
    expect([l.rows, l.cols]).toEqual([3, 3]);
    expect(Array.from(l.data)).toEqual([2, 0, 0, 1, 3, 0, 1, 2, 4]);
  });

  it("refuses a matrix that is not positive definite, naming the column", () => {
    expect(() => cholesky(square([[-1]]))).toThrow(
      new Error("matrix is not positive definite at column 0"),
    );
    expect(() => cholesky(square([[0]]))).toThrow(
      new Error("matrix is not positive definite at column 0"),
    );
    expect(() =>
      cholesky(
        square([
          [1, 1],
          [1, 1],
        ]),
      ),
    ).toThrow(new Error("matrix is not positive definite at column 1"));
  });

  it("solves each triangle column by column", () => {
    const l = cholesky(a);
    const b = matrix(3, 2);
    [2, 4, 1, 7, 3, 5].forEach((v, i) => (b.data[i] = v));
    // L x = b: x0 = b0 / 2, x1 = (b1 - x0) / 3, x2 = (b2 - x0 - 2 x1) / 4.
    const f = forward(l, b);
    close(f.data, [1, 2, 0, 5 / 3, 0.5, -1 / 12]);
    // L^T x = b: x2 = b2 / 4, x1 = (b1 - 2 x2) / 3, x0 = (b0 - x1 - x2) / 2.
    const g = backward(l, b);
    close(g.data, [17 / 24, 0.625, -1 / 6, 1.5, 0.75, 1.25]);
    close(
      backward(l, forward(l, column([1, 1, 1]))).data,
      solve3(a, [1, 1, 1]),
    );
  });
});

/** Gaussian elimination, independent of the Cholesky path, for a 3x3 check. */
function solve3(m: Matrix, rhs: number[]): number[] {
  const r = [0, 1, 2].map((i) =>
    [0, 1, 2].map((j) => m.data[i * 3 + j] as number).concat(rhs[i] as number),
  );
  for (let c = 0; c < 3; c++)
    for (let i = c + 1; i < 3; i++) {
      const f = (r[i] as number[])[c]! / (r[c] as number[])[c]!;
      for (let k = c; k < 4; k++)
        (r[i] as number[])[k]! -= f * (r[c] as number[])[k]!;
    }
  const x = [0, 0, 0];
  for (let i = 2; i >= 0; i--) {
    let s = (r[i] as number[])[3]!;
    for (let k = i + 1; k < 3; k++) s -= (r[i] as number[])[k]! * x[k]!;
    x[i] = s / (r[i] as number[])[i]!;
  }
  return x;
}

describe("[UC-02] the rows a plate becomes", () => {
  const K = STANDARD_PER_PROBABLE;
  const tau2 = 0.003 ** 2;
  const observations: InstrumentSet = {
    instrument: "I",
    solar_radius_arcmin: 16,
    plates: [
      {
        plate_id: "P",
        check_scale: 0.01,
        check_rotation: 0.02,
        check_scale_sigma: 0.003,
        stars: [
          {
            star_id: "A",
            x_arcmin: 3,
            y_arcmin: 4,
            dx_arcsec: 1,
            dy_arcsec: null,
            error_arcsec: 0.5,
            convention: "standard",
          },
          {
            star_id: "B",
            x_arcmin: 0,
            y_arcmin: 8,
            dx_arcsec: 2,
            dy_arcsec: 3,
            error_arcsec: 0.4,
            convention: "probable_error",
          },
          {
            star_id: "C",
            x_arcmin: 6,
            y_arcmin: 8,
            dx_arcsec: null,
            dy_arcsec: 1,
            error_arcsec: 0.2,
            convention: "standard",
          },
        ],
      },
    ],
  };
  const scenario: Scenario = {
    error_convention: "converted",
    plate_scale: "fixed_from_check_plates",
    error_correlation: "per_plate",
    likelihood: "gaussian",
  };

  it("a null axis gives no row, check-plate terms come off the measurement, and only probable errors are converted", () => {
    const { design, y, cov } = buildRows(observations, scenario);
    expect([design.rows, design.cols]).toEqual([4, 3]);
    // Columns: x offset, y offset, alpha. Response is (R / theta) * coordinate / theta.
    close(design.data, [1, 0, 1.92, 1, 0, 0, 0, 1, 2, 0, 1, 1.28]);
    close(y, [1 - 0.11, 2 - 0.16, 3 - 0.08, 1 - (0.08 - 0.12)]);
    // Variances on the diagonal, plus tau^2 times the along-axis coordinates 3, 0, 8, 8 within the plate.
    const along = [3, 0, 8, 8];
    const variance = [0.25, (0.4 * K) ** 2, (0.4 * K) ** 2, 0.04];
    close(
      cov.data,
      along.flatMap((ai, i) =>
        along.map(
          (aj, j) => (i === j ? (variance[i] as number) : 0) + tau2 * ai * aj,
        ),
      ),
    );
  });

  it("probable errors used as quoted are not converted", () => {
    const { cov } = buildRows(observations, {
      ...scenario,
      error_convention: "probable_as_standard",
      error_correlation: "independent",
    });
    close(
      [0, 5, 10, 15].map((i) => cov.data[i] as number),
      [0.25, 0.16, 0.16, 0.04],
    );
  });
});

describe("[UC-02] the Student-t fit", () => {
  const rows = (values: number[]) => {
    const n = values.length;
    const design = matrix(n, 1);
    design.data.fill(1);
    const cov = matrix(n, n);
    for (let i = 0; i < n; i++) set(cov, i, i, 1);
    return { design, y: Float64Array.from(values), cov };
  };
  // log of the nu = 4 density: Gamma(5/2) / Gamma(2) = (3/4) sqrt(pi).
  const density = (r: number) =>
    Math.log(0.75 * Math.sqrt(Math.PI)) -
    0.5 * Math.log(4 * Math.PI) -
    2.5 * Math.log1p((r * r) / 4);

  it("reports the Student-t log likelihood at the reweighted location, and the iterations it took", () => {
    const values = [0, 1, 5];
    const out = fitRows(rows(values), "student_t");
    const beta = out.alpha_arcsec;
    // The IRLS fixed point: the weighted residuals sum to zero.
    expect(
      values.reduce(
        (acc, v) => acc + (5 * (v - beta)) / (4 + (v - beta) ** 2),
        0,
      ),
    ).toBeCloseTo(0, 12);
    expect(out.loglik).toBeCloseTo(
      values.reduce((acc, v) => acc + density(v - beta), 0),
      12,
    );
    expect(out.iterations).toBe(28);
  });

  it("measures convergence against the size of the solution", () => {
    expect(fitRows(rows([0, 1e6, 5e6]), "student_t").iterations).toBe(6);
  });

  it("uses the normalising constant of the degrees of freedom given", () => {
    // nu = 3: Gamma(2) / Gamma(3/2) = 2 / sqrt(pi), so log Gamma(nu / 2) is not zero and its sign shows.
    const values = [0, 1, 5];
    const out = fitRows(rows(values), "student_t", 3);
    const nu3 = (r: number) =>
      Math.log(2 / Math.sqrt(Math.PI)) -
      0.5 * Math.log(3 * Math.PI) -
      2 * Math.log1p((r * r) / 3);
    expect(out.loglik).toBeCloseTo(
      values.reduce((acc, v) => acc + nu3(v - out.alpha_arcsec), 0),
      12,
    );
  });

  it("a Gaussian fit takes no iterations", () => {
    expect(fitRows(rows([0, 1, 5]), "gaussian").iterations).toBe(0);
  });
});

// Dense linear algebra for small systems: row-major Float64Array matrices, Cholesky factorisation,
// triangular solves. Enough for a few hundred rows and a few dozen parameters.

export interface Matrix {
  rows: number;
  cols: number;
  data: Float64Array;
}

export const matrix = (rows: number, cols: number): Matrix => ({ rows, cols, data: new Float64Array(rows * cols) });

export const at = (m: Matrix, i: number, j: number): number => m.data[i * m.cols + j] as number;

export function set(m: Matrix, i: number, j: number, value: number): void {
  m.data[i * m.cols + j] = value;
}

/** Lower-triangular L with A = L L^T; throws if A is not positive definite. */
export function cholesky(a: Matrix): Matrix {
  const n = a.rows;
  const l = matrix(n, n);
  for (let j = 0; j < n; j++) {
    let diag = at(a, j, j);
    for (let k = 0; k < j; k++) diag -= at(l, j, k) ** 2;
    if (!(diag > 0)) throw new Error(`matrix is not positive definite at column ${j}`);
    const ljj = Math.sqrt(diag);
    set(l, j, j, ljj);
    for (let i = j + 1; i < n; i++) {
      let s = at(a, i, j);
      for (let k = 0; k < j; k++) s -= at(l, i, k) * at(l, j, k);
      set(l, i, j, s / ljj);
    }
  }
  return l;
}

/** Solve L x = b for lower-triangular L, column by column of B. */
export function forward(l: Matrix, b: Matrix): Matrix {
  const out = matrix(b.rows, b.cols);
  for (let c = 0; c < b.cols; c++) {
    for (let i = 0; i < l.rows; i++) {
      let s = at(b, i, c);
      for (let k = 0; k < i; k++) s -= at(l, i, k) * at(out, k, c);
      set(out, i, c, s / at(l, i, i));
    }
  }
  return out;
}

/** Solve L^T x = b for lower-triangular L. */
export function backward(l: Matrix, b: Matrix): Matrix {
  const n = l.rows;
  const out = matrix(b.rows, b.cols);
  for (let c = 0; c < b.cols; c++) {
    for (let i = n - 1; i >= 0; i--) {
      let s = at(b, i, c);
      for (let k = i + 1; k < n; k++) s -= at(l, k, i) * at(out, k, c);
      set(out, i, c, s / at(l, i, i));
    }
  }
  return out;
}

/** Solve the symmetric positive-definite system S x = b. */
export function solveSpd(s: Matrix, b: Matrix): Matrix {
  const l = cholesky(s);
  return backward(l, forward(l, b));
}

/** A^T diag(w) A, and A^T diag(w) y, for weights w (all ones when omitted). */
export function normal(a: Matrix, y: Float64Array, w?: Float64Array): { s: Matrix; t: Matrix } {
  const p = a.cols;
  const s = matrix(p, p);
  const t = matrix(p, 1);
  for (let i = 0; i < a.rows; i++) {
    const wi = w ? (w[i] as number) : 1;
    for (let j = 0; j < p; j++) {
      const aij = at(a, i, j);
      if (aij === 0) continue;
      t.data[j] = (t.data[j] as number) + wi * aij * (y[i] as number);
      for (let k = 0; k < p; k++) s.data[j * p + k] = (s.data[j * p + k] as number) + wi * aij * at(a, i, k);
    }
  }
  return { s, t };
}

export function column(values: ArrayLike<number>): Matrix {
  const m = matrix(values.length, 1);
  m.data.set(Array.from(values));
  return m;
}

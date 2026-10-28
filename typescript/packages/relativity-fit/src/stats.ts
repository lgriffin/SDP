// The special functions the fitter needs, derived here rather than borrowed.

/** erf by its Maclaurin series: accurate to double precision for |z| < 2. */
export function erf(z: number): number {
  if (Math.abs(z) >= 2) throw new RangeError("series erf is used only for |z| < 2");
  let term = z;
  let sum = z;
  for (let n = 1; n < 200; n++) {
    term *= (-z * z) / n;
    const next = term / (2 * n + 1);
    sum += next;
    if (Math.abs(next) < 1e-18 * Math.abs(sum)) break;
  }
  return (2 / Math.sqrt(Math.PI)) * sum;
}

export const normalCdf = (x: number): number => 0.5 * (1 + erf(x / Math.SQRT2));

/** Phi^-1(p) by Newton's method on the series CDF, for p in (0.05, 0.95). */
export function normalQuantile(p: number): number {
  let x = 0;
  for (let i = 0; i < 100; i++) {
    const density = Math.exp((-x * x) / 2) / Math.sqrt(2 * Math.PI);
    const step = (normalCdf(x) - p) / density;
    x -= step;
    if (Math.abs(step) < 1e-16) break;
  }
  return x;
}

/** Standard errors per probable error: the probable error is the 75th-percentile deviation. */
export const STANDARD_PER_PROBABLE = 1 / normalQuantile(0.75);

/** log Gamma by the Lanczos approximation (g = 7, n = 9). */
export function logGamma(x: number): number {
  const c = [
    0.99999999999980993, 676.5203681218851, -1259.1392167224028, 771.32342877765313, -176.61502916214059,
    12.507343278686905, -0.13857109526572012, 9.9843695780195716e-6, 1.5056327351493116e-7,
  ];
  if (x < 0.5) return Math.log(Math.PI / Math.abs(Math.sin(Math.PI * x))) - logGamma(1 - x);
  const xm = x - 1;
  let a = c[0] as number;
  const t = xm + 7.5;
  for (let i = 1; i < 9; i++) a += (c[i] as number) / (xm + i);
  return 0.5 * Math.log(2 * Math.PI) + (xm + 0.5) * Math.log(t) - t + Math.log(a);
}

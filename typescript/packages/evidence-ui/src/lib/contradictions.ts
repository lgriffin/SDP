// What the cross-survey-contradiction test found for one star, in words and one row per comparison
// (INV-029): which survey or image measured what, what Gaia predicted, by how much they differ and
// against what expected scatter. Pure functions, unit-tested without a browser.
import { formatNumber, type TestResultDoc } from "./view.ts";

export const CONTRADICTION_TEST = "cross-survey-contradiction";

/** One comparison as the test writes it into its result (INV-026). */
export interface ComparisonDoc {
  channel: string;
  survey: string;
  kind: "position" | "band";
  counted: boolean;
  set_aside: string | null;
  epoch_gap_years: number | null;
  reported_sigma: number | null;
  calibration_sigma: number | null;
  total_sigma: number | null;
  z: number | null;
  unit: "mas" | "mag";
  offset_mas?: (number | null)[];
  field_offset_mas?: (number | null)[];
  residual_mas?: number | null;
  observed_mag?: number | null;
  predicted_mag?: number | null;
  field_offset_mag?: number | null;
  residual_mag?: number | null;
}

export interface ContradictionDetails {
  worst_channel: string | null;
  comparisons_counted: number;
  comparisons_set_aside: number;
  flagged: string[];
  pattern: string;
  pattern_reading: string;
  comparisons: ComparisonDoc[];
  images_left_out?: Record<string, string>;
}

/** One line of the table the candidate and compare pages show. */
export interface ComparisonRow {
  channel: string;
  label: string;
  when: string;
  measured: string;
  difference: string;
  expected: string;
  z: string;
  verdict: "disagrees" | "agrees" | "set aside";
  why: string;
}

const SURVEY_NAMES: Record<string, string> = { "2mass": "2MASS", ps1: "Pan-STARRS", allwise: "AllWISE" };

/** The cross-survey-contradiction result among a star's results, or null when the test did not run. */
export function contradictionResult(results: readonly TestResultDoc[] | null | undefined): TestResultDoc | null {
  return results?.find((r) => r.test_name === CONTRADICTION_TEST) ?? null;
}

/** A star's test results: the dossier's when it has one, else the investigation's own (both pages read them so). */
export function starResults(dossier: unknown, testResults: unknown): TestResultDoc[] {
  const fromDossier = (dossier as { test_results?: TestResultDoc[] } | null | undefined)?.test_results;
  return fromDossier ?? ((testResults as TestResultDoc[] | null | undefined) ?? []);
}

export function details(result: TestResultDoc): ContradictionDetails {
  return result.details as unknown as ContradictionDetails;
}

/** "2mass:Ks" is 2MASS Ks; "image:<product>:F200W" is the F200W image <product>; positions say so. */
export function channelLabel(channel: string, survey = ""): string {
  if (channel.startsWith("image:")) {
    const rest = channel.slice("image:".length);
    const cut = rest.lastIndexOf(":");
    const product = rest.slice(0, cut);
    const what = rest.slice(cut + 1);
    const camera = survey.startsWith("image:") ? survey.slice("image:".length) : "image";
    return what === "position" ? `${camera} position on ${product}` : `${camera} ${what} on ${product}`;
  }
  const [id, what] = channel.split(":", 2);
  const name = SURVEY_NAMES[id ?? ""] ?? id ?? channel;
  return what === "position" ? `${name} position` : `${name} ${what ?? ""}`.trim();
}

const signed = (v: number, digits: number) => `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(digits)}`;
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

/** The offset from the field's own (the frame tie, the zero point), which the test removes first. */
export function positionResidual(c: ComparisonDoc): [number, number] | null {
  const o = c.offset_mas;
  const f = c.field_offset_mas;
  if (!o || o.length !== 2 || num(o[0]) === null || num(o[1]) === null) return null;
  const f0 = num(f?.[0]) ?? 0;
  const f1 = num(f?.[1]) ?? 0;
  return [(o[0] as number) - f0, (o[1] as number) - f1];
}

/** East and north in words, for a residual of this size. */
export function direction(east: number, north: number): string {
  const parts: string[] = [];
  if (Math.abs(north) >= 0.4 * Math.hypot(east, north)) parts.push(north > 0 ? "north" : "south");
  if (Math.abs(east) >= 0.4 * Math.hypot(east, north)) parts.push(east > 0 ? "east" : "west");
  return parts.join("-");
}

function when(c: ComparisonDoc): string {
  const gap = num(c.epoch_gap_years);
  if (gap === null) return "—";
  const year = 2016 + gap;
  return Math.abs(gap) < 0.05 ? "Gaia's epoch" : `${year.toFixed(1)} (${formatNumber(Number(Math.abs(gap).toFixed(1)))} yr ${gap < 0 ? "before" : "after"} Gaia)`;
}

/** One row per comparison: the flagged ones first, then the rest counted, then those set aside. */
export function comparisonRows(d: ContradictionDetails): ComparisonRow[] {
  const flagged = new Set(d.flagged);
  const rank = (c: ComparisonDoc) => (flagged.has(c.channel) ? 0 : c.counted ? 1 : 2);
  return [...d.comparisons]
    .sort((a, b) => rank(a) - rank(b) || Math.abs(num(b.z) ?? 0) - Math.abs(num(a.z) ?? 0) || a.channel.localeCompare(b.channel))
    .map((c) => {
      const z = num(c.z);
      const total = num(c.total_sigma);
      // the engine's own flag decides, at the profile's flag_z, never a threshold of the page's
      const verdict = !c.counted ? "set aside" : flagged.has(c.channel) ? "disagrees" : "agrees";
      let measured = "—";
      let difference = "—";
      let why = "";
      if (c.kind === "band") {
        const obs = num(c.observed_mag);
        const pred = num(c.predicted_mag);
        const off = num(c.field_offset_mag) ?? 0;
        const res = num(c.residual_mag);
        if (obs !== null) measured = c.channel.startsWith("image:") ? `${obs.toFixed(2)} (instrumental)` : `${obs.toFixed(2)} mag`;
        if (pred !== null) measured += `, Gaia predicts ${(pred + off).toFixed(2)}`;
        if (res !== null) {
          difference = `${signed(res, 2)} mag`;
          why = res < 0 ? "brighter than Gaia's colour predicts" : "fainter than Gaia's colour predicts";
        }
      } else {
        const r = positionResidual(c);
        if (r !== null) {
          const size = Math.hypot(r[0], r[1]);
          measured = `${formatNumber(Number(size.toFixed(size < 10 ? 1 : 0)))} mas from where Gaia's motion puts it`;
          difference = `${formatNumber(Number(size.toFixed(size < 10 ? 1 : 0)))} mas ${direction(r[0], r[1])}`.trim();
          why = "out of place";
        }
      }
      const expected =
        total === null
          ? "—"
          : `${formatNumber(Number(total.toPrecision(2)))} ${c.unit}` +
            (num(c.reported_sigma) && num(c.calibration_sigma)
              ? ` (${formatNumber(Number((c.reported_sigma as number).toPrecision(2)))} reported, ${formatNumber(Number((c.calibration_sigma as number).toPrecision(2)))} from the field)`
              : num(c.reported_sigma)
                ? " (reported)"
                : " (from the field)");
      return {
        channel: c.channel,
        label: channelLabel(c.channel, c.survey),
        when: when(c),
        measured,
        difference,
        expected,
        z: z === null ? "—" : `${signed(z, 1)}σ`,
        verdict,
        why: verdict === "set aside" ? (c.set_aside ?? "set aside") : verdict === "disagrees" ? why : "",
      };
    });
}

/** The result in one or two sentences, for the top of the section. */
export function summary(d: ContradictionDetails): string {
  if (d.pattern === "not-assessable") {
    return `Nothing could be compared: all ${d.comparisons_set_aside} comparison${d.comparisons_set_aside === 1 ? " was" : "s were"} set aside (${d.pattern_reading}).`;
  }
  const counted = `${d.comparisons_counted} comparison${d.comparisons_counted === 1 ? "" : "s"}`;
  if (!d.flagged.length) {
    return `Every survey agrees with Gaia within the expected scatter (${counted}).`;
  }
  const worst = d.comparisons.find((c) => c.channel === d.worst_channel) ?? d.comparisons.find((c) => d.flagged.includes(c.channel));
  const names = d.flagged.map((ch) => channelLabel(ch, d.comparisons.find((c) => c.channel === ch)?.survey ?? ""));
  const lead = worst ? comparisonRows({ ...d, comparisons: [worst] })[0] : undefined;
  const head =
    lead && worst
      ? `${lead.label} is ${lead.difference.replace(/^[+−]/, "")} ${lead.why}, a ${Math.abs(num(worst.z) ?? 0).toFixed(1)}σ disagreement.`
      : `${names.join(", ")} disagree with Gaia.`;
  const others = names.length > 1 ? ` ${names.length} comparisons disagree: ${names.join(", ")}.` : "";
  return `${head}${others} Reading: ${d.pattern_reading}.`;
}

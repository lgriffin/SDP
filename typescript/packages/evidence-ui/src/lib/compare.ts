// The side-by-side comparison of a candidate with reference catalogues (INV-023): which quantities to
// line up, where a catalogue disagrees with what the search found, and what the pulls say in plain words.
// Pure functions, unit-tested without a browser.
import type { ReferenceCatalogueOption, ReferencePull, ReferenceRow } from "./ports.ts";
import { formatNumber } from "./view.ts";

export type Row = Record<string, unknown>;

/** One line of the comparison table: a quantity every catalogue may report under the same canonical name. */
export interface Quantity {
  key: string;
  label: string;
  unit?: string;
  /** How a reference value is checked against the candidate's, when both have one. */
  check?: "mag" | "parallax" | "motion";
  /** Shown in the table's details: what this quantity tells a scientist. */
  hint?: string;
}

export const QUANTITIES: Quantity[] = [
  { key: "separation_arcsec", label: "Distance from the candidate", unit: "″", hint: "Where the candidate was at the catalogue's epoch, moved along its proper motion." },
  { key: "main_id", label: "Known as" },
  { key: "name", label: "Variable star name" },
  { key: "object_type", label: "Object type", hint: "SIMBAD's type: * star, V* variable, ** double, Cl* cluster, G galaxy, QSO quasar." },
  { key: "variable_type", label: "Variability type" },
  { key: "references", label: "Papers that mention it" },
  { key: "epoch_jyear", label: "Epoch of the position", unit: "yr" },
  { key: "parallax_mas", label: "Parallax", unit: "mas", check: "parallax" },
  { key: "pm_ra_cosdec_masyr", label: "Proper motion, RA", unit: "mas/yr", check: "motion" },
  { key: "pm_dec_masyr", label: "Proper motion, Dec", unit: "mas/yr", check: "motion" },
  { key: "ruwe", label: "Gaia RUWE", hint: "Above 1.4 the single-star fit is poor: a binary, a blend or a bad measurement." },
  { key: "g_mag", label: "Gaia G", unit: "mag", check: "mag" },
  { key: "bp_rp", label: "Gaia BP − RP colour", unit: "mag", check: "mag" },
  { key: "ps_g_mag", label: "Pan-STARRS g", unit: "mag", check: "mag" },
  { key: "ps_r_mag", label: "Pan-STARRS r", unit: "mag", check: "mag" },
  { key: "ps_i_mag", label: "Pan-STARRS i", unit: "mag", check: "mag" },
  { key: "ps_z_mag", label: "Pan-STARRS z", unit: "mag", check: "mag" },
  { key: "ps_y_mag", label: "Pan-STARRS y", unit: "mag", check: "mag" },
  { key: "j_mag", label: "2MASS J", unit: "mag", check: "mag" },
  { key: "h_mag", label: "2MASS H", unit: "mag", check: "mag" },
  { key: "k_mag", label: "2MASS Ks", unit: "mag", check: "mag" },
  { key: "w1_mag", label: "WISE W1", unit: "mag", check: "mag" },
  { key: "w2_mag", label: "WISE W2", unit: "mag", check: "mag" },
  { key: "w3_mag", label: "WISE W3", unit: "mag", check: "mag" },
  { key: "w4_mag", label: "WISE W4", unit: "mag", check: "mag" },
  { key: "period_d", label: "Period", unit: "d" },
  { key: "mag_max", label: "Brightest", unit: "mag" },
  { key: "mag_min", label: "Faintest", unit: "mag" },
  { key: "spectral_type", label: "Spectral type" },
  { key: "quality", label: "Quality flags", hint: "2MASS ph_qual (A is best) or AllWISE cc_flags (0 is clean)." },
];

/** A position closer than this is the same object at these catalogues' precision. */
export const SAME_OBJECT_ARCSEC = 1.0;
/** Brightness in the same band differing by more than this needs a reason: variability, a blend, or another object. */
export const MAG_TOLERANCE = 0.3;

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

/** What the search found for the candidate, as one row in canonical names: its position and every
 * catalogue row its dossier holds, first catalogue first (so Gaia's parallax wins over another's). */
export function foundRow(dossier: unknown, candidateKey: string): Row | null {
  const objects = (dossier as { objects?: { object: { object_key: string; sky_icrs: [number, number] }; catalogue_rows: Record<string, Row[]> }[] } | null)?.objects;
  const mine = objects?.find((o) => o.object.object_key === candidateKey);
  if (!mine) return null;
  const row: Row = { ra_deg: mine.object.sky_icrs[0], dec_deg: mine.object.sky_icrs[1], source_key: candidateKey };
  for (const source of Object.keys(mine.catalogue_rows).sort()) {
    for (const r of mine.catalogue_rows[source] ?? []) {
      for (const [k, v] of Object.entries(r)) if (!(k in row) && v !== null && v !== undefined) row[k] = v;
    }
  }
  return row;
}

/** The latest pull of each catalogue, in the order the server lists its catalogues, then any others. */
export function latestPulls(pulls: readonly ReferencePull[], catalogues: readonly ReferenceCatalogueOption[] = []): ReferencePull[] {
  const latest = new Map<string, ReferencePull>();
  for (const p of pulls) latest.set(p.source_id, p);
  const order = catalogues.map((c) => c.sourceId);
  const rank = (id: string) => (order.includes(id) ? order.indexOf(id) : order.length);
  return [...latest.values()].sort((a, b) => rank(a.source_id) - rank(b.source_id) || a.source_id.localeCompare(b.source_id));
}

export interface Cell {
  text: string;
  /** Why this value disagrees with the candidate's, in words; null when it agrees or cannot be compared. */
  differs: string | null;
}

export function formatValue(q: Quantity, value: unknown, err?: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value !== "number") return String(value);
  let text: string;
  if (q.key === "separation_arcsec") text = value < 10 ? value.toFixed(2) : value.toFixed(1);
  else if (q.key === "epoch_jyear") text = value.toFixed(1);
  else if (q.check === "mag" || q.key === "mag_max" || q.key === "mag_min") text = value.toFixed(2);
  else text = formatNumber(value);
  const e = num(err);
  if (e !== null) text += ` ± ${formatNumber(e)}`;
  if (q.key === "separation_arcsec") return `${text}″`;
  return q.unit && q.key !== "epoch_jyear" ? `${text} ${q.unit}` : text;
}

/** One reference value against the candidate's: the text to show and, when it disagrees, why. */
export function compareCell(q: Quantity, found: Row | null, ref: Row | null): Cell {
  if (!ref) return { text: "—", differs: null };
  const value = ref[q.key];
  const text = formatValue(q, value, q.key === "parallax_mas" ? ref.parallax_mas_err : undefined);
  if (q.key === "separation_arcsec") {
    const sep = num(value);
    return { text, differs: sep !== null && sep > SAME_OBJECT_ARCSEC ? `more than ${SAME_OBJECT_ARCSEC}″ away: maybe another object` : null };
  }
  const mine = found ? num(found[q.key]) : null;
  const theirs = num(value);
  if (mine === null || theirs === null || !q.check) return { text, differs: null };
  const delta = theirs - mine;
  if (q.check === "mag") {
    return { text, differs: Math.abs(delta) > MAG_TOLERANCE ? `${delta > 0 ? "fainter" : "brighter"} by ${Math.abs(delta).toFixed(2)} mag` : null };
  }
  if (q.check === "parallax") {
    const e1 = num(found?.parallax_mas_err);
    const e2 = num(ref.parallax_mas_err);
    const sigma = e1 !== null && e2 !== null ? Math.hypot(e1, e2) : null;
    const limit = sigma !== null ? 3 * sigma : Math.max(0.5, 0.2 * Math.abs(mine));
    const how = sigma !== null ? `${(Math.abs(delta) / sigma).toFixed(1)}σ` : `${formatNumber(Math.abs(delta))} mas`;
    return { text, differs: Math.abs(delta) > limit ? `differs by ${how}` : null };
  }
  const limit = Math.max(3, 0.2 * Math.abs(mine));
  return { text, differs: Math.abs(delta) > limit ? `differs by ${formatNumber(Math.abs(delta))} mas/yr` : null };
}

/** The quantities at least one column reports, in table order. */
export function shownQuantities(found: Row | null, refs: readonly (Row | null)[]): Quantity[] {
  const rows = [found, ...refs].filter((r): r is Row => r !== null);
  return QUANTITIES.filter((q) => q.key === "separation_arcsec" ? refs.some((r) => r !== null) : rows.some((r) => r[q.key] !== undefined && r[q.key] !== null && r[q.key] !== ""));
}

/** Is this reference row the candidate itself (the same Gaia source)? */
export function isCandidate(row: ReferenceRow | null, candidateKey: string): boolean {
  return !!row && row.source_key === candidateKey;
}

export interface Finding {
  tone: "good" | "warn" | "bad" | "neutral";
  text: string;
}

/** What the latest pulls say, in plain sentences, most decisive first. */
export function findings(pulls: readonly ReferencePull[], found: Row | null, candidateKey: string): Finding[] {
  const out: Finding[] = [];
  for (const p of pulls) {
    const best = p.matches[0] ?? null;
    if (p.status === "failed") {
      out.push({ tone: "neutral", text: `${p.title} did not answer, so it says nothing either way. Try again later.` });
      continue;
    }
    if (!best) {
      out.push({ tone: "good", text: `${p.title} has nothing within ${formatNumber(p.radius_arcsec)}″.` });
      continue;
    }
    const sep = num(best.separation_arcsec) ?? 0;
    const near = `${sep.toFixed(2)}″ away`;
    if (p.source_id === "simbad") {
      const refs = num(best.references);
      const papers = refs !== null ? `, mentioned in ${refs} paper${refs === 1 ? "" : "s"}` : "";
      out.push({ tone: "warn", text: `SIMBAD already knows ${String(best.main_id ?? "an object")} ${near} (type ${String(best.object_type ?? "unknown")}${papers}). Check its literature before calling this new.` });
    } else if (p.source_id === "vsx") {
      out.push({ tone: "warn", text: `AAVSO VSX lists ${String(best.name ?? "a variable star")} ${near} (${String(best.variable_type ?? "type unknown")}). A variable here is already catalogued.` });
    } else if (isCandidate(best, candidateKey)) {
      out.push({ tone: "neutral", text: `${p.title}'s nearest source is the candidate itself.` });
    } else {
      const disagreements = QUANTITIES.map((q) => compareCell(q, found, best)).filter((c) => c.differs).length;
      const tone = sep > SAME_OBJECT_ARCSEC || disagreements ? "warn" : "neutral";
      const tail = disagreements ? `, and ${disagreements} quantit${disagreements === 1 ? "y differs" : "ies differ"} from what the search found` : "";
      out.push({ tone, text: `${p.title} has a source ${near}${tail}.` });
    }
    if (p.matches.length > 1) {
      out.push({ tone: "neutral", text: `${p.title} has ${p.matches.length} sources within ${formatNumber(p.radius_arcsec)}″: a crowded spot, so a blend is possible.` });
    }
  }
  const tones = { warn: 0, bad: 0, good: 1, neutral: 2 };
  return out.sort((a, b) => tones[a.tone] - tones[b.tone]);
}

/** Catalogues not pulled yet, which the form ticks for you. */
export function notYetPulled(catalogues: readonly ReferenceCatalogueOption[], pulls: readonly ReferencePull[]): string[] {
  const asked = new Set(pulls.map((p) => p.source_id));
  return catalogues.map((c) => c.sourceId).filter((id) => !asked.has(id));
}

export const RADII = [2, 5, 10, 30, 60, 300] as const;

// What the demonstration server answers when a scientist pulls references (INV-024). Every row is
// derived from the synthetic candidate's own synthetic catalogue rows by fixed offsets, so a browser
// test sees the same comparison each time. Nothing here stands for a real catalogue's contents.
import { foundRow } from "../compare.ts";
import type { Investigation, ReferenceCatalogueOption, ReferencePull, ReferenceRow } from "../ports.ts";

/** The catalogues sdp-api offers, with the same titles and descriptions (sdp.adapters.archives.references). */
export const DEMO_CATALOGUES: ReferenceCatalogueOption[] = [
  { sourceId: "2mass", title: "2MASS", releaseLabel: "2mass:psc", citation: "Skrutskie et al. 2006, AJ 131, 1163", description: "Near-infrared J, H and Ks brightness, measured about 1998-2001." },
  { sourceId: "allwise", title: "AllWISE", releaseLabel: "irsa:allwise", citation: "Cutri et al. 2013, AllWISE Explanatory Supplement", description: "Mid-infrared W1 to W4 brightness and contamination flags, observed 2010-2011." },
  { sourceId: "gaia", title: "Gaia DR3", releaseLabel: "gaia:DR3", citation: "Gaia Collaboration, Vallenari et al. 2023, A&A 674, A1", description: "Position, parallax, proper motion and brightness of every Gaia source near the candidate." },
  { sourceId: "ps1", title: "Pan-STARRS DR2", releaseLabel: "ps1:DR2", citation: "Chambers et al. 2016, arXiv:1612.05560; Flewelling et al. 2020, ApJS 251, 7", description: "Optical g, r, i, z and y brightness from MAST, deeper than Gaia, north of Dec -30." },
  { sourceId: "simbad", title: "SIMBAD", releaseLabel: "simbad:live", citation: "Wenger et al. 2000, A&AS 143, 9", description: "Whether the object is already known, what type it is and how many papers mention it." },
  { sourceId: "vsx", title: "AAVSO VSX", releaseLabel: "aavso:vsx", citation: "Watson, Henden & Price 2006, SASS 25, 47 (VizieR B/vsx)", description: "Whether a variable star is already catalogued here, with its type, period and range." },
];

const n = (v: unknown, fallback = 0): number => (typeof v === "number" && Number.isFinite(v) ? v : fallback);

function rowsFor(sourceId: string, found: Record<string, unknown>, key: string): ReferenceRow[] {
  const g = n(found.g_mag, 14);
  const k = n(found.k_mag, g - 1.5);
  const j = n(found.j_mag, k + 0.6);
  switch (sourceId) {
    case "gaia":
      return [
        { ...found, source_key: key, separation_arcsec: 0 },
        { source_key: `${key}-neighbour`, separation_arcsec: 3.7, g_mag: g + 2.1, bp_rp: n(found.bp_rp) + 0.4, parallax_mas: 0.4, parallax_mas_err: 0.2, pm_ra_cosdec_masyr: -1.5, pm_dec_masyr: -2.0, epoch_jyear: 2016 },
      ];
    case "2mass":
      return [{ source_key: "2mass:demo-1", separation_arcsec: 0.12, epoch_jyear: 2000.4, j_mag: j, h_mag: (j + k) / 2, k_mag: k, quality: "AAA" }];
    case "allwise":
      return [{ source_key: "allwise:demo-1", separation_arcsec: 0.31, epoch_jyear: 2010.5, w1_mag: k - 0.05, w2_mag: k - 0.02, w3_mag: k - 0.1, quality: "0000" }];
    case "ps1":
      return [{ source_key: "ps1:demo-1", separation_arcsec: 0.05, epoch_jyear: 2012.1, ps_g_mag: g + 0.45, ps_r_mag: g - 0.05, ps_i_mag: g - 0.3, detections: 41 }];
    default:
      return []; // SIMBAD and VSX know nothing at a synthetic position
  }
}

export function demoPull(sourceId: string, investigation: Investigation, radiusArcsec: number, actor: string, at: string): ReferencePull {
  const catalogue = DEMO_CATALOGUES.find((c) => c.sourceId === sourceId);
  if (!catalogue) throw new Error(`unknown catalogue ${sourceId}`);
  const found = foundRow(investigation.dossier, investigation.candidateKey);
  const base = {
    source_id: sourceId,
    title: catalogue.title,
    release_label: catalogue.releaseLabel,
    citation: catalogue.citation,
    radius_arcsec: radiusArcsec,
    target: {
      ra_deg: n(found?.ra_deg),
      dec_deg: n(found?.dec_deg),
      epoch_jyear: found ? n(found.epoch_jyear, 2016) : null,
      pm_ra_cosdec_masyr: (found?.pm_ra_cosdec_masyr as number | undefined) ?? null,
      pm_dec_masyr: (found?.pm_dec_masyr as number | undefined) ?? null,
    },
    asked_at: at,
    actor,
    raw_artifact_id: null,
  };
  if (!found) return { ...base, status: "failed", matches: [], error: "InvariantViolation: this demonstration candidate has no dossier, so no position" };
  const ra = n(found.ra_deg);
  const dec = n(found.dec_deg);
  // Each demo row sits due north of the candidate by its separation, so position and distance agree.
  const matches = rowsFor(sourceId, found, investigation.candidateKey)
    .filter((r) => n(r.separation_arcsec) <= radiusArcsec)
    .map((r) => ({ ra_deg: ra, dec_deg: dec + n(r.separation_arcsec) / 3600, ...r }));
  return { ...base, status: matches.length ? "found" : "none", matches, error: null };
}

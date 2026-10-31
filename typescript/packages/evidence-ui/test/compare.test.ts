// The comparison page's logic (INV-023): what lines up, what counts as a disagreement, and what the
// pulls say in words.
import { describe, expect, it } from "vitest";

import { compareCell, findings, formatValue, foundRow, latestPulls, notYetPulled, QUANTITIES, shownQuantities } from "../src/lib/compare.ts";
import type { ReferencePull } from "../src/lib/ports.ts";
import { FIXTURES } from "../src/lib/testing/fixtures.ts";
import { DEMO_CATALOGUES } from "../src/lib/testing/references.ts";

const q = (key: string) => QUANTITIES.find((x) => x.key === key)!;

function pull(source_id: string, status: ReferencePull["status"], matches: ReferencePull["matches"] = [], extra: Partial<ReferencePull> = {}): ReferencePull {
  return {
    source_id,
    title: DEMO_CATALOGUES.find((c) => c.sourceId === source_id)?.title ?? source_id,
    release_label: "x:1",
    citation: "c",
    radius_arcsec: 5,
    target: { ra_deg: 1, dec_deg: 2, epoch_jyear: 2016, pm_ra_cosdec_masyr: null, pm_dec_masyr: null },
    asked_at: "2026-10-31T10:00:00Z",
    actor: "ada",
    status,
    matches,
    raw_artifact_id: null,
    error: status === "failed" ? "TimeoutError: no answer" : null,
    ...extra,
  };
}

describe("comparison [INV-023]", () => {
  const inv = FIXTURES.investigation["inv-000001"]!;

  it("[INV-023] merges the candidate's own catalogue rows into one row", () => {
    const found = foundRow(inv.dossier, inv.candidateKey);
    expect(found?.source_key).toBe(inv.candidateKey);
    expect(typeof found?.g_mag).toBe("number");
    expect(typeof found?.j_mag).toBe("number");
    expect(foundRow(inv.dossier, "nobody")).toBeNull();
    expect(foundRow(null, inv.candidateKey)).toBeNull();
  });

  it("[INV-023] says in words where a catalogue disagrees", () => {
    const found = { g_mag: 12, parallax_mas: 4, parallax_mas_err: 0.05, pm_ra_cosdec_masyr: 20 };
    expect(compareCell(q("g_mag"), found, { g_mag: 12.1 }).differs).toBeNull();
    expect(compareCell(q("g_mag"), found, { g_mag: 12.5 }).differs).toBe("fainter by 0.50 mag");
    expect(compareCell(q("g_mag"), found, { g_mag: 11.4 }).differs).toBe("brighter by 0.60 mag");
    expect(compareCell(q("parallax_mas"), found, { parallax_mas: 4.1, parallax_mas_err: 0.05 }).differs).toBeNull();
    expect(compareCell(q("parallax_mas"), found, { parallax_mas: 5, parallax_mas_err: 0.05 }).differs).toBe("differs by 14.1σ");
    expect(compareCell(q("parallax_mas"), found, { parallax_mas: 5, parallax_mas_err: 0.05 }).text).toBe("5 ± 0.05 mas");
    expect(compareCell(q("pm_ra_cosdec_masyr"), found, { pm_ra_cosdec_masyr: -58 }).differs).toBe("differs by 78 mas/yr");
    expect(compareCell(q("separation_arcsec"), found, { separation_arcsec: 0.4 }).differs).toBeNull();
    expect(compareCell(q("separation_arcsec"), found, { separation_arcsec: 3.7 }).differs).toMatch(/another object/);
    expect(compareCell(q("object_type"), found, { object_type: "V*" })).toEqual({ text: "V*", differs: null });
    expect(compareCell(q("g_mag"), found, null)).toEqual({ text: "—", differs: null });
  });

  it("[INV-023] formats values with their units", () => {
    expect(formatValue(q("separation_arcsec"), 0.123)).toBe("0.12″");
    expect(formatValue(q("j_mag"), 14.489)).toBe("14.49 mag");
    expect(formatValue(q("epoch_jyear"), 2000.405)).toBe("2000.4");
    expect(formatValue(q("main_id"), "HD 1")).toBe("HD 1");
    expect(formatValue(q("g_mag"), null)).toBe("—");
  });

  it("[INV-023] keeps the latest pull per catalogue, in the server's order, and offers the rest", () => {
    const pulls = [pull("simbad", "none"), pull("gaia", "found", [{ separation_arcsec: 0 }]), pull("simbad", "found", [{ separation_arcsec: 1 }], { radius_arcsec: 30 })];
    expect(latestPulls(pulls, DEMO_CATALOGUES).map((p) => [p.source_id, p.radius_arcsec])).toEqual([["gaia", 5], ["simbad", 30]]);
    expect(notYetPulled(DEMO_CATALOGUES, pulls)).toEqual(["2mass", "allwise", "ps1", "vsx"]);
  });

  it("[INV-023] shows only quantities some column reports", () => {
    const keys = shownQuantities({ g_mag: 1 }, [{ separation_arcsec: 0, j_mag: 2 }, null]).map((x) => x.key);
    expect(keys).toEqual(["separation_arcsec", "g_mag", "j_mag"]);
    expect(shownQuantities({ g_mag: 1 }, [null]).map((x) => x.key)).toEqual(["g_mag"]);
  });

  it("[INV-023] puts a known object first and says plainly what each catalogue found", () => {
    const said = findings(
      [
        pull("gaia", "found", [{ source_key: "gaia:DR3:0", separation_arcsec: 0 }, { source_key: "gaia:DR3:9", separation_arcsec: 3 }]),
        pull("vsx", "none"),
        pull("ps1", "failed"),
        pull("simbad", "found", [{ main_id: "V* XX Fix", object_type: "RR*", references: 3, separation_arcsec: 0.21 }]),
      ],
      { g_mag: 12 },
      "gaia:DR3:0",
    );
    expect(said[0]).toEqual({ tone: "warn", text: "SIMBAD already knows V* XX Fix 0.21″ away (type RR*, mentioned in 3 papers). Check its literature before calling this new." });
    expect(said.map((f) => f.text)).toContain("AAVSO VSX has nothing within 5″.");
    expect(said.map((f) => f.text)).toContain("Gaia DR3's nearest source is the candidate itself.");
    expect(said.map((f) => f.text)).toContain("Gaia DR3 has 2 sources within 5″: a crowded spot, so a blend is possible.");
    expect(said.at(-1)?.text).toBe("Pan-STARRS DR2 did not answer, so it says nothing either way. Try again later.");
  });
});

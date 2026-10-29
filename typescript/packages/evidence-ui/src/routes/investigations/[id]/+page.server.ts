import { error } from "@sveltejs/kit";

import { attempt } from "$lib/server/actions.ts";
import { demonstration, evidence, workbench } from "$lib/server/source.ts";
import { EvidenceUnavailable, PlannerUnavailable, type Plan, type ReproductionRun } from "$lib/ports.ts";

import type { Actions, PageServerLoad } from "./$types";

const SHOW = ["open", "parked", "closed", "promoted", "all"] as const;

export const load: PageServerLoad = async ({ params, url }) => {
  const source = evidence();
  const set = await source.investigationSet(params.id);
  if (!set) error(404, `No investigation set ${params.id}`);
  let plan: Plan | null = null;
  try {
    plan = (await source.plans()).find((p) => p.setId === set.id) ?? null;
  } catch (e) {
    if (!(e instanceof PlannerUnavailable)) throw e;
  }
  // The set is worth showing even when its reproduction cannot be read (an API without Investigate).
  let reproduction: ReproductionRun | null = null;
  try {
    reproduction = await source.reproduction(set.id);
  } catch (e) {
    if (!(e instanceof EvidenceUnavailable)) throw e;
  }
  const asked = url.searchParams.get("show") ?? "all";
  const show = (SHOW as readonly string[]).includes(asked) ? asked : "all";
  // The demonstration keeps pictures of its images, not the files a dossier is made of.
  return { set, plan, show, downloads: !demonstration(), reproduction };
};

export const actions: Actions = {
  reproduce: async ({ params }) => attempt(async () => ({ started: (await workbench().reproduceSet(params.id)).state })),
};

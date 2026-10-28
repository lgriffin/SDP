import { error } from "@sveltejs/kit";

import { evidence } from "$lib/server/source.ts";
import { PlannerUnavailable, type Plan } from "$lib/ports.ts";

import type { PageServerLoad } from "./$types";

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
  const asked = url.searchParams.get("show") ?? "all";
  const show = (SHOW as readonly string[]).includes(asked) ? asked : "all";
  return { set, plan, show };
};

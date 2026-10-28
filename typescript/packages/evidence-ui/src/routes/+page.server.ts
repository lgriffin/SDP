import { evidence } from "$lib/server/source.ts";
import { openCandidates } from "$lib/plain.ts";
import { PlannerUnavailable, type Plan } from "$lib/ports.ts";

import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async () => {
  const source = evidence();
  const [experiments, sets] = await Promise.all([source.experiments(), source.investigationSets()]);
  const details = await Promise.all(sets.map((s) => source.investigationSet(s.id)));
  const openBySet = Object.fromEntries(details.filter((d) => d !== null).map((d) => [d.id, openCandidates(d.investigations)]));
  let plans: Plan[] = [];
  let planner = true;
  try {
    plans = await source.plans();
  } catch (e) {
    if (!(e instanceof PlannerUnavailable)) throw e;
    planner = false;
  }
  return { experiments, sets, openBySet, plans, planner };
};

import { evidence } from "$lib/server/source.ts";
import { PlannerUnavailable, type Plan } from "$lib/ports.ts";

import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async () => {
  try {
    return { plans: await evidence().plans(), planner: true };
  } catch (e) {
    if (!(e instanceof PlannerUnavailable)) throw e;
    return { plans: [] as Plan[], planner: false };
  }
};

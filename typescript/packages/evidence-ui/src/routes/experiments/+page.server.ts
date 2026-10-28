import { evidence } from "$lib/server/source.ts";

import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ url }) => {
  const state = url.searchParams.get("state") || undefined;
  const programme = url.searchParams.get("programme") || undefined;
  const all = await evidence().experiments(state);
  const programmes = [...new Set(all.map((e) => e.protocol))].sort();
  const experiments = programme ? all.filter((e) => e.protocol === programme) : all;
  return { state: state ?? null, programme: programme ?? null, programmes, experiments };
};

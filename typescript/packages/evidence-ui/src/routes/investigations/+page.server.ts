import { openCandidates } from "$lib/plain.ts";
import { evidence } from "$lib/server/source.ts";

import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async () => {
  const source = evidence();
  const sets = await source.investigationSets();
  const details = await Promise.all(sets.map((s) => source.investigationSet(s.id)));
  const openBySet = Object.fromEntries(details.filter((d) => d !== null).map((d) => [d.id, openCandidates(d.investigations)]));
  return { sets, openBySet };
};

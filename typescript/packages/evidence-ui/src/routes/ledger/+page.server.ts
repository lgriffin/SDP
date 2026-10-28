import { evidence } from "$lib/server/source.ts";
import { chainProblems } from "$lib/view.ts";

import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async () => {
  const entries = await evidence().ledger();
  return { entries, problems: chainProblems(entries) };
};

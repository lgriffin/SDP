import { evidence } from "$lib/server/source.ts";

import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ url }) => {
  const state = url.searchParams.get("state") ?? undefined;
  return { state: state ?? null, experiments: await evidence().experiments(state) };
};

import { evidence } from "$lib/server/source.ts";

import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ url }) => {
  const after = Number(url.searchParams.get("after") ?? 0) || 0;
  return { after, events: await evidence().events(after, 200) };
};

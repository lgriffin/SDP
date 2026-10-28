import { error } from "@sveltejs/kit";

import { evidence } from "$lib/server/source.ts";

import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ params }) => {
  const set = await evidence().investigationSet(params.id);
  if (!set) error(404, `No investigation set ${params.id}`);
  return { set };
};

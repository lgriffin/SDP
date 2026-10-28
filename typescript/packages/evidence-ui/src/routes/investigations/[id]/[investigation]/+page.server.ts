import { error } from "@sveltejs/kit";

import { evidence } from "$lib/server/source.ts";

import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ params }) => {
  const investigation = await evidence().investigation(params.investigation);
  if (!investigation || investigation.setId !== params.id) error(404, `No investigation ${params.investigation} in ${params.id}`);
  return { investigation };
};

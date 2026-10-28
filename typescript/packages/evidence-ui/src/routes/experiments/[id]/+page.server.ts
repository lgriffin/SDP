import { error } from "@sveltejs/kit";

import { evidence } from "$lib/server/source.ts";

import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ params }) => {
  const source = evidence();
  const experiment = await source.experiment(params.id);
  if (!experiment) error(404, `No experiment ${params.id}`);
  const lineage = await source.lineage(`experiment/${experiment.id}`);
  return { experiment, lineage };
};

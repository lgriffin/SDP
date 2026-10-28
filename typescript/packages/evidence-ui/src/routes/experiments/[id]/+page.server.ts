import { error } from "@sveltejs/kit";

import { attempt } from "$lib/server/actions.ts";
import { evidence, workbench } from "$lib/server/source.ts";

import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ params }) => {
  const source = evidence();
  const experiment = await source.experiment(params.id);
  if (!experiment) error(404, `No experiment ${params.id}`);
  const lineage = await source.lineage(`experiment/${experiment.id}`);
  return { experiment, lineage };
};

export const actions: Actions = {
  run: async ({ params }) =>
    attempt(async () => {
      const r = await workbench().runExperiment(params.id);
      return { done: `The run finished: the experiment is now ${r.state}.` };
    }),
  validate: async ({ params }) =>
    attempt(async () => {
      const r = await workbench().validateExperiment(params.id);
      return { done: `Checked against the gates: the verdict is ${r.verdict}.` };
    }),
};

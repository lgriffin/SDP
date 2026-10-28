import { error, json } from "@sveltejs/kit";

import { evidence } from "$lib/server/source.ts";

import type { RequestHandler } from "./$types";

export const GET: RequestHandler = async ({ params }) => {
  const experiment = await evidence().experiment(params.id);
  if (!experiment?.manifest) error(404, `No manifest for ${params.id}`);
  return json(experiment.manifest, {
    headers: { "content-disposition": `attachment; filename="${params.id}.manifest.json"` },
  });
};

// The set's evidence dossier as a download: one RO-Crate with its plan, stages, candidates, images
// and data, which `sdp investigate reproduce` re-runs from alone (INV-021, INV-022).
import { error } from "@sveltejs/kit";

import { evidence } from "$lib/server/source.ts";

import type { RequestHandler } from "./$types";

export const GET: RequestHandler = async ({ params }) => {
  const found = await evidence().dossier(params.id);
  if (!found) error(404, `No evidence dossier for ${params.id} on this server`);
  return new Response(found.body, {
    headers: {
      "content-type": found.mediaType,
      "content-disposition": `attachment; filename="${params.id.replace(/[^\w.-]/g, "_")}-dossier.zip"`,
      // A note or a triage decision changes the dossier, so it is built afresh each time.
      "cache-control": "no-store",
    },
  });
};

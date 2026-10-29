// A stored artifact's bytes as a download, e.g. the FITS cutout a test read. ?name= names the file.
import { error } from "@sveltejs/kit";

import { evidence } from "$lib/server/source.ts";

import type { RequestHandler } from "./$types";

export const GET: RequestHandler = async ({ params, url }) => {
  const found = await evidence().artifact(params.id);
  if (!found) error(404, `No file ${params.id} on this server`);
  const name = (url.searchParams.get("name") ?? params.id.replace(":", "-")).replace(/[^\w.@()+,-]/g, "_");
  return new Response(found.body, {
    headers: {
      "content-type": found.mediaType,
      "content-disposition": `attachment; filename="${name}"`,
      "cache-control": "private, max-age=31536000, immutable",
    },
  });
};

// A FITS image drawn as a picture, from sdp-api through this server so the browser needs no API token (INV-020).
import { error } from "@sveltejs/kit";

import { evidence } from "$lib/server/source.ts";

import type { RequestHandler } from "./$types";

export const GET: RequestHandler = async ({ params }) => {
  const found = await evidence().preview(params.id);
  if (!found) error(404, `No image ${params.id}`);
  return new Response(found.body, {
    headers: { "content-type": found.mediaType, "cache-control": "private, max-age=31536000, immutable" },
  });
};

import { error } from "@sveltejs/kit";

import { demonstration, evidence } from "$lib/server/source.ts";

import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ params, url }) => {
  const set = await evidence().investigationSet(params.id);
  if (!set) error(404, `No investigation set ${params.id}`);
  const images = set.images ?? [];
  if (!images.length) error(404, "This search read no images.");
  const image = images.find((i) => i.artifact_id === url.searchParams.get("image")) ?? images[0]!;
  const star = image.stars.find((s) => s.object_key === url.searchParams.get("star"))?.object_key ?? null;
  // The demonstration keeps pictures of its images, not the files.
  return { set: { id: set.id, field: set.field, profileId: set.profileId }, images, image, star, downloads: !demonstration() };
};

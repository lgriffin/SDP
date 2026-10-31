import { error, fail } from "@sveltejs/kit";

import { RADII } from "$lib/compare.ts";
import { actorFrom, attempt, rememberedActor } from "$lib/server/actions.ts";
import { evidence, workbench } from "$lib/server/source.ts";

import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ params, cookies }) => {
  const investigation = await evidence().investigation(params.investigation);
  if (!investigation || investigation.setId !== params.id) error(404, `No investigation ${params.investigation} in ${params.id}`);
  const comparison = await evidence().comparison(params.investigation);
  return { investigation, comparison, actor: rememberedActor(cookies) };
};

export const actions: Actions = {
  pull: async ({ request, cookies, params }) => {
    const form = await request.formData();
    const sources = form.getAll("source").map(String);
    const radius = Number(form.get("radius") ?? 5);
    const actor = actorFrom(form, cookies);
    if (typeof actor !== "string") return fail(400, { ...actor.data, sources, radius });
    if (!sources.length) return fail(400, { error: "Tick at least one catalogue to ask.", field: "source", sources, radius });
    if (!(RADII as readonly number[]).includes(radius)) return fail(400, { error: "Choose a search radius from the list.", field: "radius", sources, radius });
    return attempt(
      async () => {
        const summary = await workbench().pullReferences(params.investigation, sources, radius, actor);
        const failed = summary.pulls.filter((p) => p.status === "failed").length;
        const done = failed
          ? `Asked ${summary.pulls.length} catalogue${summary.pulls.length === 1 ? "" : "s"}; ${failed} did not answer.`
          : `Asked ${summary.pulls.length} catalogue${summary.pulls.length === 1 ? "" : "s"}.`;
        return { done };
      },
      { sources, radius },
    );
  },
};

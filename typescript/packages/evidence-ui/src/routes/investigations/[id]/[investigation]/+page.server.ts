import { error, fail } from "@sveltejs/kit";

import { actorFrom, attempt, rememberedActor } from "$lib/server/actions.ts";
import { evidence, workbench } from "$lib/server/source.ts";
import type { TriageAction } from "$lib/ports.ts";

import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ params, cookies }) => {
  const investigation = await evidence().investigation(params.investigation);
  if (!investigation || investigation.setId !== params.id) error(404, `No investigation ${params.investigation} in ${params.id}`);
  // Where the candidate sits on each image the search read (INV-020).
  const set = await evidence().investigationSet(params.id);
  const sightings = (set?.images ?? []).flatMap((image) =>
    image.stars
      .filter((star) => star.object_key === investigation.candidateKey)
      .map((star) => ({ image, star })),
  );
  return { investigation, sightings, actor: rememberedActor(cookies) };
};

const DONE: Record<TriageAction, string> = {
  park: "Parked. It will wait here until someone reopens it.",
  reopen: "Reopened for review.",
  close: "Closed. This is final.",
  annotate: "Note added.",
};

function triage(action: TriageAction): Actions[string] {
  return async ({ request, cookies, params }) => {
    const form = await request.formData();
    const reason = String(form.get("reason") ?? "").trim();
    const actor = actorFrom(form, cookies);
    if (typeof actor !== "string") return fail(400, { ...actor.data, action, reason });
    if (!reason) {
      const ask = action === "annotate" ? "Write the note first." : "Say why, in a sentence. It is kept with the decision.";
      return fail(400, { error: ask, field: "reason", action, reason });
    }
    return attempt(
      async () => {
        await workbench().triage(params.investigation, action, actor, reason);
        return { done: DONE[action], action };
      },
      { action, reason },
    );
  };
}

export const actions: Actions = {
  park: triage("park"),
  reopen: triage("reopen"),
  close: triage("close"),
  annotate: triage("annotate"),
};

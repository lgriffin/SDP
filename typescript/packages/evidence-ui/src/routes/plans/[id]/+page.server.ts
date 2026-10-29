import { error, fail } from "@sveltejs/kit";

import { needsSignature, planProgress } from "$lib/plain.ts";
import { actorFrom, attempt, rememberedActor } from "$lib/server/actions.ts";
import { evidence, workbench } from "$lib/server/source.ts";
import { PlannerUnavailable } from "$lib/ports.ts";

import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ params, cookies }) => {
  let plan;
  try {
    plan = await evidence().plan(params.id);
  } catch (e) {
    if (e instanceof PlannerUnavailable) error(404, "This server does not offer download plans yet.");
    throw e;
  }
  if (!plan) error(404, `No plan ${params.id}`);
  // While a download runs, its investigation set shows how far it has got (INV-018).
  const progress = planProgress(plan);
  const set = progress ? ((await evidence().investigationSets()).find((s) => s.planArtifactId === plan.id) ?? null) : null;
  return { plan, actor: rememberedActor(cookies), stages: set?.stages ?? null };
};

export const actions: Actions = {
  approve: async ({ request, cookies, params }) => {
    const form = await request.formData();
    const actor = actorFrom(form, cookies);
    if (typeof actor !== "string") return actor;
    const plan = await evidence().plan(params.id);
    if (plan && needsSignature(plan)) return fail(409, { error: "This plan is over the approval threshold, so it needs a signed approval." });
    return attempt(async () => {
      const r = await workbench().approvePlan(params.id, actor);
      return { done: r.state === "auto_approved" ? "Approved. It is within budget, so no signature was needed." : "Approved." };
    });
  },
  refuse: async ({ request, cookies, params }) => {
    const form = await request.formData();
    const actor = actorFrom(form, cookies);
    if (typeof actor !== "string") return actor;
    const reason = String(form.get("reason") ?? "").trim();
    if (!reason) return fail(400, { error: "Say why you are refusing it. The reason is kept with the plan.", field: "reason" });
    return attempt(async () => {
      await workbench().refusePlan(params.id, actor, reason);
      return { done: "Refused. Nothing will be downloaded under this plan." };
    });
  },
  execute: async ({ params, cookies }) =>
    attempt(async () => {
      await workbench().executePlan(params.id, rememberedActor(cookies) || "viewer");
      return { done: "Started. This page follows the download until it finishes." };
    }),
};

import { fail, redirect, type Cookies } from "@sveltejs/kit";

import { coneRegion, planHref } from "$lib/plain.ts";
import { actorFrom, attempt, rememberedActor } from "$lib/server/actions.ts";
import { evidence, profiles, workbench } from "$lib/server/source.ts";
import { PlannerUnavailable, type InvestigateRequest } from "$lib/ports.ts";

import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ cookies }) => {
  const source = evidence();
  const [experiments, options] = await Promise.all([source.experiments(), profiles().profiles()]);
  let planner = true;
  try {
    await source.plans();
  } catch (e) {
    if (!(e instanceof PlannerUnavailable)) throw e;
    planner = false;
  }
  return {
    ready: experiments.filter((e) => e.state === "defined" || e.state === "failed"),
    profiles: options.map(({ key, description }) => ({ key, description })),
    planner,
    actor: rememberedActor(cookies),
  };
};

type Parsed = { request: InvestigateRequest } | { problem: string; field: string };

async function parse(form: FormData, actor: string): Promise<Parsed> {
  const number = (name: string) => Number(String(form.get(name) ?? "").trim() || NaN);
  const ra = number("ra");
  const dec = number("dec");
  const radius = number("radius");
  const epoch = number("epoch");
  const seed = number("seed");
  if (!(ra >= 0 && ra < 360)) return { problem: "Right ascension is in degrees, from 0 up to 360.", field: "ra" };
  if (!(dec >= -90 && dec <= 90)) return { problem: "Declination is in degrees, from −90 to 90.", field: "dec" };
  if (!(radius > 0 && radius <= 10)) return { problem: "The radius is in degrees, more than 0 and at most 10.", field: "radius" };
  if (!Number.isFinite(epoch)) return { problem: "The epoch is a Julian year, such as 2016.0.", field: "epoch" };
  if (!Number.isInteger(seed) || seed < 0) return { problem: "The random seed is a whole number, 0 or more.", field: "seed" };
  const key = String(form.get("profile") ?? "");
  const profile = (await profiles().profiles()).find((p) => p.key === key);
  if (!profile) return { problem: "Choose which tests to run.", field: "profile" };
  return {
    request: {
      region: coneRegion(ra, dec, radius),
      epochJyear: epoch,
      label: String(form.get("label") ?? "").trim(),
      profile: profile.document,
      seed,
      actor,
    },
  };
}

async function submitted(request: Request, cookies: Cookies) {
  const form = await request.formData();
  const values = Object.fromEntries([...form.entries()].map(([k, v]) => [k, String(v)]));
  const actor = actorFrom(form, cookies);
  if (typeof actor !== "string") return { failure: fail(400, { ...actor.data, values }) };
  const parsed = await parse(form, actor);
  if ("problem" in parsed) return { failure: fail(400, { error: parsed.problem, field: parsed.field, values }) };
  return { request: parsed.request, values };
}

export const actions: Actions = {
  investigate: async ({ request, cookies }) => {
    const s = await submitted(request, cookies);
    if ("failure" in s) return s.failure;
    const result = await attempt(() => workbench().investigate(s.request), { values: s.values });
    if ("setId" in result) redirect(303, `/investigations/${result.setId}`);
    return result;
  },
  plan: async ({ request, cookies }) => {
    const s = await submitted(request, cookies);
    if ("failure" in s) return s.failure;
    const result = await attempt(() => workbench().planInvestigation(s.request), { values: s.values });
    if ("planId" in result) redirect(303, planHref(result.planId));
    return result;
  },
};

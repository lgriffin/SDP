// Shared form-action plumbing: who is acting (remembered in a cookie so a scientist types their name
// once), and turning a refused or failed command into a message the page can show beside the button.
import { fail, type ActionFailure, type Cookies } from "@sveltejs/kit";

import { CommandRefused, EvidenceUnavailable, PlannerUnavailable } from "../ports.ts";

const ACTOR = "sdp_actor";

export function rememberedActor(cookies: Cookies): string {
  return cookies.get(ACTOR) ?? "";
}

/** The name on a form, remembered for next time; a failure when it is missing. */
export function actorFrom(form: FormData, cookies: Cookies): string | ActionFailure<ActionError> {
  const actor = String(form.get("actor") ?? "").trim();
  if (!actor) return fail(400, { error: "Please say who you are, so the record shows who took this step.", field: "actor" });
  cookies.set(ACTOR, actor, { path: "/", httpOnly: true, sameSite: "strict", maxAge: 60 * 60 * 24 * 365, secure: false });
  return actor;
}

export interface ActionError {
  error: string;
  field?: string;
  command?: string;
  [key: string]: unknown;
}

export async function attempt<T>(action: () => Promise<T>, extra: Record<string, unknown> = {}): Promise<T | ActionFailure<ActionError>> {
  try {
    return await action();
  } catch (e) {
    if (e instanceof CommandRefused) return fail(e.status, { ...extra, error: e.message });
    if (e instanceof PlannerUnavailable) return fail(501, { ...extra, error: "This server does not offer download plans yet." });
    if (e instanceof EvidenceUnavailable) return fail(503, { ...extra, error: `The SDP server could not be reached (${e.message}).` });
    throw e;
  }
}

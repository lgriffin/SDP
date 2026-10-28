// An unreachable API is the most likely failure on a lab machine: say so plainly, not "Internal Error".
// On a demonstration server each browser gets its own copy of the synthetic data.
import { randomUUID } from "node:crypto";

import type { Handle, HandleServerError } from "@sveltejs/kit";

import { EvidenceUnavailable } from "$lib/ports.ts";
import { isDemonstration, visitor } from "$lib/server/source.ts";

const DEMO = "sdp_demo";

export const handle: Handle = async ({ event, resolve }) => {
  if (!isDemonstration()) return resolve(event);
  let key = event.cookies.get(DEMO);
  if (!key) {
    key = randomUUID();
    event.cookies.set(DEMO, key, { path: "/", httpOnly: true, sameSite: "strict", secure: false });
  }
  return visitor.run(key, () => resolve(event));
};

export const handleError: HandleServerError = ({ error }) => {
  if (error instanceof EvidenceUnavailable) {
    return { message: `The SDP server could not be reached. Check that sdp-api is running. (${error.message})` };
  }
  return { message: "Something went wrong on the UI server." };
};

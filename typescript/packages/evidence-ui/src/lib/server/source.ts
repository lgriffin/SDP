// Composition root for the UI server: GraphQL and REST against sdp-api, or the in-memory fakes for
// demos and browser tests (SDP_UI_FIXTURES=1). Test profiles come from SDP_PROFILES_DIR, plus the
// profiles past sets ran under. A demonstration gives each browser its own copy of the fixtures
// (keyed by a cookie, see hooks.server.ts), so one visitor's clicks never change another's page.
import { AsyncLocalStorage } from "node:async_hooks";

import { env } from "$env/dynamic/private";

import { GraphQLEvidenceSource } from "../adapters/graphql.ts";
import { DirectoryProfileCatalogue } from "../adapters/profiles.ts";
import { commandBase, RestWorkbench } from "../adapters/rest.ts";
import type { EvidenceSource, ProfileCatalogue, Workbench } from "../ports.ts";
import { InMemoryEvidenceSource } from "../testing/fixtures.ts";
import { InMemoryWorkbench } from "../testing/workbench.ts";

interface Runtime {
  evidence: EvidenceSource;
  workbench: Workbench;
  profiles: ProfileCatalogue;
  demonstration: boolean;
}

let runtime: Runtime | undefined;

/** The demonstration visitor handling the current request, set by hooks.server.ts. */
export const visitor = new AsyncLocalStorage<string>();
const demos = new Map<string, Runtime>();
const MAX_DEMOS = 200;

export const isDemonstration = (): boolean => env.SDP_UI_FIXTURES === "1";

function demo(key: string): Runtime {
  let r = demos.get(key);
  if (!r) {
    const evidence = new InMemoryEvidenceSource();
    r = {
      evidence,
      workbench: new InMemoryWorkbench(evidence),
      profiles: new DirectoryProfileCatalogue(env.SDP_PROFILES_DIR, evidence),
      demonstration: true,
    };
    if (demos.size >= MAX_DEMOS) demos.delete(demos.keys().next().value as string);
  }
  demos.delete(key);
  demos.set(key, r);
  return r;
}

function build(): Runtime {
  const endpoint = env.SDP_API_URL ?? "http://127.0.0.1:8000/graphql";
  const evidence = new GraphQLEvidenceSource(endpoint);
  return {
    evidence,
    workbench: new RestWorkbench(commandBase(endpoint)),
    profiles: new DirectoryProfileCatalogue(env.SDP_PROFILES_DIR, evidence),
    demonstration: false,
  };
}

function current(): Runtime {
  if (isDemonstration()) return demo(visitor.getStore() ?? "shared");
  runtime ??= build();
  return runtime;
}

export const evidence = (): EvidenceSource => current().evidence;
export const workbench = (): Workbench => current().workbench;
export const profiles = (): ProfileCatalogue => current().profiles;
export const demonstration = (): boolean => current().demonstration;

// Composition root for the UI server: GraphQL against sdp-api, or the fixture source for demos and
// browser tests (SDP_UI_FIXTURES=1).
import { env } from "$env/dynamic/private";

import { GraphQLEvidenceSource } from "../adapters/graphql.ts";
import type { EvidenceSource } from "../ports.ts";
import { InMemoryEvidenceSource } from "../testing/fixtures.ts";

let source: EvidenceSource | undefined;

export function evidence(): EvidenceSource {
  source ??= env.SDP_UI_FIXTURES === "1"
    ? new InMemoryEvidenceSource()
    : new GraphQLEvidenceSource(env.SDP_API_URL ?? "http://127.0.0.1:8000/graphql");
  return source;
}

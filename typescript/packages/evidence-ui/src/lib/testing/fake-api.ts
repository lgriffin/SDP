// A fake sdp-api: answers the UI's GraphQL operations from fixture data, so the GraphQL adapter is
// tested over real HTTP-shaped requests without a server (fakes over mocks, TST-005).
import { FIXTURES, type FixtureData } from "./fixtures.ts";

interface Request {
  query: string;
  variables: Record<string, unknown>;
}

export interface FakeApi {
  fetch: (input: string, init?: RequestInit) => Promise<Response>;
  requests: Request[];
}

/** `planner: false` answers like a server from before Stage E, whose schema has no plan queries. */
export function fakeApi(data: FixtureData = FIXTURES, status = 200, planner = true): FakeApi {
  const requests: Request[] = [];
  const answer = (query: string, v: Record<string, unknown>): unknown => {
    const operation = /query\s+(\w+)/.exec(query)?.[1];
    switch (operation) {
      case "Experiments":
        return { experiments: data.experiments.filter((e) => v.state == null || e.state === v.state) };
      case "Experiment":
        return { experiment: data.experiment[String(v.id)] ?? null };
      case "Lineage":
        return { lineage: data.lineage[String(v.entityId)] ?? [] };
      case "Ledger":
        return { ledger: data.ledger };
      case "InvestigationSets":
        return { investigationSets: data.investigationSets };
      case "InvestigationSet":
        return { investigationSet: data.investigationSet[String(v.id)] ?? null };
      case "Investigation":
        return { investigation: data.investigation[String(v.id)] ?? null };
      case "Plans":
        return { plans: data.plans };
      case "Plan":
        return { plan: data.plans.find((p) => p.id === v.id) ?? null };
      case "Archives":
        return { archives: data.archives };
      case "Events":
        return { events: data.events.filter((e) => e.sequence > Number(v.after)).slice(0, Number(v.limit)) };
      default:
        return undefined;
    }
  };
  return {
    requests,
    fetch: async (input, init) => {
      // GET /artifacts/{id}/preview.png answers with the fixture picture (INV-020).
      const preview = /\/artifacts\/([^/]+)\/preview\.png$/.exec(input);
      if (init?.method === "GET") {
        const b64 = preview ? data.previews[decodeURIComponent(preview[1]!)] : undefined;
        if (status !== 200) return new Response("unavailable", { status });
        return b64 ? new Response(Buffer.from(b64, "base64"), { headers: { "content-type": "image/png" } }) : new Response("no such artifact", { status: 404 });
      }
      const body = JSON.parse(String(init?.body)) as Request;
      requests.push(body);
      if (status !== 200) return new Response("unavailable", { status });
      const operation = /query\s+(\w+)/.exec(body.query)?.[1] ?? "";
      if (!planner && operation.startsWith("Plan")) {
        const field = operation === "Plans" ? "plans" : "plan";
        // The message exactly as sdp-api's graphql-core words it.
        const errors = [{ message: `Cannot query field '${field}' on type 'Query'.` }];
        return new Response(JSON.stringify({ errors }), { headers: { "content-type": "application/json" } });
      }
      const result = answer(body.query, body.variables);
      const payload = result === undefined ? { errors: [{ message: "unknown operation" }] } : { data: result };
      return new Response(JSON.stringify(payload), { headers: { "content-type": "application/json" } });
    },
  };
}

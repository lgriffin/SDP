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

export function fakeApi(data: FixtureData = FIXTURES, status = 200): FakeApi {
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
      case "Events":
        return { events: data.events.filter((e) => e.sequence > Number(v.after)).slice(0, Number(v.limit)) };
      default:
        return undefined;
    }
  };
  return {
    requests,
    fetch: async (_input, init) => {
      const body = JSON.parse(String(init?.body)) as Request;
      requests.push(body);
      if (status !== 200) return new Response("unavailable", { status });
      const result = answer(body.query, body.variables);
      const payload = result === undefined ? { errors: [{ message: "unknown operation" }] } : { data: result };
      return new Response(JSON.stringify(payload), { headers: { "content-type": "application/json" } });
    },
  };
}

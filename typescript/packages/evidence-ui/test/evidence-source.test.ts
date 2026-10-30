// One contract suite for the EvidenceSource port, run against the fake and the GraphQL adapter.
import { describe, expect, it } from "vitest";

import { GraphQLEvidenceSource } from "../src/lib/adapters/graphql.ts";
import { EvidenceUnavailable, PlannerUnavailable, type EvidenceSource } from "../src/lib/ports.ts";
import { fakeApi } from "../src/lib/testing/fake-api.ts";
import { FIXTURES, InMemoryEvidenceSource } from "../src/lib/testing/fixtures.ts";

function contract(name: string, make: () => EvidenceSource): void {
  describe(`EvidenceSource contract: ${name}`, () => {
    it("lists every experiment and filters by state", async () => {
      const source = make();
      expect((await source.experiments()).map((e) => e.id)).toEqual(FIXTURES.experiments.map((e) => e.id));
      const failed = await source.experiments("failed");
      expect(failed.length).toBe(1);
      expect(failed[0]?.state).toBe("failed");
    });

    it("returns an experiment with its gates, tasks and dossiers, or null", async () => {
      const source = make();
      const verified = await source.experiment("exp-000001");
      expect(verified?.verdict?.status).toBe("VERIFIED");
      expect(verified?.checks.map((c) => c.gateId)).toEqual(["SYN-G-01", "SYN-G-02"]);
      expect(verified?.dossiers.length).toBe(1);
      expect(await source.experiment("exp-missing")).toBeNull();
    });

    it("returns lineage, the ledger and events after a sequence", async () => {
      const source = make();
      expect((await source.lineage("experiment/exp-000001")).length).toBeGreaterThan(0);
      expect(await source.lineage("nothing")).toEqual([]);
      expect((await source.ledger()).map((e) => e.kind)).toEqual(["predeclaration", "approval"]);
      const events = await source.events(5, 3);
      expect(events.map((e) => e.sequence)).toEqual([6, 7, 8]);
    });

    it("returns investigation sets, a set with its candidates, and an investigation with its dossier", async () => {
      const source = make();
      const sets = await source.investigationSets();
      expect(sets.map((s) => s.id)).toEqual(["invset-000001", "invset-000101"]);
      expect(sets[0]?.stages.map((s) => s.state)).toEqual(["completed", "completed", "completed", "completed"]);
      const set = await source.investigationSet("invset-000001");
      expect(set?.investigations.map((i) => i.state).sort()).toEqual(["closed", "open", "open", "open", "parked"]);
      expect(set?.label).toBe("EXPLORATORY");
      const inv = await source.investigation("inv-000002");
      expect(inv?.state).toBe("parked");
      expect(inv?.transitions.at(-1)?.reason).toBe("Revisit when Gaia DR4 lands.");
      expect((inv?.dossier as { kind: string }).kind).toBe("investigation-dossier");
      expect(await source.investigationSet("invset-missing")).toBeNull();
      expect(await source.investigation("inv-missing")).toBeNull();
    });

    it("returns plans with their state and document, or null", async () => {
      const source = make();
      const plans = await source.plans();
      expect(plans.map((p) => p.state)).toEqual(["executed", "drafted", "drafted", "executed"]);
      const first = plans[0]!;
      expect((await source.plan(first.id))?.document.plan_id).toBe(first.id);
      expect(await source.plan("sha256:missing")).toBeNull();
    });

    it("[INV-019] lists the registered archives and the last registration", async () => {
      const archives = await make().archives();
      expect(archives?.sources.map((a) => a.sourceId)).toEqual(["2mass", "gaia", "mast"]);
      expect(archives?.sources[2]).toEqual({ sourceId: "mast", origin: "registry", verifiedOn: "2026-10-29", productTypes: ["cube", "image", "spectrum", "timeseries"] });
      expect(archives?.registration).toBeNull();
    });
  });
}

contract("in-memory fixtures", () => new InMemoryEvidenceSource());
contract("GraphQL adapter over a fake sdp-api", () => new GraphQLEvidenceSource("http://api.test/graphql", fakeApi().fetch));

describe("GraphQL adapter failures", () => {
  it("reports an unavailable API as EvidenceUnavailable", async () => {
    const source = new GraphQLEvidenceSource("http://api.test/graphql", fakeApi(FIXTURES, 503).fetch);
    await expect(source.experiments()).rejects.toBeInstanceOf(EvidenceUnavailable);
  });

  it("reports GraphQL errors with their messages", async () => {
    const source = new GraphQLEvidenceSource("http://api.test/graphql", fakeApi().fetch);
    await expect(source.request("query Unknown { x }")).rejects.toThrow("unknown operation");
  });

  it("reports a refused connection", async () => {
    const refuse = async (): Promise<Response> => {
      throw new TypeError("fetch failed");
    };
    const source = new GraphQLEvidenceSource("http://api.test/graphql", refuse);
    await expect(source.ledger()).rejects.toThrow("cannot reach http://api.test/graphql");
  });

  it("says the planner is missing on a server from before Stage E", async () => {
    const source = new GraphQLEvidenceSource("http://api.test/graphql", fakeApi(FIXTURES, 200, false).fetch);
    await expect(source.plans()).rejects.toBeInstanceOf(PlannerUnavailable);
    await expect(source.plan("p")).rejects.toBeInstanceOf(PlannerUnavailable);
    expect((await source.experiments()).length).toBe(4);
  });

  it("sends variables, not interpolated strings", async () => {
    const api = fakeApi();
    await new GraphQLEvidenceSource("http://api.test/graphql", api.fetch).experiment('exp"} evil');
    expect(api.requests[0]?.variables).toEqual({ id: 'exp"} evil' });
  });
});

describe("images", () => {
  it("[INV-020] fetches an image's picture, or null when there is none", async () => {
    const fake = fakeApi();
    const [artifact] = Object.keys(FIXTURES.previews);
    for (const source of [new InMemoryEvidenceSource(), new GraphQLEvidenceSource("http://api.test/graphql", fake.fetch)]) {
      const picture = await source.preview(artifact!);
      expect(picture?.mediaType).toBe("image/png");
      expect([...picture!.body.slice(0, 4)]).toEqual([0x89, 0x50, 0x4e, 0x47]);
      expect(await source.preview("sha256:missing")).toBeNull();
    }
    expect(await new InMemoryEvidenceSource().artifact(artifact!)).toBeNull();
    const set = await new InMemoryEvidenceSource().investigationSet("invset-000101");
    expect(set?.images?.[0]?.stars.filter((s) => s.flagged).map((s) => s.object_key)).toEqual(["j-vanished"]);
  });

  it("[INV-020] reads an artifact's bytes from beside the GraphQL endpoint", async () => {
    const seen: string[] = [];
    const source = new GraphQLEvidenceSource("http://api.test/graphql", async (url) => {
      seen.push(url);
      return url.endsWith("/missing") ? new Response("", { status: 404 }) : new Response(new Uint8Array([1, 2]), { headers: { "content-type": "application/fits" } });
    });
    expect(await source.artifact("sha256:ab")).toEqual({ body: new Uint8Array([1, 2]), mediaType: "application/fits" });
    expect(await source.artifact("missing")).toBeNull();
    expect(seen).toEqual(["http://api.test/artifacts/sha256%3Aab", "http://api.test/artifacts/missing"]);
    const down = new GraphQLEvidenceSource("http://api.test/graphql", async () => new Response("", { status: 500 }));
    await expect(down.preview("x")).rejects.toBeInstanceOf(EvidenceUnavailable);
    await expect(down.preview("x")).rejects.toThrow("http://api.test/artifacts/x/preview.png answered 500");
    const gap = new GraphQLEvidenceSource("http://api.test/graphql", async () => Response.json({ error: "stages/test.json is not in the artifact store" }, { status: 409 }));
    await expect(gap.dossier("invset-1")).rejects.toThrow("stages/test.json is not in the artifact store");
    const odd = new GraphQLEvidenceSource("http://api.test/graphql", async () => Response.json(null, { status: 500 }));
    await expect(odd.dossier("invset-1")).rejects.toThrow("http://api.test/investigations/invset-1/dossier.zip answered 500");
  });
});

describe("the evidence dossier", () => {
  it("[INV-021] downloads a set's dossier from beside the GraphQL endpoint; the demonstration has none", async () => {
    const seen: string[] = [];
    const source = new GraphQLEvidenceSource("http://api.test/graphql", async (url) => {
      seen.push(url);
      return url.includes("missing") ? new Response("", { status: 404 }) : new Response(new Uint8Array([0x50, 0x4b]), { headers: { "content-type": "application/zip" } });
    });
    expect(await source.dossier("invset 1")).toEqual({ body: new Uint8Array([0x50, 0x4b]), mediaType: "application/zip" });
    expect(await source.dossier("invset-missing")).toBeNull();
    expect(seen[0]).toBe("http://api.test/investigations/invset%201/dossier.zip");
    expect(await new InMemoryEvidenceSource().dossier("invset-000101")).toBeNull();
  });

  it("[INV-022] reads the last reproduction check, or null from a server without one", async () => {
    const run = { state: "finished", started_at: "a", finished_at: "b", error: null, result: { ok: true, results_compared: 3, seed: 1, differences: [] } };
    const seen: string[] = [];
    const source = new GraphQLEvidenceSource("http://api.test/graphql", async (url) => {
      seen.push(url);
      if (url.includes("older")) return new Response("Method Not Allowed", { status: 405 });
      return new Response(JSON.stringify({ reproduction: url.includes("fresh") ? null : run }));
    });
    expect(await source.reproduction("invset 1")).toEqual(run);
    expect(await source.reproduction("fresh")).toBeNull();
    expect(await source.reproduction("older")).toBeNull();
    expect(seen[0]).toBe("http://api.test/investigations/invset%201/reproduce");
    const down = new GraphQLEvidenceSource("http://api.test/graphql", async () => new Response("", { status: 500 }));
    await expect(down.reproduction("x")).rejects.toBeInstanceOf(EvidenceUnavailable);
    const gone = new GraphQLEvidenceSource("http://api.test/graphql", async () => {
      throw new TypeError("fetch failed");
    });
    await expect(gone.reproduction("x")).rejects.toThrow("cannot reach");
  });
});

describe("an older sdp-api", () => {
  const older = (missing: string) => async (_: string, init?: RequestInit) => {
    const { query } = JSON.parse(String(init?.body)) as { query: string };
    if (new RegExp(`\\b${missing}\\b`).test(query)) {
      return new Response(JSON.stringify({ errors: [{ message: `Cannot query field '${missing}' on type 'X'.` }] }));
    }
    return fakeApi().fetch(_, init);
  };

  it("[INV-019] without archive registration lists no archives instead of failing", async () => {
    expect(await new GraphQLEvidenceSource("http://api.test/graphql", older("archives")).archives()).toBeNull();
  });

  it("[INV-018] without background runs still lists its plans", async () => {
    const plans = await new GraphQLEvidenceSource("http://api.test/graphql", older("run")).plans();
    expect(plans.length).toBe(4);
  });

  it("[INV-020] without the image view still shows a set", async () => {
    const set = await new GraphQLEvidenceSource("http://api.test/graphql", older("images")).investigationSet("invset-000001");
    expect(set?.id).toBe("invset-000001");
  });
});

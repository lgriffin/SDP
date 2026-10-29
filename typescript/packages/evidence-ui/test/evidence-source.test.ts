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
      expect(sets.map((s) => s.id)).toEqual(["invset-000001"]);
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
      expect(plans.map((p) => p.state)).toEqual(["executed", "drafted", "drafted"]);
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

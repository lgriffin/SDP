// One contract suite for the EvidenceSource port, run against the fake and the GraphQL adapter.
import { describe, expect, it } from "vitest";

import { GraphQLEvidenceSource } from "../src/lib/adapters/graphql.ts";
import { EvidenceUnavailable, type EvidenceSource } from "../src/lib/ports.ts";
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

  it("sends variables, not interpolated strings", async () => {
    const api = fakeApi();
    await new GraphQLEvidenceSource("http://api.test/graphql", api.fetch).experiment('exp"} evil');
    expect(api.requests[0]?.variables).toEqual({ id: 'exp"} evil' });
  });
});

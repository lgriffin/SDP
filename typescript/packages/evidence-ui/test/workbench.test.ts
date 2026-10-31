// The Workbench port: the REST adapter's requests and error mapping, and the fake's triage, run and
// plan rules (which mirror the kernel's, so pages behave the same against either).
import { describe, expect, it } from "vitest";

import { commandBase, RestWorkbench, withToken } from "../src/lib/adapters/rest.ts";
import { CommandRefused, EvidenceUnavailable, PlannerUnavailable } from "../src/lib/ports.ts";
import { InMemoryEvidenceSource } from "../src/lib/testing/fixtures.ts";
import { InMemoryWorkbench } from "../src/lib/testing/workbench.ts";

interface Sent {
  url: string;
  body: unknown;
}

function server(status: number, body: string): { fetch: (u: string, i?: RequestInit) => Promise<Response>; sent: Sent[] } {
  const sent: Sent[] = [];
  return {
    sent,
    fetch: async (url, init) => {
      sent.push({ url, body: JSON.parse(String(init?.body)) });
      return new Response(body, { status });
    },
  };
}

describe("RestWorkbench", () => {
  it("derives the command base from the GraphQL endpoint", () => {
    expect(commandBase("http://h:8000/graphql")).toBe("http://h:8000");
    expect(commandBase("http://h:8000/graphql/")).toBe("http://h:8000");
  });

  it("posts each command to its endpoint with snake_case bodies", async () => {
    const s = server(200, JSON.stringify({ experiment_id: "e 1", state: "completed", set_id: "set-1", plan_id: "p" }));
    const w = new RestWorkbench("http://api.test/", s.fetch);
    expect(await w.runExperiment("e 1")).toEqual({ experimentId: "e 1", state: "completed" });
    expect((await w.investigate({ region: "icrs;circle(1,2,3)", epochJyear: 2016, label: "L", profile: { a: 1 }, seed: 4, actor: "ada" })).setId).toBe("set-1");
    await w.triage("inv-1", "park", "ada", "later");
    expect(s.sent.map((x) => x.url)).toEqual([
      "http://api.test/experiments/e%201/run",
      "http://api.test/investigations",
      "http://api.test/investigations/inv-1/triage",
    ]);
    expect(s.sent[1]?.body).toEqual({ region: "icrs;circle(1,2,3)", epoch_jyear: 2016, label: "L", profile: { a: 1 }, seed: 4, actor: "ada" });
    expect(s.sent[2]?.body).toEqual({ action: "park", actor: "ada", reason: "later" });
  });

  it("turns a refusal into CommandRefused with the server's reason", async () => {
    const w = new RestWorkbench("http://api.test", server(409, JSON.stringify({ error: "a signed ledger approval is required (INT-014)" })).fetch);
    const refused = await w.approvePlan("p", "ada").catch((e: unknown) => e);
    expect(refused).toBeInstanceOf(CommandRefused);
    expect((refused as CommandRefused).status).toBe(409);
    expect((refused as Error).message).toContain("INT-014");
  });

  it("reads plan state from the answer or from its approval", async () => {
    expect((await new RestWorkbench("http://a", server(200, JSON.stringify({ state: "auto_approved" })).fetch).approvePlan("p", "x")).state).toBe("auto_approved");
    const doc = JSON.stringify({ approval: { decision: "refused" } });
    expect((await new RestWorkbench("http://a", server(200, doc).fetch).refusePlan("p", "x", "no")).state).toBe("refused");
  });

  it("says the planner is missing when its routes are unknown", async () => {
    const w = new RestWorkbench("http://api.test", server(404, "Not Found").fetch);
    await expect(w.executePlan("p", "ada")).rejects.toBeInstanceOf(PlannerUnavailable);
    await expect(w.runExperiment("e")).rejects.toBeInstanceOf(EvidenceUnavailable);
  });

  it("says the planner is missing when the server has its routes but no planner wired", async () => {
    const body = JSON.stringify({ error: "the planner is not wired on this server", type: "PlannerUnavailable" });
    const w = new RestWorkbench("http://api.test", server(503, body).fetch);
    await expect(w.approvePlan("p", "leigh")).rejects.toBeInstanceOf(PlannerUnavailable);
    const down = new RestWorkbench("http://api.test", server(503, JSON.stringify({ error: "db down" })).fetch);
    await expect(down.approvePlan("p", "leigh")).rejects.toBeInstanceOf(EvidenceUnavailable);
  });

  it("[INV-018] starts a plan's download in the background and returns its run", async () => {
    const run = { state: "running", started_at: "2026-10-29T22:00:00+00:00", finished_at: null, error: null };
    const s = server(202, JSON.stringify({ state: "auto_approved", run }));
    const w = new RestWorkbench("http://api.test", s.fetch);
    expect(await w.executePlan("sha256:p 1", "ada")).toEqual({ state: "auto_approved", run });
    expect(s.sent).toEqual([{ url: "http://api.test/plans/sha256%3Ap%201/execute", body: { actor: "ada", background: true } }]);
  });

  it("[INV-019] asks the server to register the archives", async () => {
    const registration = { state: "running", started_at: "2026-10-29T22:00:00+00:00", finished_at: null, error: null };
    const s = server(202, JSON.stringify({ sources: [], registration }));
    const w = new RestWorkbench("http://api.test", s.fetch);
    expect(await w.registerArchives()).toEqual({ sources: [], registration });
    expect(s.sent.map((x) => x.url)).toEqual(["http://api.test/archives/register"]);
  });

  it("[INV-022] starts reproducing a set from its dossier in the background", async () => {
    const run = { state: "running", started_at: "2026-10-29T22:00:00+00:00", finished_at: null, error: null };
    const s = server(202, JSON.stringify({ reproduction: run }));
    const w = new RestWorkbench("http://api.test", s.fetch);
    expect(await w.reproduceSet("invset 1")).toEqual(run);
    expect(s.sent.map((x) => x.url)).toEqual(["http://api.test/investigations/invset%201/reproduce"]);
    const refused = new RestWorkbench("http://api.test", server(404, JSON.stringify({ error: "no investigation set x" })).fetch);
    await expect(refused.reproduceSet("x")).rejects.toBeInstanceOf(CommandRefused);
  });

  it("reports an unreachable server", async () => {
    const w = new RestWorkbench("http://api.test", async () => {
      throw new TypeError("fetch failed");
    });
    await expect(w.validateExperiment("e")).rejects.toThrow("cannot reach http://api.test");
  });
});

describe("InMemoryWorkbench", () => {
  const make = () => {
    const source = new InMemoryEvidenceSource();
    return { source, w: new InMemoryWorkbench(source, () => "2026-10-29T09:00:00+00:00") };
  };

  it("runs a defined experiment, then validates it, and refuses out of order", async () => {
    const { source, w } = make();
    await expect(w.validateExperiment("exp-000004")).rejects.toBeInstanceOf(CommandRefused);
    expect((await w.runExperiment("exp-000004")).state).toBe("completed");
    expect((await source.experiments("completed")).map((e) => e.id)).toEqual(["exp-000004"]);
    expect((await w.validateExperiment("exp-000004")).verdict).toBe("INCONCLUSIVE");
    await expect(w.runExperiment("exp-000001")).rejects.toThrow("only a defined or failed experiment can run");
  });

  it("triages by the kernel's transitions and records who and why", async () => {
    const { source, w } = make();
    expect((await w.triage("inv-000002", "reopen", "ada", "DR4 is out")).state).toBe("open");
    const inv = await source.investigation("inv-000002");
    expect(inv?.transitions.at(-1)).toEqual({ fromState: "parked", toState: "open", reason: "DR4 is out", actor: "ada", at: "2026-10-29T09:00:00+00:00" });
    expect((await source.investigationSet("invset-000001"))?.investigations.find((i) => i.id === "inv-000002")?.state).toBe("open");
    await expect(w.triage("inv-000005", "reopen", "ada", "x")).rejects.toThrow("cannot move from closed to open");
    await expect(w.triage("inv-000001", "park", "ada", " ")).rejects.toThrow("reason");
    await w.triage("inv-000001", "annotate", "ada", "Bright neighbour.");
    const notes = (await source.investigation("inv-000001"))?.notes as { text: string }[];
    expect(notes.at(-1)?.text).toBe("Bright neighbour.");
  });

  it("auto-approves a plan within the threshold and refuses to approve one above it", async () => {
    const { source, w } = make();
    const [, small, large] = await source.plans();
    expect((await w.approvePlan(small!.id, "ada")).state).toBe("auto_approved");
    expect((await w.executePlan(small!.id, "ada")).run?.state).toBe("running");
    await expect(w.approvePlan(large!.id, "ada")).rejects.toThrow("a signed ledger approval is required (INT-014)");
    await expect(w.executePlan(large!.id, "ada")).rejects.toThrow("only an approved plan runs");
    expect((await w.refusePlan(large!.id, "ada", "too big for this field")).state).toBe("refused");
    expect((await source.plan(large!.id))?.document.approval?.reason).toBe("too big for this field");
  });
});

describe("InMemoryWorkbench background runs", () => {
  it("[INV-018] shows a demonstration download running, then finished a moment later", async () => {
    const source = new InMemoryEvidenceSource();
    const w = new InMemoryWorkbench(source, () => "2026-10-29T09:00:00.000Z");
    const [, small] = await source.plans();
    await w.approvePlan(small!.id, "ada");
    await w.executePlan(small!.id, "ada");
    expect((await w.executePlan(small!.id, "ada")).run?.started_at).toBe("2026-10-29T09:00:00.000Z");
    const p = source.data.plans.find((x) => x.id === small!.id)!;
    source.settle(p, Date.parse("2026-10-29T09:00:01.000Z"));
    expect(p.state).toBe("auto_approved");
    source.settle(p, Date.parse("2026-10-29T09:00:03.000Z"));
    expect(p.state).toBe("executed");
    expect(p.run).toEqual({ state: "finished", started_at: "2026-10-29T09:00:00.000Z", finished_at: "2026-10-29T09:00:03.000Z", error: null });
    expect(p.execution?.acquired.length).toBe(p.document.items.length);
    expect(p.setId).toBe("invset-000001");
  });

  it("[INV-022] reproduces a demonstration set a moment after it starts, and fails one whose tests did not run", async () => {
    const source = new InMemoryEvidenceSource();
    const w = new InMemoryWorkbench(source, () => "2026-10-29T09:00:00.000Z");
    const set = source.data.investigationSet["invset-000101"]!;
    expect(await source.reproduction(set.id)).toBeNull();
    expect((await w.reproduceSet(set.id)).state).toBe("running");
    expect(await w.reproduceSet(set.id)).toBe(source.data.reproductions[set.id]);
    const done = await source.reproduction(set.id);
    expect(done).toMatchObject({ state: "finished", result: { ok: true, results_compared: set.objectsExamined, seed: set.seed, differences: [] } });
    await expect(w.reproduceSet("invset-missing")).rejects.toMatchObject({ status: 404 });
    expect(await source.reproduction("invset-missing")).toBeNull();
    set.stages = set.stages.map((st) => (st.stage === "test" ? { ...st, state: "failed" } : st));
    await w.reproduceSet(set.id);
    expect(await source.reproduction(set.id)).toMatchObject({ state: "failed", error: expect.stringContaining("no stages/test.json") });
  });

  it("[INV-021] drafts the JWST plan only for the JWST example, and opens its set once it has run", async () => {
    const source = new InMemoryEvidenceSource();
    const w = new InMemoryWorkbench(source, () => "2026-10-29T09:00:00.000Z");
    const profile = { profile_id: "jwst-image-check", archives: [{ source_id: "mast" }] };
    const ask = (region: string, p: Record<string, unknown> = profile) =>
      w.planInvestigation({ region, epochJyear: 2016, label: "", profile: p, seed: 0, actor: "ada" });
    const { planId } = await ask("icrs;circle(259.323,43.1505,0.004)");
    const p = source.data.plans.find((x) => x.id === planId)!;
    expect(p.state).toBe("drafted");
    expect(p.document.items.some((i) => i.source_id.endsWith("mast"))).toBe(true);
    await w.approvePlan(planId, "ada");
    await w.executePlan(planId, "ada");
    source.settle(p, Date.parse("2026-10-29T09:00:03.000Z"));
    expect(p.setId).toBe("invset-000101");
    await expect(ask("icrs;circle(10,10,0.1)")).rejects.toThrow("one JWST field");
    await expect(ask("icrs;circle(259.323,43.1505,0.004)", { ...profile, profile_id: "other" })).rejects.toMatchObject({ status: 409 });
  });

  it("[INV-019] registers the demonstration archives", async () => {
    const source = new InMemoryEvidenceSource();
    source.data.archives = { sources: [], registration: null };
    const w = new InMemoryWorkbench(source, () => "2026-10-29T09:00:00.000Z");
    const done = await w.registerArchives();
    expect(done.sources.map((a) => a.sourceId)).toEqual(["2mass", "gaia", "mast"]);
    expect((await source.archives())?.registration?.state).toBe("finished");
  });
});

describe("the desktop app's API token", () => {
  it("is added to every request, keeping the request's own headers", async () => {
    const seen: Record<string, string>[] = [];
    const base = async (_: string, init?: RequestInit): Promise<Response> => {
      seen.push(init?.headers as Record<string, string>);
      return new Response("{}");
    };
    await withToken(base, "t0k")("http://api.test/graphql", { headers: { "content-type": "application/json" } });
    expect(seen[0]).toEqual({ "content-type": "application/json", authorization: "Bearer t0k" });
    expect(withToken(base, undefined)).toBe(base);
  });
});

describe("reference pulls (INV-024)", () => {
  it("[INV-024] the REST adapter posts the catalogues, radius and actor", async () => {
    const s = server(200, JSON.stringify({ investigation_id: "inv-1", pulls: [{ source_id: "simbad", status: "none", matches: 0 }] }));
    const w = new RestWorkbench("http://api.test", s.fetch);
    expect(await w.pullReferences("inv 1", ["simbad", "gaia"], 5, "ada")).toEqual({ pulls: [{ source_id: "simbad", status: "none", matches: 0 }] });
    expect(s.sent).toEqual([{ url: "http://api.test/investigations/inv%201/references", body: { sources: ["simbad", "gaia"], radius_arcsec: 5, actor: "ada" } }]);
  });

  it("[INV-024] the fake keeps every pull with the candidate and refuses what the kernel refuses", async () => {
    const source = new InMemoryEvidenceSource();
    const w = new InMemoryWorkbench(source, () => "2026-10-31T10:00:00Z");
    const summary = await w.pullReferences("inv-000001", ["gaia", "simbad"], 5, "ada");
    expect(summary.pulls.map((p) => [p.source_id, p.status])).toEqual([["gaia", "found"], ["simbad", "none"]]);
    await w.pullReferences("inv-000001", ["gaia"], 2, "ada");
    const pulls = (await source.comparison("inv-000001"))?.pulls ?? [];
    expect(pulls.map((p) => [p.source_id, p.radius_arcsec, p.matches.length])).toEqual([["gaia", 5, 2], ["simbad", 5, 0], ["gaia", 2, 1]]);
    await expect(w.pullReferences("inv-000001", ["nowhere"], 5, "ada")).rejects.toThrow(CommandRefused);
    await expect(w.pullReferences("inv-000001", ["gaia"], 0, "ada")).rejects.toThrow(/radius/);
    await expect(w.pullReferences("inv-000001", ["gaia"], 5, " ")).rejects.toThrow(/who asked/);
    await expect(w.pullReferences("inv-missing", ["gaia"], 5, "ada")).rejects.toThrow(/no investigation/);
  });
});

<script lang="ts">
  import { enhance } from "$app/forms";
  import { invalidateAll } from "$app/navigation";
  import Badge from "$lib/components/Badge.svelte";
  import CopyCommand from "$lib/components/CopyCommand.svelte";
  import Journey from "$lib/components/Journey.svelte";
  import { fieldLabel, humanBytes, humanDuration, humanTime, needsSignature, planBadge, planProgress, runJourney, withinBudget } from "$lib/plain.ts";
  import { shortHash } from "$lib/view.ts";

  let { data, form } = $props();
  const p = $derived(data.plan);
  const doc = $derived(p.document);
  const budget = $derived(doc.constraints.budget);
  const archives = $derived(Object.keys(doc.cost.requests_per_source));
  const requests = $derived(Object.values(doc.cost.requests_per_source).reduce((a, b) => a + b, 0));
  const signed = $derived(needsSignature(p));
  const scaleMax = $derived(Math.max(budget.max_bytes, doc.cost.total_bytes));
  const pct = (n: number) => `${Math.min(100, (n / scaleMax) * 100)}%`;
  const unavailable = $derived(new Set(p.execution?.unavailable ?? []));
  const progress = $derived(planProgress(p));
  // A download takes minutes; the page reads the plan again until it has finished or stopped.
  $effect(() => {
    if (progress !== "running") return;
    const timer = setInterval(() => invalidateAll(), 4000);
    return () => clearInterval(timer);
  });
  // Typed once and kept across failed submissions; the server remembers it in a cookie.
  let actor = $state(data.actor);
  let reason = $state("");
  let busy = $state(false);
  const working = () => {
    busy = true;
    return async ({ update }: { update: (o?: { reset?: boolean }) => Promise<void> }) => {
      await update({ reset: false });
      busy = false;
    };
  };
</script>

<svelte:head><title>Download plan · SDP</title></svelte:head>

<p class="crumbs"><a href="/plans">Downloads</a> / <span class="mono">{shortHash(p.id)}</span></p>
<h1>Download plan for {fieldLabel(doc.intent.field)}</h1>
<p class="sub"><Badge {...planBadge(p)} /> <span class="mono muted">{shortHash(p.id, 16)}</span></p>

<div class="top">
  <section class="sheet" aria-labelledby="cost-h" data-testid="plan-cost">
    <h2 id="cost-h">What it costs</h2>
    <p class="cost">
      <strong>{humanBytes(doc.cost.total_bytes)}</strong> in {requests} request{requests === 1 ? "" : "s"} to
      {archives.length} archive{archives.length === 1 ? "" : "s"}, taking about <strong>{humanDuration(doc.cost.estimated_wall_seconds)}</strong>.
    </p>
    <div class="meter" role="img" aria-label="{humanBytes(doc.cost.total_bytes)} planned; approval needed above {humanBytes(budget.approval_threshold_bytes)}; budget {humanBytes(budget.max_bytes)}">
      <div class="fill" class:over={signed} style:width={pct(doc.cost.total_bytes)}></div>
      <div class="mark" style:left={pct(budget.approval_threshold_bytes)}></div>
    </div>
    <div class="legend">
      <span>Signature needed above {humanBytes(budget.approval_threshold_bytes)}</span>
      <span>Budget {humanBytes(budget.max_bytes)}</span>
    </div>
    {#if !withinBudget(p)}<p class="error-text">This plan is over its budget and will be refused.</p>{/if}
    {#if doc.cost.nas_headroom_after_bytes !== null}
      <p class="muted small">{humanBytes(doc.cost.nas_headroom_after_bytes)} of storage will be left afterwards.</p>
    {/if}
  </section>

  <section class="sheet decide" aria-labelledby="decide-h" data-testid="plan-decision">
    <h2 id="decide-h">Decision</h2>
    {#if form?.done}<p class="done" role="status" data-testid="action-done">{form.done}</p>{/if}
    {#if form?.error}<p class="error-text" role="alert" data-testid="action-error">{form.error}</p>{/if}

    {#if p.state === "drafted"}
      {#if signed}
        <p>
          This plan is over {humanBytes(budget.approval_threshold_bytes)}, so approving it is a signed step. Whoever holds a
          signing key runs:
        </p>
        <CopyCommand command="sdp plan approve {p.id} --actor <your-name>" />
      {/if}
      <form method="POST" use:enhance={working}>
        <label>Your name <input name="actor" autocomplete="name" bind:value={actor} aria-invalid={form?.field === "actor" ? true : undefined} /></label>
        {#if !signed}
          <p>It is within budget, so your approval is enough.</p>
          <button formaction="?/approve" disabled={busy}>Approve</button>
        {/if}
        <details class="refuse" open={signed || form?.field === "reason"}>
          <summary>Refuse this plan</summary>
          <label>Why <textarea name="reason" bind:value={reason} aria-invalid={form?.field === "reason" ? true : undefined}></textarea></label>
          <button class="danger" formaction="?/refuse" disabled={busy}>Refuse</button>
        </details>
      </form>
    {:else if progress === "running"}
      <div class="progress" role="status" aria-live="polite" data-testid="run-progress">
        <p><strong>Downloading and checking.</strong> Started {humanTime(p.run?.started_at)}, about {humanDuration(doc.cost.estimated_wall_seconds)} in all.</p>
        <Journey steps={runJourney(data.stages)} label="How far the download has got" stacked />
        <p class="muted small">You can leave this page; the download carries on, and this page catches up when you come back.</p>
      </div>
    {:else if p.state === "approved" || p.state === "auto_approved"}
      {#if progress === "stopped"}
        <div data-testid="run-stopped">
          <p class="error-text">The download stopped before it finished.</p>
          <Journey steps={runJourney(data.stages, true)} label="Where the download stopped" stacked />
          <details>
            <summary>What went wrong</summary>
            <p class="mono small">{p.run?.error}</p>
          </details>
          <p>Archives are public services and are sometimes busy. Starting again is safe: the plan stays approved, and nothing outside it is fetched.</p>
        </div>
      {:else}
        <p>Approved by {doc.approval?.actor}. Nothing has been downloaded yet.</p>
      {/if}
      <form method="POST" action="?/execute" use:enhance={working}>
        <button disabled={busy}>{busy ? "Starting…" : progress === "stopped" ? "Start again" : "Download now"}</button>
        <p class="muted small">It runs on its own once started, about {humanDuration(doc.cost.estimated_wall_seconds)}.</p>
      </form>
    {:else if p.state === "refused"}
      <p>Refused by {doc.approval?.actor}: {doc.approval?.reason}</p>
    {:else if p.execution}
      <p data-testid="execution">
        Fetched {p.execution.acquired.length} of {doc.items.length} item{doc.items.length === 1 ? "" : "s"}.
        {#if p.execution.unavailable.length}{p.execution.unavailable.length} could not be fetched; they are marked below, and the tests that needed them say so.{/if}
      </p>
      {#if p.setId}<a class="button secondary" href="/investigations/{p.setId}">See the candidates</a>{/if}
    {/if}
  </section>
</div>

<section aria-labelledby="items-h">
  <h2 id="items-h">What it will fetch</h2>
  <div class="table-wrap">
    <table data-testid="plan-items">
      <thead><tr><th>From</th><th>What</th><th>Size</th><th class="why">Why</th></tr></thead>
      <tbody>
        {#each doc.items as item (item.item_id)}
          <tr class:missing={unavailable.has(item.item_id)}>
            <td class="mono">{item.source_id}</td>
            <td>
              <span class="op">{item.operation.replace("_", " ")}</span>: <span class="target">{item.target}</span>
              <div class="why-inline muted small">for the {item.test_kind} test</div>
              {#if unavailable.has(item.item_id)}<div><Badge tone="bad" label="Could not be fetched" /></div>{/if}
            </td>
            <td class="num">{humanBytes(item.expected_bytes)}{#if item.expected_rows !== null}<div class="muted small">{item.expected_rows} rows</div>{/if}</td>
            <td class="why">for the <span class="mono">{item.test_kind}</span> test<div class="muted small">{item.licence}</div></td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
</section>

{#if doc.gaps.length}
  <section aria-labelledby="gaps-h">
    <h2 id="gaps-h">What no archive could supply</h2>
    <ul data-testid="plan-gaps">
      {#each doc.gaps as g (g.requirement_id)}<li>{g.reason} <span class="mono muted small">{g.requirement_id}</span></li>{/each}
    </ul>
    <p class="muted">Tests that need these will report the evidence as unavailable rather than guess.</p>
  </section>
{/if}

<section aria-labelledby="details-h" class="details">
  <h2 id="details-h">Details for reviewers</h2>
  <details>
    <summary>Which archives were considered</summary>
    <div class="table-wrap">
      <table data-testid="plan-discovery">
        <thead><tr><th>Archive</th><th>For</th><th>Match</th><th>Reason</th><th>Checked</th></tr></thead>
        <tbody>
          {#each doc.discovery as m (m.source_id + m.requirement_id)}
            <tr>
              <td class="mono">{m.source_id}</td>
              <td class="mono">{m.requirement_id}</td>
              <td><Badge tone={m.status === "satisfied" ? "good" : m.status === "partial" ? "warn" : "neutral"} label={m.status} /></td>
              <td>{m.reasons.map((r) => r.reason).join("; ")}</td>
              <td class="muted">{m.descriptor_verified_on}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  </details>
  <details>
    <summary>The plan's identity</summary>
    <dl>
      <dt>Plan id</dt><dd class="mono">{p.id}</dd>
      <dt>Planner</dt><dd class="mono">{doc.intent.planner_id}@{doc.intent.planner_version}</dd>
      <dt>Profile hash</dt><dd class="mono">{shortHash(doc.intent.profile_hash, 16)}</dd>
      {#if doc.approval?.ledger_entry_id}<dt>Ledger entry</dt><dd class="mono">{doc.approval.ledger_entry_id}</dd>{/if}
    </dl>
  </details>
</section>

<style>
  .sub { display: flex; gap: 0.5rem; align-items: center; flex-wrap: wrap; margin: 0 0 1.2rem; }
  .top { display: grid; grid-template-columns: minmax(0, 1.3fr) minmax(0, 1fr); gap: 1rem; align-items: start; }
  .top h2 { margin-top: 0; font-size: 1.05rem; color: var(--muted); font-weight: 600; }
  .cost { font-size: 1.1rem; }
  .meter { position: relative; height: 0.9rem; background: var(--sunken); border: 1px solid var(--line); border-radius: 999px; margin: 1rem 0 0.4rem; }
  .fill { position: absolute; inset: 0 auto 0 0; background: var(--accent); border-radius: 999px; min-width: 0.5rem; }
  .fill.over { background: var(--warn); }
  .mark { position: absolute; top: -0.35rem; bottom: -0.35rem; width: 2px; margin-left: -1px; background: var(--text); }
  .legend { display: flex; justify-content: space-between; gap: 1rem; flex-wrap: wrap; font-size: 0.85rem; color: var(--muted); }
  .decide form { display: grid; gap: 0.8rem; }
  .decide label { display: flex; flex-direction: column; gap: 0.3rem; }
  .decide button { justify-self: start; }
  .refuse { display: grid; gap: 0.6rem; }
  .refuse[open] { padding-top: 0.2rem; }
  .refuse label { margin: 0.6rem 0; }
  .done { color: var(--good); font-weight: 600; }
  .small { font-size: 0.85rem; }
  .progress { display: grid; gap: 0.8rem; }
  .progress p { margin: 0; }
  .op { text-transform: capitalize; font-weight: 600; }
  .target { overflow-wrap: anywhere; }
  tr.missing td { background: color-mix(in srgb, var(--bad) 6%, transparent); }
  .details details { background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); padding: 0.8rem 1rem; margin-bottom: 0.6rem; }
  .details details[open] summary { margin-bottom: 0.8rem; }
  dl { display: grid; grid-template-columns: max-content 1fr; gap: 0.45rem 1.2rem; margin: 0; }
  dt { color: var(--muted); }
  dd { margin: 0; overflow-wrap: anywhere; }
  .why-inline { display: none; }
  @media (max-width: 800px) {
    .top { grid-template-columns: 1fr; }
    dl { grid-template-columns: 1fr; }
  }
  @media (max-width: 640px) {
    .why { display: none; }
    .why-inline { display: block; }
  }
</style>

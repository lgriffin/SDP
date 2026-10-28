<script lang="ts">
  import Badge from "$lib/components/Badge.svelte";
  import { decisiveChecks, describeGate, formatNumber, orderChecks, shortHash, stateTone, verdictTone } from "$lib/view.ts";

  let { data } = $props();
  const e = $derived(data.experiment);
  const checks = $derived(orderChecks(e.checks));
  const decisive = $derived(e.verdict ? new Set(decisiveChecks(e.checks, e.verdict.status).map((c) => c.gateId)) : new Set<string>());
  const failure = $derived(e.failure as { task_id?: string; reason?: string; diagnostics?: { traceback?: string } } | null);
</script>

<svelte:head><title>{e.id} · SDP evidence</title></svelte:head>

<p class="muted"><a href="/">Experiments</a> / <span class="mono">{e.id}</span></p>
<h1 class="mono">{e.id}</h1>
<p>
  {e.protocol} <span class="muted">{e.protocolVersion}</span> ·
  <Badge tone={stateTone(e.state)} label={e.state} />
  {#if e.verdict}<Badge tone={verdictTone(e.verdict.status)} label={e.verdict.status} />{/if}
</p>

<section aria-labelledby="verdict">
  <h2 id="verdict">Verdict</h2>
  {#if e.verdict}
    <p data-testid="verdict">
      <strong>{e.verdict.status}</strong> under protocol {e.verdict.protocolVersion}.
      {#if e.verdict.reasons.length}<span class="muted">{e.verdict.reasons.join(" ")}</span>{/if}
    </p>
  {:else if failure}
    <div class="failure" data-testid="failure">
      <p><strong>Failed</strong> in task <code>{failure.task_id}</code>: {failure.reason}</p>
      {#if failure.diagnostics?.traceback}
        <details><summary>Traceback</summary><pre>{failure.diagnostics.traceback}</pre></details>
      {/if}
    </div>
  {:else if e.refusal}
    <p data-testid="refusal"><strong>Refused:</strong> {e.refusal}</p>
  {:else}
    <p class="muted" data-testid="no-verdict">No verdict yet: the experiment is {e.state}.</p>
  {/if}
</section>

{#if checks.length}
  <section aria-labelledby="gates">
    <h2 id="gates">Predeclared gates</h2>
    <p class="muted">Every gate frozen before the run, its finding and outcome. Highlighted rows decided the verdict.</p>
    <div class="table-wrap">
      <table data-testid="gates">
        <thead>
          <tr><th>Gate</th><th>Criterion</th><th>Value</th><th>Severity</th><th>Oracles</th><th>Outcome</th></tr>
        </thead>
        <tbody>
          {#each checks as c (c.gateId)}
            <tr class:decisive={decisive.has(c.gateId) && e.verdict?.status !== "VERIFIED"} data-gate={c.gateId}>
              <td class="mono">{c.gateId}</td>
              <td>{describeGate(c)}</td>
              <td class="mono">{formatNumber(c.value)}{c.unit ? ` ${c.unit}` : ""}</td>
              <td>{c.severity}</td>
              <td>
                {#if c.oracleIds.length}
                  {c.oracleIds.join(", ")} <span class="muted">{c.oraclesVerified ? "verified" : "unverified"}</span>
                {:else}<span class="muted">—</span>{/if}
              </td>
              <td><Badge tone={c.outcome === "pass" ? "good" : c.outcome === "fail" ? "bad" : "warn"} label={c.outcome} /></td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  </section>
{/if}

{#each e.dossiers as d (d.id)}
  <section aria-labelledby="dossier-{d.id}" data-testid="dossier">
    <h2 id="dossier-{d.id}">Dossier <span class="mono">{d.id}</span></h2>
    <h3>Limitations</h3>
    <ul>
      {#each d.limitations as l, i (i)}<li>{l}</li>{/each}
    </ul>
    {#if Object.keys((d.sections as Record<string, string>) ?? {}).length}
      {#each Object.entries(d.sections as Record<string, string>) as [title, body] (title)}
        <h3>{title}</h3>
        <p>{body}</p>
      {/each}
    {/if}
  </section>
{/each}

<section aria-labelledby="tasks">
  <h2 id="tasks">Tasks</h2>
  <div class="table-wrap">
    <table data-testid="tasks">
      <thead><tr><th>Task</th><th>Kind</th><th>Depends on</th><th>Status</th><th>Duration</th><th>Checkpoint</th></tr></thead>
      <tbody>
        {#each e.tasks as t (t.taskId)}
          <tr>
            <td class="mono">{t.taskId}</td>
            <td class="mono">{t.taskKind}</td>
            <td class="mono">{t.dependsOn.join(", ") || "—"}</td>
            <td>{t.status}</td>
            <td class="mono">{t.durationS == null ? "—" : `${formatNumber(t.durationS)} s`}</td>
            <td class="mono" title={t.checkpoint ?? ""}>{shortHash(t.checkpoint)}</td>
          </tr>
        {:else}
          <tr><td colspan="6" class="muted">No tasks have run.</td></tr>
        {/each}
      </tbody>
    </table>
  </div>
</section>

<section aria-labelledby="provenance">
  <h2 id="provenance">Provenance</h2>
  <dl>
    <dt>Predeclaration</dt><dd class="mono" title={e.predeclarationHash}>{shortHash(e.predeclarationHash, 16)}</dd>
    <dt>Seed</dt><dd class="mono">{e.seed}</dd>
    <dt>Backend</dt><dd>{e.backend}</dd>
    <dt>Inputs</dt>
    <dd>
      <ul class="plain">
        {#each Object.entries(e.inputs as Record<string, string>) as [role, artifact] (role)}
          <li><span class="mono">{role}</span> = <span class="mono" title={artifact}>{shortHash(artifact)}</span></li>
        {/each}
      </ul>
    </dd>
    <dt>Parameters</dt><dd><code>{JSON.stringify(e.parameters)}</code></dd>
    <dt>Lineage</dt>
    <dd data-testid="lineage">
      {#if data.lineage.length}
        <ul class="plain">{#each data.lineage as node (node)}<li class="mono" title={node}>{shortHash(node, 16)}</li>{/each}</ul>
      {:else}<span class="muted">No recorded ancestors.</span>{/if}
    </dd>
    {#if e.manifestId}
      <dt>Manifest</dt>
      <dd><a href="/experiments/{e.id}/manifest.json" class="mono" data-testid="manifest-link">{shortHash(e.manifestId, 16)}</a></dd>
    {/if}
    {#if e.bundleDoi}<dt>DOI</dt><dd><a href="https://doi.org/{e.bundleDoi}">{e.bundleDoi}</a></dd>{/if}
  </dl>
</section>

<style>
  tr.decisive td { background: color-mix(in srgb, var(--bad) 10%, transparent); }
  dl { display: grid; grid-template-columns: max-content 1fr; gap: 0.4rem 1rem; margin: 0; }
  dt { color: var(--muted); }
  dd { margin: 0; overflow-wrap: anywhere; }
  .plain { list-style: none; padding: 0; margin: 0; }
  .failure pre { overflow-x: auto; background: var(--surface); border: 1px solid var(--line); padding: 0.6rem; font-size: 0.8rem; }
  h3 { font-size: 0.95rem; margin: 1rem 0 0.3rem; }
</style>

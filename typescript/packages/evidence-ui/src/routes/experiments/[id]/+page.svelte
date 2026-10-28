<script lang="ts">
  import { enhance } from "$app/forms";
  import Badge from "$lib/components/Badge.svelte";
  import CopyCommand from "$lib/components/CopyCommand.svelte";
  import GateRuler from "$lib/components/GateRuler.svelte";
  import Journey from "$lib/components/Journey.svelte";
  import {
    experimentJourney,
    experimentNextStep,
    experimentStateLabel,
    gateSentence,
    humanTime,
    outcomeLabel,
    outcomeTone,
    programmeName,
    verdictSentence,
    verdictTitle,
  } from "$lib/plain.ts";
  import { decisiveChecks, formatNumber, orderChecks, shortHash, stateTone, verdictTone } from "$lib/view.ts";

  let { data, form } = $props();
  const e = $derived(data.experiment);
  const checks = $derived(orderChecks(e.checks));
  const decisive = $derived(e.verdict ? new Set(decisiveChecks(e.checks, e.verdict.status).map((c) => c.gateId)) : new Set<string>());
  const failure = $derived(e.failure as { task_id?: string; reason?: string; diagnostics?: { traceback?: string } } | null);
  const step = $derived(experimentNextStep(e));
  const limitations = $derived([...new Set([...e.limitations, ...e.dossiers.flatMap((d) => d.limitations)])]);
  let busy = $state(false);
  const working = () => {
    busy = true;
    return async ({ update }: { update: () => Promise<void> }) => {
      await update();
      busy = false;
    };
  };
</script>

<svelte:head><title>{programmeName(e.protocol)} {e.id} · SDP</title></svelte:head>

<p class="crumbs"><a href="/experiments">Results</a> / <span class="mono">{e.id}</span></p>
<h1>{programmeName(e.protocol)}</h1>
<p class="sub">
  <span class="mono">{e.id}</span> · protocol version {e.protocolVersion} ·
  <Badge tone={stateTone(e.state)} label={experimentStateLabel(e.state)} />
</p>
<div class="journey-wrap"><Journey steps={experimentJourney(e.state)} /></div>

<div class="top">
  <section class="sheet verdict-panel {e.verdict ? verdictTone(e.verdict.status) : failure ? 'bad' : ''}" aria-labelledby="verdict">
    <h2 id="verdict">What it found</h2>
    {#if e.verdict}
      <div data-testid="verdict">
        <p class="verdict-title"><Badge tone={verdictTone(e.verdict.status)} label={verdictTitle(e.verdict.status)} /> <span class="visually-hidden">{e.verdict.status}</span></p>
        <p>{verdictSentence(e.verdict.status)}</p>
        {#if e.verdict.reasons.length}<p class="muted">The kernel's reason: {e.verdict.reasons.join(" ")}</p>{/if}
      </div>
    {:else if failure}
      <div class="failure" data-testid="failure">
        <p><strong>The run stopped</strong> in the step <code>{failure.task_id}</code>: {failure.reason}</p>
        <p class="muted">Finished steps are kept. Fix the cause, then run it again; it picks up where it stopped.</p>
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

  <section class="sheet next" aria-labelledby="next" data-testid="next-step">
    <h2 id="next">Next step</h2>
    {#if form?.done}<p class="done" role="status" data-testid="action-done">{form.done}</p>{/if}
    {#if form?.error}<p class="error-text" role="alert" data-testid="action-error">{form.error}</p>{/if}
    {#if step.kind === "run" || step.kind === "validate"}
      <p>{step.explanation}</p>
      <form method="POST" action="?/{step.kind}" use:enhance={working}>
        <button disabled={busy}>{busy ? "Working…" : step.label}</button>
      </form>
    {:else if step.kind === "copy" && step.command}
      <p><strong>{step.label}.</strong> {step.explanation}</p>
      <CopyCommand command={step.command} />
    {:else}
      <p class="muted">{step.label}. {step.explanation}</p>
    {/if}
  </section>
</div>

{#if checks.length}
  <section aria-labelledby="gates">
    <h2 id="gates">The checks, frozen before the run</h2>
    <p class="muted">Each check compares a measurement with a limit written down in advance. The shaded part of each scale is where the check passes.</p>
    <ol class="gates" data-testid="gates">
      {#each checks as c (c.gateId)}
        {@const decided = decisive.has(c.gateId) && e.verdict?.status !== "VERIFIED"}
        <li class:decisive={decided} data-gate={c.gateId}>
          <div class="gate-head">
            <div>
              <div class="gate-name">{gateSentence(c, formatNumber)}</div>
              <div class="muted small">
                <span class="mono">{c.gateId}</span> · {c.severity === "blocking" ? "Required" : "Advisory"}
                {#if c.oracleIds.length} · reference values {c.oraclesVerified ? "verified" : "not yet verified"}{/if}
              </div>
            </div>
            <Badge tone={outcomeTone(c.outcome)} label={outcomeLabel(c.outcome)} />
          </div>
          <GateRuler check={c} />
          {#if decided}<p class="decided">This check decided the verdict.</p>{/if}
        </li>
      {/each}
    </ol>
  </section>
{/if}

{#if limitations.length}
  <section aria-labelledby="limits" data-testid="dossier">
    <h2 id="limits">What this result cannot tell you</h2>
    <ul>{#each limitations as l (l)}<li>{l}</li>{/each}</ul>
    {#each e.dossiers as d (d.id)}
      {#each Object.entries((d.sections as Record<string, string>) ?? {}) as [title, body] (title)}
        <h3>{title}</h3>
        <p>{body}</p>
      {/each}
    {/each}
  </section>
{/if}

<section aria-labelledby="details-h" class="details">
  <h2 id="details-h">Details for reviewers</h2>
  <details>
    <summary>Steps of the run ({e.tasks.length})</summary>
    <div class="table-wrap">
      <table data-testid="tasks">
        <thead><tr><th>Step</th><th>Kind</th><th>Needs</th><th>Status</th><th>Took</th><th>Checkpoint</th></tr></thead>
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
            <tr><td colspan="6" class="muted">No steps have run.</td></tr>
          {/each}
        </tbody>
      </table>
    </div>
  </details>
  <details>
    <summary>Where the result came from</summary>
    <dl>
      <dt>Started</dt><dd>{humanTime(e.startedAt)}</dd>
      <dt>Finished</dt><dd>{humanTime(e.finishedAt)}</dd>
      <dt>Predeclaration</dt><dd class="mono" title={e.predeclarationHash}>{shortHash(e.predeclarationHash, 16)}</dd>
      <dt>Random seed</dt><dd class="mono">{e.seed}</dd>
      <dt>Computed on</dt><dd>{e.backend.toUpperCase()}</dd>
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
        <dd><a href="/experiments/{e.id}/manifest.json" class="mono" data-testid="manifest-link">Download {shortHash(e.manifestId, 16)}</a></dd>
      {/if}
      {#if e.bundleDoi}<dt>DOI</dt><dd><a href="https://doi.org/{e.bundleDoi}">{e.bundleDoi}</a></dd>{/if}
    </dl>
  </details>
</section>

<style>
  .sub { color: var(--muted); display: flex; flex-wrap: wrap; gap: 0.4rem; align-items: center; margin: 0 0 0.9rem; }
  .journey-wrap { margin-bottom: 1.6rem; }
  .top { display: grid; grid-template-columns: minmax(0, 1.6fr) minmax(0, 1fr); gap: 1rem; align-items: start; }
  .top h2 { margin-top: 0; font-size: 1.05rem; color: var(--muted); font-weight: 600; }
  .verdict-panel.good { background: color-mix(in srgb, var(--good) 6%, var(--surface)); }
  .verdict-panel.bad { background: color-mix(in srgb, var(--bad) 6%, var(--surface)); }
  .verdict-panel.warn { background: color-mix(in srgb, var(--warn) 7%, var(--surface)); }
  .verdict-title { font-size: 1.2rem; margin: 0 0 0.5rem; }
  .verdict-title :global(.badge) { font-size: 1.05rem; padding: 0.25rem 0.9rem 0.25rem 0.7rem; }
  .done { color: var(--good); font-weight: 600; }
  .gates { list-style: none; padding: 0; margin: 0; display: grid; gap: 0.75rem; }
  .gates li { background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); padding: 0.9rem 1.1rem 0.4rem; }
  .gates li.decisive { border-color: color-mix(in srgb, var(--bad) 55%, var(--line)); box-shadow: 0 0 0 3px color-mix(in srgb, var(--bad) 12%, transparent); }
  .gate-head { display: flex; justify-content: space-between; gap: 1rem; align-items: flex-start; flex-wrap: wrap; }
  .gate-name { font-weight: 650; }
  .small { font-size: 0.88rem; }
  .decided { color: var(--bad); font-weight: 600; font-size: 0.92rem; margin: 0 0 0.5rem; }
  .details details { background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); padding: 0.8rem 1rem; margin-bottom: 0.6rem; }
  .details details[open] summary { margin-bottom: 0.8rem; }
  dl { display: grid; grid-template-columns: max-content 1fr; gap: 0.45rem 1.2rem; margin: 0; }
  dt { color: var(--muted); }
  dd { margin: 0; overflow-wrap: anywhere; }
  .plain { list-style: none; padding: 0; margin: 0; }
  .failure pre { overflow-x: auto; background: var(--sunken); border: 1px solid var(--line); border-radius: 8px; padding: 0.6rem; font-size: 0.8rem; }
  .visually-hidden { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
  @media (max-width: 800px) {
    .top { grid-template-columns: 1fr; }
    dl { grid-template-columns: 1fr; }
    dd { margin-bottom: 0.4rem; }
  }
</style>

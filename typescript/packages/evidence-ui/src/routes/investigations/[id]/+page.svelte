<script lang="ts">
  import Badge from "$lib/components/Badge.svelte";
  import Journey from "$lib/components/Journey.svelte";
  import { candidateStateLabel, fieldLabel, humanTime, openCandidates, planHref, planStateLabel, setJourney } from "$lib/plain.ts";
  import { formatNumber, formatPercent, investigationTone, rowCapReached, shortHash, stageTone, type NullResultDoc } from "$lib/view.ts";

  let { data } = $props();
  const s = $derived(data.set);
  const nulls = $derived((s.nullResults ?? []) as NullResultDoc[]);
  const field = $derived(s.field as { region?: Record<string, unknown>; epoch_jyear?: number; label?: string });
  const tests = $derived((s.testsApplied ?? []) as { name: string; version: string; question?: string }[]);
  const open = $derived(openCandidates(s.investigations));
  const filters = [
    { key: "all", label: "All" },
    { key: "open", label: "To review" },
    { key: "parked", label: "Parked" },
    { key: "closed", label: "Closed" },
  ];
  const shown = $derived(data.show === "all" ? s.investigations : s.investigations.filter((i) => i.state === data.show));
  const cutOff = $derived(rowCapReached(s.stages ?? []));
  const count = (key: string) => (key === "all" ? s.investigations.length : s.investigations.filter((i) => i.state === key).length);
</script>

<svelte:head><title>{s.id} · SDP</title></svelte:head>

<p class="crumbs"><a href="/investigations">Candidates</a> / <span class="mono">{s.id}</span></p>
<h1>Candidates in {fieldLabel(s.field)}</h1>
<p class="sub">
  <span class="mono">{s.id}</span> · tests from {s.profileId} v{s.profileVersion} · started {humanTime(s.startedAt)}
  {#if s.complete}<Badge tone="good" label="Complete" />{:else}<Badge tone="neutral" label="Incomplete" />{/if}
</p>
<div class="journey-wrap"><Journey steps={setJourney(s, data.plan, open)} /></div>

<p class="notice" data-testid="exploratory-set">
  <Badge tone="warn" label={s.label === "EXPLORATORY" ? "Exploratory" : s.label} />
  These candidates came from looking at the data, so none of them is a finding yet. Use them to decide what is worth a proper test.
</p>

{#if cutOff.length}
  <p class="notice" role="alert" data-testid="row-cap-reached">
    <Badge tone="warn" label="Only part of the field" />
    {#each cutOff as [source, cap], i (source)}{i ? "; " : ""}{source} had at least {formatNumber(cap)} rows here{/each}, so
    only part of the field was read. Choose a smaller region, or raise <code>row_cap</code> in a copy of the profile.
  </p>
{/if}

<section aria-labelledby="candidates-h">
  <h2 id="candidates-h">Candidates, strongest first</h2>
  <nav class="filters" aria-label="Show candidates">
    {#each filters as f (f.key)}
      <a href="?show={f.key}" aria-current={data.show === f.key ? "true" : undefined}>{f.label} <span class="num">({count(f.key)})</span></a>
    {/each}
  </nav>
  <div class="table-wrap">
    <table data-testid="candidates">
      <thead><tr><th>Candidate</th><th>Score</th><th>Status</th><th><span class="visually-hidden">Action</span></th></tr></thead>
      <tbody>
        {#each shown as i (i.id)}
          <tr>
            <td><a href="/investigations/{s.id}/{i.id}" class="mono">{i.candidateKey}</a><div class="mono muted small">{i.id}</div></td>
            <td class="num">{formatNumber(i.score)} <span class="muted small policy">{i.rankingPolicy}</span></td>
            <td><Badge tone={investigationTone(i.state)} label={candidateStateLabel(i.state)} /><span class="visually-hidden"> ({i.state})</span></td>
            <td class="go"><a class="button secondary" href="/investigations/{s.id}/{i.id}">{i.state === "open" ? "Review" : "Open"}</a></td>
          </tr>
        {:else}
          <tr><td colspan="4" class="muted">No candidates here.</td></tr>
        {/each}
      </tbody>
    </table>
  </div>
</section>

<section aria-labelledby="nulls-h">
  <h2 id="nulls-h">How often the tests fire by chance</h2>
  <p class="muted">
    Each test was also run on shuffled or synthetic versions of this field, where nothing real should turn up. If a
    test fires often there, treat its candidates with more caution.
  </p>
  {#each nulls as n (n.test_name + n.test_version)}
    <p class="chance" data-testid="chance">
      On {n.trials} null fields ({n.null_model}), <strong class="mono">{n.test_name}</strong>
      fired for <strong>{formatPercent(n.false_positive_rate)}</strong> of objects ({n.false_positives} of {n.examined}) at α = {formatNumber(n.alpha)}.
    </p>
  {/each}
  <details>
    <summary>The numbers</summary>
    <div class="table-wrap">
      <table data-testid="nulls">
        <thead><tr><th>Test</th><th>Null model</th><th>Trials</th><th>Examined</th><th>Fired</th><th>Rate at alpha</th></tr></thead>
        <tbody>
          {#each nulls as n (n.test_name + n.test_version)}
            <tr>
              <td class="mono">{n.test_name}@{n.test_version}</td>
              <td>{n.null_model}</td>
              <td class="num">{n.trials}</td>
              <td class="num">{n.examined}</td>
              <td class="num">{n.false_positives}</td>
              <td class="num">{formatPercent(n.false_positive_rate)} <span class="muted">α = {formatNumber(n.alpha)}</span></td>
            </tr>
          {:else}
            <tr><td colspan="6" class="muted">No null fields were run.</td></tr>
          {/each}
        </tbody>
      </table>
    </div>
  </details>
</section>

<section aria-labelledby="details-h" class="details">
  <h2 id="details-h">Details for reviewers</h2>
  <details>
    <summary>Stages of the search</summary>
    <div class="table-wrap">
      <table data-testid="stages">
        <thead><tr><th>Stage</th><th>State</th><th>Summary</th><th>Artifact</th><th>Finished</th></tr></thead>
        <tbody>
          {#each s.stages as st (st.stage)}
            <tr data-stage={st.stage}>
              <td>{st.stage}</td>
              <td><Badge tone={stageTone(st.state)} label={st.state} /></td>
              <td>
                {#if st.error}<span class="error-text">{st.error}</span>
                {:else}
                  {#each Object.entries((st.summary ?? {}) as Record<string, unknown>) as [k, v] (k)}
                    <span class="kv"><span class="muted">{k}</span> {formatNumber(v)}</span>
                  {/each}
                {/if}
              </td>
              <td class="mono" title={st.artifactId ?? ""}>{shortHash(st.artifactId)}</td>
              <td class="muted">{humanTime(st.finishedAt)}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  </details>
  <details>
    <summary>Where the candidates came from</summary>
    <dl>
      <dt>Field</dt><dd><span class="mono">{s.fieldId}</span> {field.label ?? ""} <code>{JSON.stringify(field.region)}</code> <span class="muted">epoch J{field.epoch_jyear}</span></dd>
      {#if data.plan}<dt>Download plan</dt><dd><a href={planHref(data.plan.id)}>{planStateLabel(data.plan.state)}</a></dd>{/if}
      <dt>Profile hash</dt><dd class="mono" title={s.profileHash}>{shortHash(s.profileHash, 16)}</dd>
      <dt>Random seed</dt><dd class="mono">{s.seed}</dd>
      <dt>Objects examined</dt><dd class="num" data-testid="examined">{s.objectsExamined}</dd>
      <dt>Tests applied</dt>
      <dd><ul class="plain">{#each tests as t (t.name)}<li><span class="mono">{t.name}@{t.version}</span> {#if t.question}<span class="muted">{t.question}</span>{/if}</li>{/each}</ul></dd>
      <dt>Archive releases</dt><dd><ul class="plain">{#each s.archiveReleases as r (r)}<li class="mono">{r}</li>{/each}</ul></dd>
      {#if s.manifestId}<dt>Manifest</dt><dd class="mono" title={s.manifestId}>{shortHash(s.manifestId, 16)}</dd>{/if}
    </dl>
  </details>
</section>

<style>
  .sub { color: var(--muted); display: flex; flex-wrap: wrap; gap: 0.4rem; align-items: center; margin: 0 0 0.9rem; }
  .journey-wrap { margin-bottom: 1.2rem; }
  .notice { background: color-mix(in srgb, var(--warn) 9%, var(--surface)); border: 1px solid color-mix(in srgb, var(--warn) 30%, var(--line)); border-radius: var(--radius); padding: 0.75rem 1rem; max-width: none; }
  .filters { display: flex; gap: 0.3rem; flex-wrap: wrap; margin-bottom: 0.7rem; }
  .filters a { padding: 0.35rem 0.8rem; border-radius: 999px; border: 1px solid var(--line); background: var(--surface); text-decoration: none; color: var(--text); }
  .filters a[aria-current="true"] { background: var(--accent); border-color: var(--accent); color: var(--on-accent); font-weight: 600; }
  .small { font-size: 0.85rem; }
  .go { text-align: right; }
  @media (max-width: 640px) {
    .policy, .go, th:last-child { display: none; }
  }
  .chance { background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); padding: 0.75rem 1rem; }
  .details details { background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); padding: 0.8rem 1rem; margin-bottom: 0.6rem; }
  .details details[open] summary { margin-bottom: 0.8rem; }
  dl { display: grid; grid-template-columns: max-content 1fr; gap: 0.45rem 1.2rem; margin: 0; }
  dt { color: var(--muted); }
  dd { margin: 0; overflow-wrap: anywhere; }
  .plain { list-style: none; padding: 0; margin: 0; }
  .kv { margin-right: 0.8rem; white-space: nowrap; }
  .visually-hidden { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
  @media (max-width: 700px) {
    dl { grid-template-columns: 1fr; }
  }
</style>

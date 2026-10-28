<script lang="ts">
  import Badge from "$lib/components/Badge.svelte";
  import { formatNumber, formatPercent, investigationTone, shortHash, stageTone, type NullResultDoc } from "$lib/view.ts";

  let { data } = $props();
  const s = $derived(data.set);
  const nulls = $derived((s.nullResults ?? []) as NullResultDoc[]);
  const field = $derived(s.field as { region?: Record<string, unknown>; epoch_jyear?: number; label?: string });
  const tests = $derived((s.testsApplied ?? []) as { name: string; version: string; question?: string }[]);
</script>

<svelte:head><title>{s.id} · SDP evidence</title></svelte:head>

<p class="muted"><a href="/investigations">Investigations</a> / <span class="mono">{s.id}</span></p>
<h1 class="mono">{s.id}</h1>
<p>
  {s.profileId} <span class="muted">{s.profileVersion}</span> ·
  <Badge tone="warn" label={s.label} />
  {#if s.complete}<Badge tone="good" label="complete" />{:else}<Badge tone="neutral" label="incomplete" />{/if}
</p>

<section aria-labelledby="stages">
  <h2 id="stages">Stages</h2>
  <div class="table-wrap">
    <table data-testid="stages">
      <thead><tr><th>Stage</th><th>State</th><th>Summary</th><th>Artifact</th><th>Finished</th></tr></thead>
      <tbody>
        {#each s.stages as st (st.stage)}
          <tr data-stage={st.stage}>
            <td>{st.stage}</td>
            <td><Badge tone={stageTone(st.state)} label={st.state} /></td>
            <td>
              {#if st.error}<span class="error">{st.error}</span>
              {:else}
                {#each Object.entries((st.summary ?? {}) as Record<string, unknown>) as [k, v] (k)}
                  <span class="kv"><span class="muted">{k}</span> {formatNumber(v)}</span>
                {/each}
              {/if}
            </td>
            <td class="mono" title={st.artifactId ?? ""}>{shortHash(st.artifactId)}</td>
            <td class="muted">{st.finishedAt ?? "—"}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
</section>

<section aria-labelledby="candidates">
  <h2 id="candidates">Candidates</h2>
  <p class="muted">One investigation per candidate, highest score first. Triage happens from the CLI or the notebook.</p>
  <div class="table-wrap">
    <table data-testid="candidates">
      <thead><tr><th>Investigation</th><th>Candidate</th><th>Score</th><th>State</th></tr></thead>
      <tbody>
        {#each s.investigations as i (i.id)}
          <tr>
            <td><a href="/investigations/{s.id}/{i.id}" class="mono">{i.id}</a></td>
            <td class="mono">{i.candidateKey}</td>
            <td class="mono">{formatNumber(i.score)} <span class="muted">{i.rankingPolicy}</span></td>
            <td><Badge tone={investigationTone(i.state)} label={i.state} /></td>
          </tr>
        {:else}
          <tr><td colspan="4" class="muted">No candidates were opened.</td></tr>
        {/each}
      </tbody>
    </table>
  </div>
</section>

<section aria-labelledby="nulls">
  <h2 id="nulls">Null fields</h2>
  <p class="muted">How often each test fires on shuffled or synthetic versions of this field: the set's false-positive rate.</p>
  <div class="table-wrap">
    <table data-testid="nulls">
      <thead><tr><th>Test</th><th>Null model</th><th>Trials</th><th>Examined</th><th>False positives</th><th>Rate at alpha</th></tr></thead>
      <tbody>
        {#each nulls as n (n.test_name + n.test_version)}
          <tr>
            <td class="mono">{n.test_name}@{n.test_version}</td>
            <td>{n.null_model}</td>
            <td class="mono">{n.trials}</td>
            <td class="mono">{n.examined}</td>
            <td class="mono">{n.false_positives}</td>
            <td class="mono">{formatPercent(n.false_positive_rate)} <span class="muted">α = {formatNumber(n.alpha)}</span></td>
          </tr>
        {:else}
          <tr><td colspan="6" class="muted">No null fields were run.</td></tr>
        {/each}
      </tbody>
    </table>
  </div>
</section>

<section aria-labelledby="provenance">
  <h2 id="provenance">Provenance</h2>
  <dl>
    <dt>Field</dt><dd><span class="mono">{s.fieldId}</span> {field.label ?? ""} <code>{JSON.stringify(field.region)}</code> <span class="muted">epoch J{field.epoch_jyear}</span></dd>
    <dt>Profile hash</dt><dd class="mono" title={s.profileHash}>{shortHash(s.profileHash, 16)}</dd>
    <dt>Seed</dt><dd class="mono">{s.seed}</dd>
    <dt>Objects examined</dt><dd class="mono" data-testid="examined">{s.objectsExamined}</dd>
    <dt>Tests applied</dt>
    <dd><ul class="plain">{#each tests as t (t.name)}<li><span class="mono">{t.name}@{t.version}</span> {#if t.question}<span class="muted">{t.question}</span>{/if}</li>{/each}</ul></dd>
    <dt>Archive releases</dt><dd><ul class="plain">{#each s.archiveReleases as r (r)}<li class="mono">{r}</li>{/each}</ul></dd>
    {#if s.manifestId}<dt>Manifest</dt><dd class="mono" title={s.manifestId}>{shortHash(s.manifestId, 16)}</dd>{/if}
  </dl>
</section>

<style>
  dl { display: grid; grid-template-columns: max-content 1fr; gap: 0.4rem 1rem; margin: 0; }
  dt { color: var(--muted); }
  dd { margin: 0; overflow-wrap: anywhere; }
  .plain { list-style: none; padding: 0; margin: 0; }
  .kv { margin-right: 0.8rem; white-space: nowrap; }
  .error { color: var(--bad); }
</style>

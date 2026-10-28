<script lang="ts">
  import Badge from "$lib/components/Badge.svelte";
  import { belowAlpha, formatNumber, formatPercent, investigationTone, shortHash, type TestResultDoc } from "$lib/view.ts";

  let { data } = $props();
  const i = $derived(data.investigation);
  interface ObjectDoc {
    object: { object_key: string; sky_icrs: [number, number]; resolver_record: { main_id?: string } | null };
    bundle_id: string;
    catalogue_rows: Record<string, Record<string, unknown>[]>;
    observation_index: { archive: string; release_label: string; records: number; reach_back_jyear: number | null }[];
    products: { product_type?: string; artifact_id?: string }[];
  }
  interface DossierDoc {
    test_results: TestResultDoc[];
    objects: ObjectDoc[];
    multiplicity: { objects_examined: number; tests_applied: number };
  }
  const dossier = $derived(i.dossier as DossierDoc | null);
  const results = $derived(dossier?.test_results ?? (i.testResults as TestResultDoc[]));
  const used = $derived(i.evidenceUsed as { archives: string[]; releases: string[]; epoch_min_jyear: number | null; epoch_max_jyear: number | null });
  const notes = $derived((i.notes ?? []) as { text: string; author: string; at: string }[]);
</script>

<svelte:head><title>{i.candidateKey} · SDP evidence</title></svelte:head>

<p class="muted">
  <a href="/investigations">Investigations</a> / <a href="/investigations/{i.setId}" class="mono">{i.setId}</a> /
  <span class="mono">{i.id}</span>
</p>
<h1 class="mono">{i.candidateKey}</h1>
<p>
  <Badge tone={investigationTone(i.state)} label={i.state} />
  <Badge tone="warn" label={i.label} />
  <span class="muted">score {formatNumber(i.score)} under {i.rankingPolicy}</span>
</p>
<p class="notice" data-testid="exploratory">
  Exploratory. These results came from looking, so they cannot confirm anything. Promotion needs a frozen
  confirmatory predeclaration that names evidence this investigation did not use.
</p>

<section aria-labelledby="results">
  <h2 id="results">Test results</h2>
  <p class="muted">Each result beside its null comparison: how often the same test fired on null versions of this field.</p>
  <div class="table-wrap">
    <table data-testid="results">
      <thead><tr><th>Test</th><th>Statistic</th><th>p</th><th>Effect</th><th>Null rate at alpha</th><th>Seed</th></tr></thead>
      <tbody>
        {#each results as r, n (n)}
          <tr class:below={belowAlpha(r, r.null?.alpha)}>
            <td class="mono">{r.test_name}@{r.test_version}</td>
            <td class="mono">{formatNumber(r.statistic)}</td>
            <td class="mono">{formatNumber(r.p_value)}</td>
            <td class="mono">{formatNumber(r.effect_size)}</td>
            <td class="mono">
              {#if r.null}{formatPercent(r.null.false_positive_rate)} <span class="muted">α = {formatNumber(r.null.alpha)}, {r.null.trials} trials</span>
              {:else}<span class="muted">—</span>{/if}
            </td>
            <td class="mono">{r.seed}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
</section>

{#if dossier}
  <section aria-labelledby="objects">
    <h2 id="objects">Evidence</h2>
    {#each dossier.objects as o (o.bundle_id)}
      <article data-testid="object">
        <h3 class="mono">{o.object.object_key}{#if o.object.resolver_record?.main_id}{" "}<span class="muted" data-testid="known-as">known as {o.object.resolver_record.main_id}</span>{/if}</h3>
        <p class="muted mono">RA {formatNumber(o.object.sky_icrs[0])} · Dec {formatNumber(o.object.sky_icrs[1])} · bundle {shortHash(o.bundle_id)}</p>
        <ul class="plain">
          {#each Object.entries(o.catalogue_rows) as [source, rows] (source)}
            <li><span class="mono">{source}</span>: {rows.length} row{rows.length === 1 ? "" : "s"}</li>
          {/each}
          {#each o.observation_index as idx (idx.archive)}
            <li data-testid="observations">
              <span class="mono">{idx.archive}</span> {idx.release_label}: {idx.records} observation{idx.records === 1 ? "" : "s"}
              {#if idx.reach_back_jyear !== null}<span class="muted">reaching back to {formatNumber(idx.reach_back_jyear)}</span>{/if}
            </li>
          {/each}
          {#each o.products as p, n (n)}
            <li><span class="mono">{p.product_type}</span> <span class="mono muted" title={p.artifact_id}>{shortHash(p.artifact_id)}</span></li>
          {/each}
        </ul>
      </article>
    {/each}
    <p class="muted" data-testid="multiplicity">
      One of {dossier.multiplicity.objects_examined} objects examined under {dossier.multiplicity.tests_applied} test{dossier.multiplicity.tests_applied === 1 ? "" : "s"}.
    </p>
  </section>
{/if}

<section aria-labelledby="triage">
  <h2 id="triage">Triage</h2>
  <ol class="timeline" data-testid="transitions">
    {#each i.transitions as t, n (n)}
      <li>
        <span class="mono">{t.fromState ?? "∅"} → {t.toState}</span> by {t.actor}
        <span class="muted">{t.at}</span>
        <div>{t.reason}</div>
      </li>
    {/each}
  </ol>
  {#if notes.length}
    <h3>Notes</h3>
    <ul data-testid="notes">{#each notes as note, n (n)}<li>{note.text} <span class="muted">{note.author}, {note.at}</span></li>{/each}</ul>
  {/if}
</section>

<section aria-labelledby="used">
  <h2 id="used">Evidence used</h2>
  <p class="muted">A confirmatory predeclaration must name something outside this.</p>
  <dl data-testid="evidence-used">
    <dt>Archives</dt><dd class="mono">{used.archives.join(", ") || "—"}</dd>
    <dt>Releases</dt><dd class="mono">{used.releases.join(", ") || "—"}</dd>
    <dt>Epochs</dt>
    <dd class="mono">{used.epoch_min_jyear === null ? "—" : `${formatNumber(used.epoch_min_jyear)} – ${formatNumber(used.epoch_max_jyear)}`}</dd>
  </dl>
</section>

<style>
  .notice { border-left: 3px solid var(--warn); padding: 0.4rem 0.8rem; background: var(--surface); }
  tr.below td { background: color-mix(in srgb, var(--warn) 10%, transparent); }
  dl { display: grid; grid-template-columns: max-content 1fr; gap: 0.4rem 1rem; margin: 0; }
  dt { color: var(--muted); }
  dd { margin: 0; overflow-wrap: anywhere; }
  .plain { list-style: none; padding: 0; margin: 0; }
  article { border: 1px solid var(--line); border-radius: 8px; padding: 0.6rem 0.8rem; margin-bottom: 0.6rem; background: var(--surface); }
  h3 { font-size: 0.95rem; margin: 0 0 0.2rem; }
  .timeline { padding-left: 1.2rem; }
  .timeline li { margin-bottom: 0.4rem; }
</style>

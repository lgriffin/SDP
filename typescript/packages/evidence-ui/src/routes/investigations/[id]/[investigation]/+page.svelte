<script lang="ts">
  import { enhance } from "$app/forms";
  import Badge from "$lib/components/Badge.svelte";
  import CopyCommand from "$lib/components/CopyCommand.svelte";
  import FieldImage from "$lib/components/FieldImage.svelte";
  import MatchList from "$lib/components/MatchList.svelte";
  import { candidateStateLabel, humanTime, imageTitle, observedOn, STAR_WORDS, starStatus } from "$lib/plain.ts";
  import { belowAlpha, formatNumber, formatPercent, investigationTone, shortHash, type TestResultDoc } from "$lib/view.ts";

  let { data, form } = $props();
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
  const knownAs = $derived(dossier?.objects.find((o) => o.object.object_key === i.candidateKey)?.object.resolver_record?.main_id);
  const sightings = $derived(data.sightings);
  const canPark = $derived(i.state === "open");
  const canReopen = $derived(i.state === "parked");
  const canClose = $derived(i.state !== "closed");
  // Typed once and kept across failed submissions; the server remembers it in a cookie.
  let actor = $state(data.actor);
  let busy = $state(false);
  const working = () => {
    busy = true;
    return async ({ update }: { update: (o?: { reset?: boolean }) => Promise<void> }) => {
      await update({ reset: false });
      busy = false;
    };
  };
</script>

<svelte:head><title>{i.candidateKey} · SDP</title></svelte:head>

<p class="crumbs">
  <a href="/investigations">Candidates</a> / <a href="/investigations/{i.setId}" class="mono">{i.setId}</a> /
  <span class="mono">{i.id}</span>
</p>
<h1>{i.candidateKey}</h1>
<p class="sub">
  {#if knownAs}<span>Known as <strong>{knownAs}</strong> ·</span>{/if}
  <Badge tone={investigationTone(i.state)} label={candidateStateLabel(i.state)} />
  <Badge tone="warn" label={i.label === "EXPLORATORY" ? "Exploratory" : i.label} />
  <span class="muted">score {formatNumber(i.score)} under {i.rankingPolicy}</span>
</p>
<p class="notice" data-testid="exploratory">
  Exploratory. These results came from looking, so they cannot confirm anything. To turn this into a finding,
  plan a hypothesis test in advance that uses evidence this search did not.
</p>
<p class="compare-link">
  <a class="button secondary" href="/investigations/{i.setId}/{i.id}/compare" data-testid="compare-link">Compare with catalogues</a>
  <span class="muted">Is it already known, or an artefact? See it beside SIMBAD, Gaia, 2MASS, WISE, Pan-STARRS and AAVSO VSX.</span>
</p>

<div class="top">
  <section aria-labelledby="results-h">
    <h2 id="results-h">Is it more than chance?</h2>
    <p class="muted">Each result beside how often the same test fired on null versions of this field.</p>
    {#each results as r, n (n)}
      <div class="result" class:below={belowAlpha(r, r.null?.alpha)}>
        <div class="result-name mono">{r.test_name}</div>
        <p>
          p = <strong class="num">{formatNumber(r.p_value)}</strong>{#if r.null}. By chance alone this test fires for <strong>{formatPercent(r.null.false_positive_rate)}</strong> of objects at α = {formatNumber(r.null.alpha)}
            ({r.null.trials} null fields).{/if}
        </p>
        {#if belowAlpha(r, r.null?.alpha)}
          <p class="small">Below α, but so are many objects in the null fields. Weigh the two together.</p>
        {/if}
      </div>
    {/each}
    <details>
      <summary>The numbers</summary>
      <div class="table-wrap">
        <table data-testid="results">
          <thead><tr><th>Test</th><th>Statistic</th><th>p</th><th>Effect</th><th>Null rate at alpha</th><th>Seed</th></tr></thead>
          <tbody>
            {#each results as r, n (n)}
              <tr>
                <td class="mono">{r.test_name}@{r.test_version}</td>
                <td class="num">{formatNumber(r.statistic)}</td>
                <td class="num">{formatNumber(r.p_value)}</td>
                <td class="num">{formatNumber(r.effect_size)}</td>
                <td class="num">
                  {#if r.null}{formatPercent(r.null.false_positive_rate)} <span class="muted">α = {formatNumber(r.null.alpha)}, {r.null.trials} trials</span>
                  {:else}<span class="muted">—</span>{/if}
                </td>
                <td class="num">{r.seed}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    </details>
  </section>

  <section class="sheet decide" aria-labelledby="decide-h" data-testid="decide">
    <h2 id="decide-h">Your decision</h2>
    {#if form?.done}<p class="done" role="status" data-testid="action-done">{form.done}</p>{/if}
    {#if form?.error}<p class="error-text" role="alert" data-testid="action-error">{form.error}</p>{/if}
    {#if i.state === "closed"}
      <p class="muted">This candidate is closed. Closing is final; you can still add a note.</p>
    {:else if i.state === "promoted"}
      <p class="muted">This candidate was promoted to a hypothesis test.</p>
    {/if}
    <form method="POST" use:enhance={working}>
      <label>
        Your name
        <input name="actor" autocomplete="name" bind:value={actor} aria-invalid={form?.field === "actor" ? true : undefined} />
      </label>
      <label>
        Why, or your note
        <span class="hint">Kept with the decision, so others can follow your reasoning.</span>
        <textarea name="reason" aria-invalid={form?.field === "reason" ? true : undefined}>{form?.reason ?? ""}</textarea>
      </label>
      <div class="buttons">
        {#if canPark}<button formaction="?/park" disabled={busy}>Park for later</button>{/if}
        {#if canReopen}<button formaction="?/reopen" disabled={busy}>Reopen</button>{/if}
        {#if canClose}<button class="danger" formaction="?/close" disabled={busy}>Close it</button>{/if}
        <button class="secondary" formaction="?/annotate" disabled={busy}>Add a note</button>
      </div>
    </form>
    {#if i.state === "open" || i.state === "parked"}
      <details class="promote">
        <summary>Promote to a hypothesis test</summary>
        <p class="small">
          Promotion needs a frozen predeclaration that names evidence this search did not use, so it is done in a terminal:
        </p>
        <CopyCommand command="sdp investigate promote {i.id} --actor <your-name> --predeclaration <frozen-hash>" />
      </details>
    {/if}
  </section>
</div>

{#if sightings.length}
  <section aria-labelledby="sightings-h" data-testid="sightings">
    <h2 id="sightings-h">On the images</h2>
    <p class="muted">The marker is where Gaia's motion puts the star on the day each image was taken; the cross is the detection it was matched to.</p>
    <div class="sightings">
      {#each sightings as { image, star } (image.artifact_id)}
        {@const status = starStatus(star)}
        <article class="sighting" data-status={status}>
          <h3>{imageTitle(image.summary, image.product_id)}{#if observedOn(image.summary)}<span class="muted">{" · "}{observedOn(image.summary)}</span>{/if}</h3>
          {#if image.width && image.height}
            <FieldImage
              src="/artifacts/{encodeURIComponent(image.artifact_id)}/preview.png"
              width={image.width}
              height={image.height}
              stars={image.stars}
              setId={i.setId}
              focus={star.object_key}
              crop={Math.max(40, Math.round(image.width / 20))}
              label="{i.candidateKey} on {imageTitle(image.summary, image.product_id)}: {STAR_WORDS[status].toLowerCase()}"
            />
          {/if}
          <p><Badge tone={star.flagged ? "bad" : star.ambiguous ? "warn" : "good"} label={STAR_WORDS[status]} />
            {#if star.d2 !== null}<span class="muted small">offset of {formatNumber(Math.sqrt(star.d2))} times its combined uncertainty</span>{/if}</p>
          <a class="small" href="/investigations/{i.setId}/images?image={encodeURIComponent(image.artifact_id)}&star={encodeURIComponent(star.object_key)}#viewer">See it on the whole image</a>
          {#if star.counterparts}
            <h4>Possible matches</h4>
            <MatchList star={star.object_key} counterparts={star.counterparts} />
          {/if}
        </article>
      {/each}
    </div>
  </section>
{/if}

{#if dossier}
  <section aria-labelledby="objects">
    <h2 id="objects">What the archives hold</h2>
    {#each dossier.objects as o (o.bundle_id)}
      <article data-testid="object">
        <h3 class="mono">{o.object.object_key}{#if o.object.resolver_record?.main_id}{" "}<span class="muted" data-testid="known-as">known as {o.object.resolver_record.main_id}</span>{/if}</h3>
        <p class="muted num">RA {formatNumber(o.object.sky_icrs[0])}° · Dec {formatNumber(o.object.sky_icrs[1])}°</p>
        <ul class="plain">
          {#each Object.entries(o.catalogue_rows) as [source, rows] (source)}
            <li><span class="mono">{source}</span>: {rows.length} catalogue row{rows.length === 1 ? "" : "s"}</li>
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
      The more objects and tests, the more often something looks interesting by chance.
    </p>
  </section>
{/if}

<section aria-labelledby="triage">
  <h2 id="triage">History</h2>
  <ol class="timeline" data-testid="transitions">
    {#each i.transitions as t, n (n)}
      <li>
        <div><strong>{t.fromState ? `${candidateStateLabel(t.fromState)} → ${candidateStateLabel(t.toState)}` : `Opened as ${candidateStateLabel(t.toState).toLowerCase()}`}</strong> by {t.actor} <span class="muted">{humanTime(t.at)}</span></div>
        <div>{t.reason}</div>
      </li>
    {/each}
  </ol>
  {#if notes.length}
    <h3>Notes</h3>
    <ul data-testid="notes">{#each notes as note, n (n)}<li>{note.text} <span class="muted">{note.author}, {humanTime(note.at)}</span></li>{/each}</ul>
  {/if}
</section>

<section aria-labelledby="used">
  <h2 id="used">Evidence this search used</h2>
  <p class="muted">A proper test of this candidate must use something outside this list.</p>
  <dl data-testid="evidence-used">
    <dt>Archives</dt><dd class="mono">{used.archives.join(", ") || "—"}</dd>
    <dt>Releases</dt><dd class="mono">{used.releases.join(", ") || "—"}</dd>
    <dt>Epochs</dt>
    <dd class="num">{used.epoch_min_jyear === null ? "—" : `${formatNumber(used.epoch_min_jyear)} – ${formatNumber(used.epoch_max_jyear)}`}</dd>
  </dl>
</section>

<style>
  .sub { display: flex; flex-wrap: wrap; gap: 0.4rem; align-items: center; margin: 0 0 0.9rem; }
  .notice { background: color-mix(in srgb, var(--warn) 9%, var(--surface)); border: 1px solid color-mix(in srgb, var(--warn) 30%, var(--line)); border-radius: var(--radius); padding: 0.75rem 1rem; max-width: none; }
  .compare-link { display: flex; flex-wrap: wrap; gap: 0.4rem 0.8rem; align-items: center; max-width: none; margin: 0.9rem 0 0; }
  .top { display: grid; grid-template-columns: minmax(0, 1.4fr) minmax(0, 1fr); gap: 1.4rem; align-items: start; }
  .decide { margin-top: 2.4rem; }
  .decide h2 { margin-top: 0; }
  .decide form { display: grid; gap: 0.8rem; }
  .decide label { display: flex; flex-direction: column; gap: 0.3rem; }
  .buttons { display: flex; flex-wrap: wrap; gap: 0.5rem; }
  .done { color: var(--good); font-weight: 600; }
  .promote { margin-top: 1rem; }
  .result { background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); padding: 0.75rem 1rem; margin-bottom: 0.6rem; }
  .result.below { border-color: color-mix(in srgb, var(--warn) 45%, var(--line)); }
  .result p { margin: 0.3rem 0 0; }
  .result-name { font-weight: 650; }
  .small { font-size: 0.9rem; color: var(--muted); }
  [aria-invalid="true"] { border-color: var(--bad); }
  dl { display: grid; grid-template-columns: max-content 1fr; gap: 0.45rem 1.2rem; margin: 0; }
  dt { color: var(--muted); }
  dd { margin: 0; overflow-wrap: anywhere; }
  .plain { list-style: none; padding: 0; margin: 0; }
  article { border: 1px solid var(--line); border-radius: var(--radius); padding: 0.7rem 1rem; margin-bottom: 0.6rem; background: var(--surface); }
  article h3 { font-size: 0.98rem; margin: 0 0 0.2rem; }
  article p { margin: 0 0 0.3rem; }
  .timeline { padding-left: 1.2rem; }
  .sightings { display: grid; grid-template-columns: repeat(auto-fill, minmax(15rem, 1fr)); gap: 0.8rem; }
  .sighting { margin: 0; display: grid; gap: 0.5rem; align-content: start; }
  .sighting h3, .sighting h4 { margin: 0; font-size: 0.95rem; }
  .sighting p { margin: 0; }

  .timeline li { margin-bottom: 0.6rem; }
  @media (max-width: 800px) {
    .top { grid-template-columns: 1fr; }
    .decide { margin-top: 0; }
  }
</style>

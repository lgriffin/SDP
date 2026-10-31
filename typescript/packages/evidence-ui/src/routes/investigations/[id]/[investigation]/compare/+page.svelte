<script lang="ts">
  import { untrack } from "svelte";

  import { enhance } from "$app/forms";
  import Badge from "$lib/components/Badge.svelte";
  import {
    compareCell,
    findings,
    formatValue,
    foundRow,
    isCandidate,
    latestPulls,
    notYetPulled,
    QUANTITIES,
    RADII,
    shownQuantities,
  } from "$lib/compare.ts";
  import { humanTime } from "$lib/plain.ts";
  import type { ReferencePull } from "$lib/ports.ts";
  import { formatNumber, shortHash } from "$lib/view.ts";

  let { data, form } = $props();
  const i = $derived(data.investigation);
  const comparison = $derived(data.comparison);
  const catalogues = $derived(comparison?.catalogues ?? []);
  const pulls = $derived(comparison?.pulls ?? []);
  const latest = $derived(latestPulls(pulls, catalogues));
  // Catalogues with something in the cone get a column; the rest are named under the table.
  const columns = $derived(latest.filter((p) => p.status === "found"));
  const empty = $derived(latest.filter((p) => p.status !== "found"));
  const found = $derived(foundRow(i.dossier, i.candidateKey));
  const best = $derived(columns.map((p) => p.matches[0] ?? null));
  const quantities = $derived(shownQuantities(found, best));
  const said = $derived(findings(latest, found, i.candidateKey));
  const crowded = $derived(latest.filter((p) => p.matches.length > 1));
  const structure = $derived(i.candidateKey.startsWith("structure:"));

  // What the form starts with: the catalogues not asked yet, or all of them once every one has answered.
  const fresh = $derived(notYetPulled(catalogues, pulls));
  let chosen = $state<string[]>([]);
  let radius = $state(5);
  // Typed once and kept across failed submissions; the server remembers it in a cookie.
  let actor = $state(data.actor);
  // Reset the choices after each pull (new pulls change what is fresh) or a refused one (the form echoes them).
  $effect.pre(() => {
    const echoed = form?.sources as string[] | undefined;
    const next = echoed ?? (fresh.length ? fresh : catalogues.map((c) => c.sourceId));
    const r = Number(form?.radius ?? (structure ? 300 : 5));
    untrack(() => {
      chosen = next;
      radius = r;
    });
  });
  let busy = $state(false);
  const working = () => {
    busy = true;
    return async ({ update }: { update: (o?: { reset?: boolean }) => Promise<void> }) => {
      await update({ reset: false });
      busy = false;
    };
  };

  function position(row: Record<string, unknown> | null | undefined): string {
    const ra = row?.ra_deg;
    const dec = row?.dec_deg;
    return typeof ra === "number" && typeof dec === "number" ? `${ra.toFixed(5)}°, ${dec >= 0 ? "+" : ""}${dec.toFixed(5)}°` : "—";
  }

  function status(p: ReferencePull): { tone: "good" | "warn" | "bad" | "neutral"; label: string } {
    if (p.status === "failed") return { tone: "bad", label: "Did not answer" };
    if (p.status === "none") return { tone: "good", label: `Nothing within ${formatNumber(p.radius_arcsec)}″` };
    const n = p.matches.length;
    return { tone: "warn", label: `${n} within ${formatNumber(p.radius_arcsec)}″` };
  }
</script>

<svelte:head><title>Compare {i.candidateKey} · SDP</title></svelte:head>

<p class="crumbs">
  <a href="/investigations">Candidates</a> / <a href="/investigations/{i.setId}" class="mono">{i.setId}</a> /
  <a href="/investigations/{i.setId}/{i.id}" class="mono">{i.id}</a> / Compare
</p>
<h1>Compare with catalogues</h1>
<p class="lede">
  <span class="mono">{i.candidateKey}</span> beside the nearest source in each catalogue you ask. Use it to rule out
  “already known” and “artefact” before you plan a test.
</p>

{#if comparison === null}
  <p class="notice" data-testid="no-comparison">
    This SDP server cannot ask reference catalogues yet. Update SDP to compare candidates with SIMBAD, Gaia, 2MASS,
    AllWISE, Pan-STARRS and AAVSO VSX.
  </p>
{:else}
  <div class="top">
    <section aria-labelledby="said-h" data-testid="findings">
      <h2 id="said-h">What the references say</h2>
      {#if !latest.length}
        <p class="muted">Nothing asked yet. Tick the catalogues on the right and press <strong>Pull references</strong>.</p>
      {:else}
        <ul class="findings">
          {#each said as f, n (n)}
            <li class={f.tone}><span class="mark" aria-hidden="true"></span>{f.text}</li>
          {/each}
        </ul>
        <p class="small">
          A match only tells you something is there. If nothing explains the candidate, write a predeclaration that names
          evidence this search did not use, then promote it from the <a href="/investigations/{i.setId}/{i.id}">candidate page</a>.
        </p>
      {/if}
    </section>

    <section class="sheet pull" aria-labelledby="pull-h" data-testid="pull">
      <h2 id="pull-h">Pull more references</h2>
      {#if form?.done}<p class="done" role="status" data-testid="pull-done">{form.done}</p>{/if}
      {#if form?.error}<p class="error-text" role="alert" data-testid="pull-error">{form.error}</p>{/if}
      <form method="POST" action="?/pull" use:enhance={working}>
        <fieldset class:invalid={form?.field === "source"}>
          <legend>Catalogues to ask</legend>
          {#each catalogues as c (c.sourceId)}
            <label class="choice">
              <input type="checkbox" name="source" value={c.sourceId} bind:group={chosen} />
              <span><strong>{c.title}</strong>{#if pulls.some((p) => p.source_id === c.sourceId)}<span class="muted">{" "}· asked before</span>{/if}<br /><span class="hint">{c.description}</span></span>
            </label>
          {/each}
        </fieldset>
        <label>
          Search radius
          <span class="hint">How far from the candidate to look. 5″ suits a star; a cluster needs minutes of arc.</span>
          <select name="radius" bind:value={radius}>
            {#each RADII as r (r)}<option value={r}>{r < 60 ? `${r}″` : `${r / 60}′`}</option>{/each}
          </select>
        </label>
        <label>
          Your name
          <input name="actor" autocomplete="name" bind:value={actor} aria-invalid={form?.field === "actor" ? true : undefined} />
        </label>
        <button disabled={busy || !chosen.length} data-testid="pull-button">
          {busy ? `Asking ${chosen.length} catalogue${chosen.length === 1 ? "" : "s"}…` : "Pull references"}
        </button>
        {#if busy}<p class="small" role="status">Archives answer in seconds to a minute. You can stay on this page.</p>{/if}
      </form>
    </section>
  </div>

  {#if columns.length}
    <section aria-labelledby="side-h">
      <h2 id="side-h">Side by side</h2>
      <p class="muted">
        Each catalogue's nearest source. Where it disagrees with what the search found, the cell says how.
        {#if structure}For a group, the comparison is with the centre of its members.{/if}
      </p>
      <div class="table-wrap">
        <table class="compare" data-testid="comparison">
          <thead>
            <tr>
              <th scope="col">Quantity</th>
              <th scope="col" class="found">What the search found<br /><span class="muted small mono">{i.candidateKey}</span></th>
              {#each columns as p (p.source_id)}
                {@const s = status(p)}
                <th scope="col" data-source={p.source_id}>
                  {p.title}<br /><Badge tone={s.tone} label={s.label} />
                  {#if isCandidate(p.matches[0] ?? null, i.candidateKey)}<br /><span class="small">the candidate itself</span>{/if}
                </th>
              {/each}
            </tr>
          </thead>
          <tbody>
            <tr>
              <th scope="row">Position (RA, Dec)</th>
              <td class="found num">{position(found)}</td>
              {#each columns as p, n (p.source_id)}
                <td class="num">{position(best[n])}</td>
              {/each}
            </tr>
            {#each quantities as q (q.key)}
              <tr>
                <th scope="row">{q.label}</th>
                <td class="found num">{q.key === "separation_arcsec" ? "—" : formatValue(q, found?.[q.key], q.key === "parallax_mas" ? found?.parallax_mas_err : undefined)}</td>
                {#each columns as p, n (p.source_id)}
                  {@const cell = compareCell(q, found, best[n] ?? null)}
                  <td class="num" class:differs={cell.differs}>
                    {cell.text}{#if cell.differs}<span class="why">{cell.differs}</span>{/if}
                  </td>
                {/each}
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
      <details class="meaning">
        <summary>What these rows mean</summary>
        <dl>
          {#each quantities.filter((x) => x.hint) as x (x.key)}<dt>{x.label}</dt><dd>{x.hint}</dd>{/each}
          <dt>A marked cell</dt><dd>Brightness in the same band more than 0.3 mag apart, parallax more than 3σ apart, proper motion more than 3 mas/yr (or 20%) apart, or a position more than 1″ away.</dd>
        </dl>
      </details>
    </section>
  {/if}

  {#if empty.length}
    <section aria-labelledby="empty-h">
      <h2 id="empty-h" class="small-h">No column for</h2>
      <ul class="plain" data-testid="empty-pulls">
        {#each empty as p (p.source_id)}
          <li data-source={p.source_id}>
            <strong>{p.title}</strong>:
            {#if p.status === "failed"}did not answer <span class="mono small">{p.error}</span>{:else}nothing within {formatNumber(p.radius_arcsec)}″{/if}
          </li>
        {/each}
      </ul>
    </section>
  {/if}

  {#if latest.length}
    {#if crowded.length}
      <section aria-labelledby="crowd-h">
        <h2 id="crowd-h">Everything within the cone</h2>
        {#each crowded as p (p.source_id)}
          <details>
            <summary>{p.title}: {p.matches.length} sources within {formatNumber(p.radius_arcsec)}″</summary>
            <div class="table-wrap">
              <table>
                <thead><tr><th>Source</th><th>Distance</th>{#each QUANTITIES.filter((q) => q.check === "mag" && p.matches.some((m) => m[q.key] !== undefined)) as q (q.key)}<th>{q.label}</th>{/each}</tr></thead>
                <tbody>
                  {#each p.matches as m, n (n)}
                    <tr>
                      <td class="mono">{m.source_key ?? "—"}</td>
                      <td class="num">{formatNumber(m.separation_arcsec)}″</td>
                      {#each QUANTITIES.filter((q) => q.check === "mag" && p.matches.some((x) => x[q.key] !== undefined)) as q (q.key)}<td class="num">{formatValue(q, m[q.key])}</td>{/each}
                    </tr>
                  {/each}
                </tbody>
              </table>
            </div>
          </details>
        {/each}
      </section>
    {/if}

    <section aria-labelledby="history-h">
      <h2 id="history-h">Every pull</h2>
      <p class="muted">Kept with the candidate, so the comparison you relied on stays on record.</p>
      <details>
        <summary>{pulls.length} pull{pulls.length === 1 ? "" : "s"}</summary>
        <div class="table-wrap">
          <table data-testid="pulls">
            <thead><tr><th>When</th><th>Who</th><th>Catalogue</th><th>Radius</th><th>Result</th><th>Archive's answer</th></tr></thead>
            <tbody>
              {#each [...pulls].reverse() as p, n (n)}
                <tr>
                  <td>{humanTime(p.asked_at)}</td>
                  <td>{p.actor}</td>
                  <td>{p.title} <span class="muted small mono">{p.release_label}</span><br /><span class="small">{p.citation}</span></td>
                  <td class="num">{formatNumber(p.radius_arcsec)}″</td>
                  <td>{status(p).label}</td>
                  <td>{#if p.raw_artifact_id}<a class="mono" href="/artifacts/{encodeURIComponent(p.raw_artifact_id)}" title={p.raw_artifact_id}>{shortHash(p.raw_artifact_id)}</a>{:else}<span class="muted">—</span>{/if}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      </details>
    </section>
  {/if}
{/if}

<style>
  .notice { background: color-mix(in srgb, var(--warn) 9%, var(--surface)); border: 1px solid color-mix(in srgb, var(--warn) 30%, var(--line)); border-radius: var(--radius); padding: 0.75rem 1rem; }
  .top { display: grid; grid-template-columns: minmax(0, 1.4fr) minmax(0, 1fr); gap: 1.4rem; align-items: start; }
  .pull { margin-top: 2.4rem; }
  .pull h2 { margin-top: 0; }
  .pull form { display: grid; gap: 0.9rem; }
  .pull label { display: flex; flex-direction: column; gap: 0.3rem; }
  fieldset { border: 1px solid var(--line); border-radius: var(--radius); padding: 0.6rem 0.8rem; margin: 0; display: grid; gap: 0.55rem; }
  fieldset.invalid { border-color: var(--bad); }
  legend { font-weight: 600; padding: 0 0.3rem; }
  .pull label.choice { flex-direction: row; align-items: flex-start; gap: 0.55rem; }
  .choice input { margin-top: 0.25rem; }
  .hint { font-size: 0.88rem; color: var(--muted); }
  .hint.block { display: block; font-weight: 400; }
  .done { color: var(--good); font-weight: 600; }
  .small { font-size: 0.9rem; color: var(--muted); }
  .findings { list-style: none; padding: 0; margin: 0 0 0.8rem; display: grid; gap: 0.5rem; }
  .findings li { display: flex; gap: 0.6rem; align-items: baseline; background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); padding: 0.6rem 0.85rem; }
  .findings li.warn { border-color: color-mix(in srgb, var(--warn) 45%, var(--line)); }
  .findings li.good { border-color: color-mix(in srgb, var(--good) 40%, var(--line)); }
  .mark { width: 0.55rem; height: 0.55rem; border-radius: 50%; background: var(--muted); flex: none; transform: translateY(-0.05rem); }
  .warn .mark { background: var(--warn); }
  .good .mark { background: var(--good); }
  .compare th[scope="row"] { font-weight: 600; color: var(--text); background: var(--surface); position: sticky; left: 0; min-width: 11rem; }
  .compare thead th { vertical-align: bottom; }
  .compare td.num { white-space: nowrap; }
  .meaning { margin-top: 0.7rem; }
  .meaning dl { display: grid; grid-template-columns: max-content 1fr; gap: 0.35rem 1rem; margin: 0.6rem 0 0; font-size: 0.92rem; }
  .meaning dt { font-weight: 600; }
  .meaning dd { margin: 0; color: var(--muted); }
  .small-h { font-size: 1rem; }
  @media (max-width: 600px) { .meaning dl { grid-template-columns: 1fr; } }
  .compare thead th:first-child { position: sticky; left: 0; z-index: 1; }
  .found { background: color-mix(in srgb, var(--accent) 7%, var(--surface)); }
  thead .found { background: color-mix(in srgb, var(--accent) 14%, var(--sunken)); color: var(--text); }
  td.differs { background: color-mix(in srgb, var(--warn) 10%, var(--surface)); }
  .why { display: block; font-size: 0.82rem; font-weight: 600; color: var(--warn); }
  .plain { list-style: none; padding: 0; }
  @media (max-width: 800px) {
    .top { grid-template-columns: 1fr; }
    .pull { margin-top: 0; }
  }
</style>

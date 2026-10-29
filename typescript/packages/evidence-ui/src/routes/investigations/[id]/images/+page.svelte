<script lang="ts">
  import Badge from "$lib/components/Badge.svelte";
  import FieldImage from "$lib/components/FieldImage.svelte";
  import MatchList from "$lib/components/MatchList.svelte";
  import { checkName, fieldLabel, humanBytes, imageTitle, observedOn, productFile, STAR_WORDS, starStatus, type StarStatus } from "$lib/plain.ts";
  import { formatNumber } from "$lib/view.ts";

  let { data } = $props();
  const image = $derived(data.image);
  const s = $derived(data.set);
  const title = $derived(imageTitle(image.summary, image.product_id));
  const failed = $derived((image.checks ?? []).filter((c) => !c.passed));
  const count = (status: StarStatus) => image.stars.filter((x) => starStatus(x) === status).length;
  const notable = $derived(image.stars.filter((x) => starStatus(x) !== "matched"));
  const focused = $derived(image.stars.find((x) => x.object_key === data.star) ?? null);
  const crop = $derived(Math.max(40, Math.round((image.width ?? 400) / 20)));
  const href = (star: string | null, artifact = image.artifact_id) =>
    `?image=${encodeURIComponent(artifact)}${star ? `&star=${encodeURIComponent(star)}` : ""}#viewer`;
  const downloadName = $derived(productFile(image.product_id).replace(/\.fits$/, image.product_id.includes("@cutout(") ? "-cutout.fits" : ".fits"));
  const level = $derived(image.summary.calibration_level as number | undefined);
  const LEVELS: Record<number, string> = { 1: "raw", 2: "calibrated single exposure", 3: "combined, calibrated mosaic" };
</script>

<svelte:head><title>{title} · images · SDP</title></svelte:head>

<p class="crumbs">
  <a href="/investigations">Candidates</a> / <a href="/investigations/{s.id}" class="mono">{s.id}</a> / images
</p>
<h1>Images of {fieldLabel(s.field)}</h1>
<p class="lede">
  Each Gaia star is drawn where its measured motion puts it on the day the image was taken. A star that is missing, or
  sits off that place by more than the uncertainties allow, stands out; it is a lead to check, not a finding.
</p>

{#if data.images.length > 1}
  <nav class="tabs" aria-label="Images">
    {#each data.images as i (i.artifact_id)}
      <a href={href(null, i.artifact_id)} aria-current={i.artifact_id === image.artifact_id ? "page" : undefined}>
        {imageTitle(i.summary, i.product_id)}
        {#if i.stars.some((x) => x.flagged)}<span class="dot" aria-label="has stars that stand out"></span>{/if}
      </a>
    {/each}
  </nav>
{/if}

<div class="layout">
  <section id="viewer" aria-labelledby="viewer-h">
    <h2 id="viewer-h" class="visually-hidden">The image</h2>
    {#if focused}
      <p class="zoomed" data-testid="zoomed">
        Close up on <strong class="mono">{focused.object_key}</strong>: {STAR_WORDS[starStatus(focused)].toLowerCase()}.
        <a href={href(null)}>See the whole image</a>
      </p>
    {/if}
    {#if focused?.counterparts}
      <div class="focus-matches" data-testid="focus-matches">
        <h3>Possible matches</h3>
        <MatchList star={focused.object_key} counterparts={focused.counterparts} />
      </div>
    {/if}
    {#if image.width && image.height}
      <FieldImage
        src="/artifacts/{encodeURIComponent(image.artifact_id)}/preview.png"
        width={image.width}
        height={image.height}
        stars={image.stars}
        setId={s.id}
        focus={data.star}
        crop={data.star ? crop : 0}
        label="{title}, north up, with {image.stars.length} Gaia stars marked"
      />
    {:else}
      <p class="muted">This image's size could not be read, so it cannot be drawn.</p>
    {/if}
    <ul class="legend" data-testid="legend">
      <li><svg viewBox="-6 -6 12 12" aria-hidden="true"><circle r="4.5" class="k matched" /></svg>{STAR_WORDS.matched} <span class="num">{count("matched")}</span></li>
      <li><svg viewBox="-6 -6 12 12" aria-hidden="true"><circle r="4.5" class="k ambiguous" /></svg>{STAR_WORDS.ambiguous} <span class="num">{count("ambiguous")}</span></li>
      <li><svg viewBox="-6 -6 12 12" aria-hidden="true"><path d="M0,-5.5L5.5,0L0,5.5L-5.5,0Z" class="k bad" /></svg>{STAR_WORDS.displaced} <span class="num">{count("displaced")}</span></li>
      <li><svg viewBox="-6 -6 12 12" aria-hidden="true"><rect x="-4.5" y="-4.5" width="9" height="9" class="k bad" /></svg>{STAR_WORDS.missing} <span class="num">{count("missing")}</span></li>
      <li><svg viewBox="-6 -6 12 12" aria-hidden="true"><path d="M-4,0h8M0,-4v8" class="k cross" /></svg>The detection it was matched to</li>
    </ul>
  </section>

  <aside>
    <section class="sheet" aria-labelledby="about-h" data-testid="about-image">
      <h2 id="about-h">{title}</h2>
      <dl>
        {#if image.summary.mission}<dt>Telescope</dt><dd>{image.summary.mission}{#if image.summary.programme_id}, programme {image.summary.programme_id}{/if}</dd>{/if}
        {#if observedOn(image.summary)}<dt>Taken</dt><dd>{observedOn(image.summary)}</dd>{/if}
        {#if level !== undefined}<dt>Calibration</dt><dd>Level {level}{#if LEVELS[level]}: {LEVELS[level]}{/if}</dd>{/if}
        {#if image.width}<dt>Size</dt><dd class="num">{image.width} × {image.height} pixels{#if typeof image.summary.size_bytes === "number"}, {humanBytes(image.summary.size_bytes)}{/if}</dd>{/if}
        <dt>File</dt><dd class="mono small">{productFile(image.product_id)}</dd>
      </dl>
      {#if data.downloads}<a class="button secondary" href="/artifacts/{encodeURIComponent(image.artifact_id)}?name={encodeURIComponent(downloadName)}" download>Download the FITS file</a>{/if}
    </section>

    <section class="sheet" aria-labelledby="checks-h" data-testid="checks">
      <h2 id="checks-h">Checks on the file</h2>
      {#if image.checks === null}
        <p class="muted">This archive's products are not checked against an envelope, so the file was used as fetched.</p>
      {:else}
        <p>
          {#if failed.length === 0}<Badge tone="good" label="All passed" />{:else}<Badge tone="bad" label="{failed.length} failed" />{/if}
          {image.checks.length - failed.length} of {image.checks.length} checks passed.
          {#if failed.length}A file that fails a check is kept and used as fetched, never cleaned up or converted.{/if}
        </p>
        <ul class="checks">
          {#each [...failed, ...image.checks.filter((c) => c.passed)] as c (c.field)}
            <li class:bad={!c.passed}>
              <svg class="mark" viewBox="0 0 16 16" aria-hidden="true">
                {#if c.passed}<path d="M3.5 8.5l3 3 6-7" />{:else}<path d="M4.5 4.5l7 7M11.5 4.5l-7 7" />{/if}
              </svg>
              <span><strong>{checkName(c.field)}</strong><span class="visually-hidden">{c.passed ? " passed" : " failed"}</span><br /><span class="muted small">{c.detail}</span></span>
            </li>
          {/each}
        </ul>
      {/if}
    </section>
  </aside>
</div>

<section aria-labelledby="notable-h">
  <h2 id="notable-h">Stars to look at</h2>
  {#if notable.length === 0}
    <p class="muted" data-testid="none-notable">Every Gaia star on this image is where its motion puts it, with one clear match.</p>
  {:else}
    <div class="table-wrap">
      <table data-testid="notable">
        <thead><tr><th>Gaia star</th><th>What the image shows</th><th>Chance</th><th><span class="visually-hidden">Actions</span></th></tr></thead>
        <tbody>
          {#each notable as x (x.object_key)}
            {@const status = starStatus(x)}
            <tr data-status={status}>
              <td class="mono">{x.object_key}</td>
              <td>
                <Badge tone={x.flagged ? "bad" : "warn"} label={STAR_WORDS[status]} />
                {#if status === "ambiguous"}<div class="muted small">More than one explanation is kept for the detection here; zoom in to see them.</div>{/if}
              </td>
              <td class="num">{x.d2 === null ? "—" : `p = ${formatNumber(x.p_value)}`}</td>
              <td class="go">
                <a href={href(x.object_key)}>Zoom in</a>
                {#if x.investigation_id}· <a href="/investigations/{s.id}/{x.investigation_id}">Candidate</a>{/if}
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</section>

<style>
  .tabs { display: flex; flex-wrap: wrap; gap: 0.3rem; margin: 0 0 1rem; }
  .tabs a { padding: 0.35rem 0.85rem; border-radius: 999px; border: 1px solid var(--line); background: var(--surface); text-decoration: none; color: var(--text); display: inline-flex; gap: 0.4rem; align-items: center; }
  .tabs a[aria-current="page"] { background: var(--accent); border-color: var(--accent); color: var(--on-accent); font-weight: 600; }
  .dot { width: 0.5rem; height: 0.5rem; border-radius: 50%; background: var(--bad); }
  .layout { display: grid; grid-template-columns: minmax(0, 1.7fr) minmax(16rem, 1fr); gap: 1.2rem; align-items: start; }
  aside { display: grid; gap: 1rem; }
  aside h2 { margin-top: 0; font-size: 1.05rem; }
  .zoomed { margin: 0 0 0.5rem; }
  .focus-matches { margin: 0 0 0.7rem; }
  .focus-matches h3 { margin: 0 0 0.3rem; font-size: 0.95rem; }
  .legend { list-style: none; padding: 0; margin: 0.6rem 0 0; display: flex; flex-wrap: wrap; gap: 0.4rem 1.1rem; font-size: 0.88rem; }
  .legend li { display: inline-flex; align-items: center; gap: 0.35rem; }
  .legend svg { width: 1rem; height: 1rem; background: #111; border-radius: 3px; }
  .k { fill: none; stroke-width: 1.4; }
  .k.matched { stroke: #7fd49b; }
  .k.ambiguous { stroke: #f2c14e; stroke-dasharray: 2.4 1.6; }
  .k.bad { stroke: #ff7a6b; }
  .k.cross { stroke: #9ecbff; }
  dl { display: grid; grid-template-columns: max-content 1fr; gap: 0.35rem 1rem; margin: 0 0 1rem; }
  dt { color: var(--muted); }
  dd { margin: 0; overflow-wrap: anywhere; }
  .small { font-size: 0.85rem; }
  .checks { list-style: none; padding: 0; margin: 0; display: grid; gap: 0.5rem; max-height: 26rem; overflow: auto; }
  .checks li { display: grid; grid-template-columns: 1.2rem 1fr; gap: 0.4rem; }
  .checks .mark { width: 1rem; height: 1rem; margin-top: 0.15rem; fill: none; stroke: var(--good); stroke-width: 2.2; stroke-linecap: round; stroke-linejoin: round; }
  .checks li.bad .mark { stroke: var(--bad); }
  .checks li.bad strong { color: var(--bad); }
  .go { text-align: right; white-space: nowrap; }
  .visually-hidden { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
  @media (max-width: 900px) {
    .layout { grid-template-columns: 1fr; }
  }
</style>

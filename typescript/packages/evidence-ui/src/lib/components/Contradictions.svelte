<script lang="ts">
  // What the cross-survey-contradiction test found for one star (INV-029): a sentence, then one row
  // per comparison with its budget, flagged first. Shown on the candidate and the compare pages.
  import { comparisonRows, summary, type ContradictionDetails } from "../contradictions.ts";
  import Badge from "./Badge.svelte";

  let { details, compact = false }: { details: ContradictionDetails; compact?: boolean } = $props();
  const rows = $derived(comparisonRows(details));
  const shown = $derived(compact ? rows.filter((r) => r.verdict !== "agrees") : rows);
  const tone = { disagrees: "bad", agrees: "good", "set aside": "neutral" } as const;
  const leftOut = $derived(Object.entries(details.images_left_out ?? {}));
</script>

<div class="contradictions" data-testid="contradictions">
  <p class="said" data-pattern={details.pattern}>{summary(details)}</p>
  {#if shown.length}
    <div class="table-wrap">
      <table data-testid="contradiction-rows">
        <thead>
          <tr>
            <th scope="col">Measured by</th>
            <th scope="col">When</th>
            <th scope="col">What it measured</th>
            <th scope="col">Difference</th>
            <th scope="col">Expected scatter</th>
            <th scope="col">z</th>
            <th scope="col">Verdict</th>
          </tr>
        </thead>
        <tbody>
          {#each shown as r (r.channel)}
            <tr data-verdict={r.verdict} data-channel={r.channel}>
              <th scope="row">{r.label}</th>
              <td class="num">{r.when}</td>
              <td>{r.measured}</td>
              <td class="num">{r.difference}</td>
              <td class="num">{r.expected}</td>
              <td class="num">{r.z}</td>
              <td><Badge tone={tone[r.verdict]} label={r.verdict} />{#if r.why}<span class="why">{r.why}</span>{/if}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
  {#if compact && rows.length > shown.length}
    <p class="small">{rows.length - shown.length} more comparison{rows.length - shown.length === 1 ? "" : "s"} agree with Gaia.</p>
  {/if}
  {#if leftOut.length}
    <p class="small">Images not used: {leftOut.map(([image, why]) => `${image} (${why})`).join("; ")}.</p>
  {/if}
  <details class="meaning">
    <summary>How to read this</summary>
    <p class="small">
      Each row is one thing a survey or an image measured, set against what Gaia predicts for it: the position moved
      along Gaia's proper motion to that date, or the brightness Gaia's G and BP − RP predict in that band, fitted on the
      field's own stars. The expected scatter is the survey's own error plus the extra scatter the field shows; z is the
      difference in units of it. A row is set aside when the survey cannot separate the star from a neighbour, flags the
      measurement, or has too few stars to calibrate. A disagreement is a lead, not a verdict: a variable star, an
      unresolved companion or a wrong match all look like this.
    </p>
  </details>
</div>

<style>
  .contradictions { display: grid; gap: 0.6rem; }
  .said { margin: 0; max-width: 70ch; }
  .why { display: block; font-size: 0.85rem; color: var(--muted); margin-top: 0.15rem; }
  tr[data-verdict="set aside"] { color: var(--muted); }
  .small { font-size: 0.9rem; color: var(--muted); margin: 0; }
  .meaning p { max-width: 75ch; }
</style>

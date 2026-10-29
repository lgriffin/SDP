<script lang="ts">
  // Every hypothesis the association kept for a Gaia star on one image (INT-009), in words.
  import type { CounterpartDoc } from "../ports.ts";

  let { star, counterparts }: { star: string; counterparts: CounterpartDoc[] } = $props();
  const pct = (p: number) => `${Math.round(p * 1000) / 10}%`;
  const detection = (recordId: string) => `Detection ${Number(recordId.split("#").pop())}`;
</script>

{#if counterparts.length === 0}
  <p class="small">No detection on this image is a plausible match: the star is not where it should be.</p>
{:else}
  <ul class="matches" data-testid="matches">
    {#each counterparts as c (c.record_id)}
      <li>
        <strong>{detection(c.record_id)}</strong>{#if c.role === "blend"}, a blend of {c.blend_of.length} Gaia stars{/if}:
        {pct(c.option_posterior)} that it is this star{#if c.role === "blend"}{" "}together with {c.blend_of.filter((b) => b !== star).join(", ")}{/if},
        {pct(c.no_counterpart)} that it is nothing in Gaia.
        {#if c.competing.length}<div class="muted small">Also kept: {c.competing.map((h) => h.replace(/^obj:/, "")).join(", ")}</div>{/if}
      </li>
    {/each}
  </ul>
{/if}

<style>
  .matches { margin: 0; padding-left: 1.1rem; font-size: 0.9rem; display: grid; gap: 0.3rem; }
  .small { font-size: 0.88rem; }
</style>

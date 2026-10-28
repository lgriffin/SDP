<script lang="ts">
  import { gateRuler } from "../plain.ts";
  import type { GateCheck } from "../ports.ts";
  import { formatNumber } from "../view.ts";

  let { check }: { check: GateCheck } = $props();
  const ruler = $derived(gateRuler(check));
  const tone = $derived(check.outcome === "pass" ? "good" : check.outcome === "fail" ? "bad" : "warn");
  const unit = $derived(check.unit ? ` ${check.unit}` : "");
  // Labels stay inside the ruler at the ends of the scale.
  const place = (x: number) => `clamp(2.5rem, ${x}%, calc(100% - 2.5rem))`;
</script>

{#if ruler}
  <div class="ruler {tone}" data-testid="gate-ruler" role="img"
    aria-label="Measured {formatNumber(check.value)}{unit} against a limit of {formatNumber(check.threshold)}{unit}{ruler.log ? ', on a log scale' : ''}">
    <div class="track">
      {#if ruler.passTo > ruler.passFrom}
        <div class="pass" style:left="{ruler.passFrom}%" style:width="{ruler.passTo - ruler.passFrom}%"></div>
      {/if}
      <div class="limit" style:left="{ruler.threshold}%"></div>
      <div class="value" style:left="{ruler.value}%"></div>
    </div>
    <div class="scale" aria-hidden="true">
      <span class="tick limit-label" style:left={place(ruler.threshold)}>limit {formatNumber(check.threshold)}</span>
      <span class="tick value-label" style:left={place(ruler.value)}>{formatNumber(check.value)}{unit}</span>
    </div>
  </div>
{:else}
  <p class="muted">Measured {formatNumber(check.value)}{unit}; limit {formatNumber(check.threshold)}{unit}.</p>
{/if}

<style>
  .ruler { position: relative; padding: 1.45rem 0 1.45rem; min-width: 12rem; }
  .track { position: relative; height: 0.7rem; border-radius: 999px; background: var(--sunken); border: 1px solid var(--line); }
  .pass { position: absolute; top: 0; bottom: 0; border-radius: 999px; background: color-mix(in srgb, var(--good) 22%, transparent); }
  .limit { position: absolute; top: -0.35rem; bottom: -0.35rem; width: 2px; margin-left: -1px; background: var(--text); }
  .value {
    position: absolute; top: 50%; width: 0.95rem; height: 0.95rem; margin: -0.475rem 0 0 -0.475rem;
    border-radius: 50%; border: 2px solid var(--surface); box-shadow: 0 1px 3px rgb(0 0 0 / 0.25);
  }
  .good .value { background: var(--good); }
  .bad .value { background: var(--bad); }
  .warn .value { background: var(--warn); }
  .scale { position: relative; height: 0; font-size: 0.78rem; font-variant-numeric: tabular-nums; }
  .tick { position: absolute; top: 0.35rem; transform: translateX(-50%); white-space: nowrap; color: var(--muted); }
  .value-label { color: var(--text); font-weight: 600; }
  .limit-label { top: -2.15rem; }
</style>

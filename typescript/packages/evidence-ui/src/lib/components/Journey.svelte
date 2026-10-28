<script lang="ts">
  import type { JourneyStep } from "../plain.ts";

  let { steps, label = "Progress" }: { steps: JourneyStep[]; label?: string } = $props();
  const words: Record<string, string> = { done: "done", current: "next", todo: "to come", failed: "stopped here" };
</script>

<ol class="journey" aria-label={label} data-testid="journey">
  {#each steps as step, i (step.label)}
    <li class={step.status} data-status={step.status} aria-current={step.status === "current" ? "step" : undefined}>
      <span class="mark" aria-hidden="true">
        {#if step.status === "done"}
          <svg viewBox="0 0 16 16" width="12" height="12"><path d="M3.5 8.5l3 3 6-7" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" /></svg>
        {:else if step.status === "failed"}
          <svg viewBox="0 0 16 16" width="12" height="12"><path d="M4.5 4.5l7 7M11.5 4.5l-7 7" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" /></svg>
        {:else}{i + 1}{/if}
      </span>
      <span class="label">{step.label}<span class="visually-hidden">: {words[step.status]}</span></span>
    </li>
  {/each}
</ol>

<style>
  .journey { position: relative; list-style: none; margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: 0.3rem 0; }
  li { display: flex; align-items: center; gap: 0.4rem; font-size: 0.88rem; color: var(--muted); }
  li:not(:last-child)::after { content: ""; width: 1.4rem; height: 2px; background: var(--line); margin: 0 0.45rem; border-radius: 1px; }
  li.done:not(:last-child)::after { background: color-mix(in srgb, var(--good) 55%, var(--line)); }
  .mark {
    width: 1.35rem; height: 1.35rem; border-radius: 50%; display: inline-grid; place-items: center;
    font-size: 0.75rem; font-weight: 700; border: 1.5px solid var(--line); background: var(--surface); color: var(--muted);
    font-variant-numeric: tabular-nums;
  }
  .done .mark { background: var(--good); border-color: var(--good); color: var(--surface); }
  .done { color: var(--text); }
  .current .mark { border-color: var(--accent); color: var(--accent); box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 18%, transparent); }
  .current { color: var(--text); font-weight: 650; }
  .failed .mark { background: var(--bad); border-color: var(--bad); color: var(--surface); }
  .failed { color: var(--bad); font-weight: 650; }
  @media (max-width: 640px) {
    li:not(:last-child)::after { width: 0.7rem; margin: 0 0.3rem; }
  }
  .visually-hidden { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
</style>

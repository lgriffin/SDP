<script lang="ts">
  import type { Tone } from "../view.ts";

  let { tone = "neutral", label }: { tone?: Tone; label: string } = $props();
</script>

<span class="badge {tone}" data-testid="badge"><span class="dot" aria-hidden="true"></span>{label}</span>

<style>
  .badge {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    padding: 0.12rem 0.6rem 0.12rem 0.5rem;
    border-radius: 999px;
    font-size: 0.85rem;
    font-weight: 600;
    white-space: nowrap;
    color: var(--text);
    background: var(--sunken);
    border: 1px solid var(--line);
  }
  .dot { width: 0.55rem; height: 0.55rem; border-radius: 50%; background: var(--muted); flex: none; }
  .good { background: color-mix(in srgb, var(--good) 12%, var(--surface)); border-color: color-mix(in srgb, var(--good) 35%, var(--line)); }
  .good .dot { background: var(--good); }
  .bad { background: color-mix(in srgb, var(--bad) 11%, var(--surface)); border-color: color-mix(in srgb, var(--bad) 35%, var(--line)); }
  .bad .dot { background: var(--bad); }
  .warn { background: color-mix(in srgb, var(--warn) 13%, var(--surface)); border-color: color-mix(in srgb, var(--warn) 35%, var(--line)); }
  .warn .dot { background: var(--warn); }
</style>

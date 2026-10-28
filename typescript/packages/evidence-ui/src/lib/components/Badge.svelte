<script lang="ts">
  import type { Tone } from "../view.ts";

  let { tone = "neutral", label }: { tone?: Tone; label: string } = $props();
</script>

<span class="badge {tone}" data-testid="badge">{label}</span>

<style>
  .badge {
    display: inline-block;
    padding: 0.1rem 0.5rem;
    border-radius: 999px;
    font-size: 0.8rem;
    font-weight: 600;
    letter-spacing: 0.02em;
    border: 1px solid currentColor;
    white-space: nowrap;
  }
  .good { color: var(--good); }
  .bad { color: var(--bad); }
  .warn { color: var(--warn); }
  .neutral { color: var(--muted); }
</style>

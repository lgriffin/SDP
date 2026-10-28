<script lang="ts">
  let { command, label = "Command" }: { command: string; label?: string } = $props();
  let copied = $state(false);

  async function copy(): Promise<void> {
    try {
      await navigator.clipboard.writeText(command);
      copied = true;
      setTimeout(() => (copied = false), 2000);
    } catch {
      copied = false;
    }
  }
</script>

<div class="copy" data-testid="copy-command">
  <code aria-label={label}>{command}</code>
  <button type="button" class="secondary" onclick={copy}>{copied ? "Copied" : "Copy"}</button>
</div>

<style>
  .copy { display: flex; gap: 0.5rem; align-items: flex-start; flex-wrap: wrap; margin: 0.5rem 0; }
  code {
    flex: 1 1 18rem;
    min-width: 0;
    overflow-wrap: anywhere;
    background: var(--sunken);
    border: 1px solid var(--line);
    border-radius: 8px;
    padding: 0.55rem 0.75rem;
    user-select: all;
  }
  button { flex: none; }
</style>

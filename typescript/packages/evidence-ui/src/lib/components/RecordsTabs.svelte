<script lang="ts">
  import { page } from "$app/state";
</script>

<nav class="tabs" aria-label="Records">
  <a href="/ledger" aria-current={page.url.pathname === "/ledger" ? "page" : undefined}>Signed ledger</a>
  <a href="/events" aria-current={page.url.pathname === "/events" ? "page" : undefined}>Activity log</a>
</nav>

<style>
  .tabs { display: flex; gap: 0.3rem; margin: 0 0 1.2rem; border-bottom: 1px solid var(--line); }
  .tabs a { padding: 0.5rem 0.9rem; text-decoration: none; color: var(--muted); border-bottom: 2px solid transparent; margin-bottom: -1px; font-weight: 550; }
  .tabs a[aria-current="page"] { color: var(--text); border-bottom-color: var(--accent); }
</style>

<script lang="ts">
  import { page } from "$app/state";
  import type { Snippet } from "svelte";

  let { children }: { children: Snippet } = $props();
  const links = [
    { href: "/", label: "Experiments" },
    { href: "/investigations", label: "Investigations" },
    { href: "/ledger", label: "Ledger" },
    { href: "/events", label: "Events" },
  ];
</script>

<header>
  <a class="brand" href="/">SDP evidence</a>
  <nav aria-label="Main">
    {#each links as link (link.href)}
      <a href={link.href} aria-current={page.url.pathname === link.href ? "page" : undefined}>{link.label}</a>
    {/each}
  </nav>
</header>

<main>
  {@render children()}
</main>

<style>
  :global(:root) {
    --bg: #fbfbf8;
    --surface: #ffffff;
    --text: #1d2321;
    --muted: #5d6763;
    --line: #dde2df;
    --accent: #2f6f4e;
    --good: #23704a;
    --bad: #b3261e;
    --warn: #8a5a00;
    color-scheme: light dark;
  }
  @media (prefers-color-scheme: dark) {
    :global(:root) {
      --bg: #141816;
      --surface: #1b201e;
      --text: #e6ebe8;
      --muted: #9aa5a0;
      --line: #2e3532;
      --accent: #7cc4a0;
      --good: #6fd19e;
      --bad: #ff8a80;
      --warn: #f0c060;
    }
  }
  :global(body) {
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
  }
  :global(a) { color: var(--accent); }
  :global(code), :global(.mono) { font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 0.85em; }
  :global(table) { width: 100%; border-collapse: collapse; background: var(--surface); }
  :global(th), :global(td) { text-align: left; padding: 0.45rem 0.6rem; border-bottom: 1px solid var(--line); vertical-align: top; }
  :global(th) { font-size: 0.8rem; color: var(--muted); font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em; }
  :global(.table-wrap) { overflow-x: auto; border: 1px solid var(--line); border-radius: 8px; }
  :global(h1) { font-size: 1.5rem; margin: 0 0 0.25rem; }
  :global(h1.mono) { font-size: 1.35rem; }
  :global(td.mono) { white-space: nowrap; }
  :global(h2) { font-size: 1.1rem; margin: 2rem 0 0.6rem; }
  :global(.muted) { color: var(--muted); }
  header {
    display: flex;
    gap: 1.5rem;
    align-items: center;
    flex-wrap: wrap;
    padding: 0.8rem 1rem;
    border-bottom: 1px solid var(--line);
    background: var(--surface);
  }
  .brand { font-weight: 700; text-decoration: none; color: var(--text); }
  nav { display: flex; gap: 1rem; }
  nav a { text-decoration: none; color: var(--muted); }
  nav a[aria-current="page"] { color: var(--accent); font-weight: 600; }
  main { max-width: 1100px; margin: 0 auto; padding: 1.5rem 1rem 3rem; }
</style>

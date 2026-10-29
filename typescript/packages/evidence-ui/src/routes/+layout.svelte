<script lang="ts">
  import { page } from "$app/state";
  import type { Snippet } from "svelte";

  let { children, data }: { children: Snippet; data: { demonstration: boolean } } = $props();
  const links = [
    { href: "/", label: "Home", match: (p: string) => p === "/" },
    { href: "/start", label: "Start", match: (p: string) => p.startsWith("/start") },
    { href: "/experiments", label: "Results", match: (p: string) => p.startsWith("/experiments") },
    { href: "/investigations", label: "Candidates", match: (p: string) => p.startsWith("/investigations") },
    { href: "/plans", label: "Downloads", match: (p: string) => p.startsWith("/plans") },
    { href: "/ledger", label: "Records", match: (p: string) => p.startsWith("/ledger") || p.startsWith("/events") },
    { href: "/help", label: "Help", match: (p: string) => p.startsWith("/help") },
  ];
</script>

<a class="skip" href="#main">Skip to content</a>
<header class="shell">
  <div class="shell-inner">
    <a class="brand" href="/">SDP <span>Scientific Discovery Platform</span></a>
    <nav aria-label="Main">
      {#each links as link (link.href)}
        <a href={link.href} aria-current={link.match(page.url.pathname) ? "page" : undefined}>{link.label}</a>
      {/each}
    </nav>
  </div>
</header>

{#if data.demonstration}
  <p class="demo" data-testid="demonstration">
    Demonstration server: everything here is synthetic data, and buttons change only this demonstration.
  </p>
{/if}

<main id="main">
  {@render children()}
</main>

<style>
  :global(:root) {
    --shell: #17392b;
    --shell-text: #eef5f0;
    --shell-muted: #a9c4b4;
    --bg: #f1f5f1;
    --surface: #ffffff;
    --sunken: #e7eee8;
    --text: #18211c;
    --muted: #536159;
    --line: #d3ddd5;
    --accent: #1d6a46;
    --accent-strong: #134f33;
    --on-accent: #ffffff;
    --good: #1d7547;
    --bad: #a3301d;
    --warn: #845800;
    --focus: #2f7dd1;
    --radius: 10px;
    --shadow: 0 1px 2px rgb(23 57 43 / 0.06), 0 4px 14px rgb(23 57 43 / 0.06);
    color-scheme: light dark;
  }
  @media (prefers-color-scheme: dark) {
    :global(:root) {
      --shell: #0c1d15;
      --shell-text: #e6efe9;
      --shell-muted: #8fb09d;
      --bg: #101814;
      --surface: #17221c;
      --sunken: #1d2a23;
      --text: #e4ece6;
      --muted: #9cb0a3;
      --line: #2b3a31;
      --accent: #7cc9a0;
      --accent-strong: #a3dcbd;
      --on-accent: #0c1d15;
      --good: #74d2a0;
      --bad: #ff9a85;
      --warn: #f0c36a;
      --focus: #8ab8f0;
      --shadow: 0 1px 2px rgb(0 0 0 / 0.3), 0 4px 14px rgb(0 0 0 / 0.25);
    }
  }
  :global(*, *::before, *::after) { box-sizing: border-box; }
  :global(body) {
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font: 16px/1.55 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    -webkit-font-smoothing: antialiased;
  }
  :global(::selection) { background: color-mix(in srgb, var(--accent) 28%, transparent); }
  :global(a) { color: var(--accent); text-underline-offset: 0.18em; text-decoration-thickness: 1px; }
  :global(a:hover) { color: var(--accent-strong); }
  :global(:focus-visible) { outline: 2px solid var(--focus); outline-offset: 2px; border-radius: 4px; }
  :global(code), :global(.mono) { font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace; font-size: 0.88em; }
  :global(.num) { font-variant-numeric: tabular-nums; }
  :global(h1) { font-size: clamp(1.6rem, 1.2rem + 1.4vw, 2.1rem); line-height: 1.2; margin: 0 0 0.4rem; letter-spacing: -0.01em; text-wrap: balance; }
  :global(h2) { font-size: 1.25rem; line-height: 1.3; margin: 2.4rem 0 0.7rem; text-wrap: balance; }
  :global(h3) { font-size: 1.05rem; margin: 1.4rem 0 0.4rem; }
  :global(p) { max-width: 70ch; }
  :global(.muted) { color: var(--muted); }
  :global(.lede) { font-size: 1.1rem; color: var(--muted); margin: 0 0 1.5rem; }
  :global(.crumbs) { font-size: 0.92rem; color: var(--muted); margin: 0 0 0.6rem; }
  :global(.sheet) { position: relative; background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); box-shadow: var(--shadow); padding: 1.1rem 1.25rem; }
  :global(.table-wrap) { position: relative; overflow-x: auto; border: 1px solid var(--line); border-radius: var(--radius); background: var(--surface); }
  :global(table) { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
  :global(th), :global(td) { text-align: left; padding: 0.6rem 0.75rem; border-bottom: 1px solid var(--line); vertical-align: top; }
  :global(tbody tr:last-child td) { border-bottom: 0; }
  :global(th) { font-size: 0.85rem; color: var(--muted); font-weight: 600; background: var(--sunken); }
  :global(td.mono) { white-space: nowrap; }
  :global(details > summary) { cursor: pointer; color: var(--accent); font-weight: 600; }
  :global(.button), :global(button) {
    font: inherit;
    font-weight: 600;
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    padding: 0.55rem 1rem;
    border-radius: 8px;
    border: 1px solid var(--accent);
    background: var(--accent);
    color: var(--on-accent);
    cursor: pointer;
    text-decoration: none;
    min-height: 2.75rem; /* 44 px, the WCAG 2.2 tap target */
  }
  :global(.button:hover), :global(button:hover) { background: var(--accent-strong); border-color: var(--accent-strong); color: var(--on-accent); }
  :global(button.secondary), :global(.button.secondary) { background: var(--surface); color: var(--accent); }
  :global(button.secondary:hover), :global(.button.secondary:hover) { background: var(--sunken); color: var(--accent-strong); }
  :global(button.danger) { background: var(--surface); color: var(--bad); border-color: var(--bad); }
  :global(button.danger:hover) { background: color-mix(in srgb, var(--bad) 10%, var(--surface)); color: var(--bad); }
  :global(button:disabled) { opacity: 0.55; cursor: not-allowed; }
  :global(input), :global(select), :global(textarea) {
    font: inherit;
    color: var(--text);
    background: var(--surface);
    border: 1px solid color-mix(in srgb, var(--muted) 55%, var(--line));
    border-radius: 8px;
    padding: 0.5rem 0.65rem;
    min-height: 2.75rem; /* 44 px, the WCAG 2.2 tap target */
    max-width: 100%;
  }
  :global(textarea) { width: 100%; min-height: 4.5rem; }
  :global(label) { font-weight: 600; }
  :global(.hint) { display: block; font-weight: 400; color: var(--muted); font-size: 0.9rem; }
  :global(.error-text) { color: var(--bad); }
  .skip { position: absolute; left: -999px; top: 0; }
  .skip:focus { left: 1rem; top: 0.5rem; background: var(--surface); padding: 0.4rem 0.8rem; z-index: 2; }
  .shell { background: var(--shell); color: var(--shell-text); }
  .shell-inner {
    max-width: 1120px;
    margin: 0 auto;
    padding: 0.7rem 1rem;
    display: flex;
    align-items: center;
    gap: 0.6rem 2rem;
    flex-wrap: wrap;
  }
  .brand { color: var(--shell-text); text-decoration: none; font-weight: 750; font-size: 1.15rem; letter-spacing: 0.02em; }
  .brand span { font-weight: 400; color: var(--shell-muted); font-size: 0.9rem; margin-left: 0.4rem; letter-spacing: 0; }
  nav { display: flex; gap: 0.25rem; overflow-x: auto; max-width: 100%; }
  nav a {
    color: var(--shell-muted);
    text-decoration: none;
    padding: 0.4rem 0.7rem;
    border-radius: 6px;
    white-space: nowrap;
    font-weight: 500;
  }
  nav a:hover { color: var(--shell-text); background: rgb(255 255 255 / 0.08); }
  nav a[aria-current="page"] { color: var(--shell-text); background: rgb(255 255 255 / 0.14); font-weight: 650; }
  .demo {
    max-width: none;
    margin: 0;
    padding: 0.5rem 1rem;
    text-align: center;
    font-size: 0.92rem;
    background: color-mix(in srgb, var(--warn) 14%, var(--bg));
    color: var(--text);
    border-bottom: 1px solid color-mix(in srgb, var(--warn) 30%, var(--line));
  }
  main { max-width: 1120px; margin: 0 auto; padding: 1.8rem 1rem 4rem; }
  @media (max-width: 640px) {
    .brand span { display: none; }
    .shell-inner { gap: 0.4rem; }
    nav { flex-wrap: wrap; overflow: visible; }
    nav a { padding: 0.35rem 0.55rem; }
  }
</style>

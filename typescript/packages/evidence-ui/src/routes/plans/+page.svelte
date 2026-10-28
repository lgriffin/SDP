<script lang="ts">
  import Badge from "$lib/components/Badge.svelte";
  import { fieldLabel, humanBytes, needsSignature, planHref, planStateLabel, planTone } from "$lib/plain.ts";
  import { shortHash } from "$lib/view.ts";

  let { data } = $props();
  const waiting = $derived(data.plans.filter((p) => p.state === "drafted"));
  const rest = $derived(data.plans.filter((p) => p.state !== "drafted"));
</script>

<svelte:head><title>Downloads · SDP</title></svelte:head>

<h1>Download plans</h1>
<p class="lede">
  Before SDP fetches data for a field, it writes a plan: what it will download, from where, how much, and why. Small
  plans are approved automatically; big ones need a signed approval.
</p>

{#if !data.planner}
  <p class="sheet" data-testid="planner-unavailable">
    This server does not offer download plans yet. Field explorations still run, and fetch what their tests need directly.
  </p>
{:else if data.plans.length === 0}
  <p data-testid="empty">No plans yet. Plans appear when you <a href="/start#field">explore a field</a> with “Plan the downloads”.</p>
{:else}
  {#each [{ title: "Waiting for a decision", list: waiting, id: "waiting" }, { title: "Decided", list: rest, id: "decided" }] as group (group.id)}
    {#if group.list.length}
      <h2>{group.title}</h2>
      <div class="table-wrap">
        <table data-testid="plans-{group.id}">
          <thead><tr><th>Plan</th><th>Field</th><th>Size</th><th>Status</th></tr></thead>
          <tbody>
            {#each group.list as p (p.id)}
              <tr>
                <td><a href={planHref(p.id)} class="mono">{shortHash(p.id)}</a></td>
                <td>{fieldLabel(p.document.intent.field)}</td>
                <td class="num">{humanBytes(p.document.cost.total_bytes)}{#if p.state === "drafted" && needsSignature(p)}<div class="muted small">needs a signed approval</div>{/if}</td>
                <td><Badge tone={planTone(p.state)} label={planStateLabel(p.state)} /></td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}
  {/each}
{/if}

<style>
  .small { font-size: 0.85rem; }
</style>

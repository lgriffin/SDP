<script lang="ts">
  import Badge from "$lib/components/Badge.svelte";
  import { stateTone, verdictTone } from "$lib/view.ts";

  let { data } = $props();
  const states = ["defined", "running", "failed", "completed", "validated", "approved", "published"];
</script>

<svelte:head><title>Experiments · SDP evidence</title></svelte:head>

<h1>Experiments</h1>
<p class="muted">Every experiment the kernel knows about, with its lifecycle state and verdict.</p>

<form method="get" class="filter">
  <label>
    State
    <select name="state" onchange={(e) => (e.currentTarget as HTMLSelectElement).form?.requestSubmit()}>
      <option value="" selected={!data.state}>all</option>
      {#each states as s (s)}
        <option value={s} selected={data.state === s}>{s}</option>
      {/each}
    </select>
  </label>
  <noscript><button>Filter</button></noscript>
</form>

{#if data.experiments.length === 0}
  <p data-testid="empty">No experiments{data.state ? ` in state ${data.state}` : ""}.</p>
{:else}
  <div class="table-wrap">
    <table data-testid="experiments">
      <thead>
        <tr><th>Experiment</th><th>Protocol</th><th>State</th><th>Verdict</th><th>Seed</th><th>Finished</th></tr>
      </thead>
      <tbody>
        {#each data.experiments as e (e.id)}
          <tr>
            <td><a href="/experiments/{e.id}" class="mono">{e.id}</a></td>
            <td>{e.protocol} <span class="muted">{e.protocolVersion}</span></td>
            <td><Badge tone={stateTone(e.state)} label={e.state} /></td>
            <td>
              {#if e.verdict}<Badge tone={verdictTone(e.verdict.status)} label={e.verdict.status} />{:else}<span class="muted">—</span>{/if}
            </td>
            <td class="mono">{e.seed}</td>
            <td class="muted">{e.finishedAt ?? "—"}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
{/if}

<style>
  .filter { margin: 1rem 0; }
  select { margin-left: 0.4rem; padding: 0.2rem; }
</style>

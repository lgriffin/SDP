<script lang="ts">
  import Badge from "$lib/components/Badge.svelte";
  import { experimentStateLabel, humanTime, programmeName, verdictTitle } from "$lib/plain.ts";
  import { stateTone, verdictTone } from "$lib/view.ts";

  let { data } = $props();
  const states = ["defined", "running", "failed", "completed", "validated", "approved", "published"];
  const submit = (e: Event) => (e.currentTarget as HTMLSelectElement).form?.requestSubmit();
</script>

<svelte:head><title>Results · SDP</title></svelte:head>

<h1>Results</h1>
<p class="lede">Every hypothesis test, with where it is and what the checks decided.</p>

<form method="get" class="filter">
  <label>
    Programme
    <select name="programme" onchange={submit}>
      <option value="" selected={!data.programme}>All programmes</option>
      {#each data.programmes as p (p)}<option value={p} selected={data.programme === p}>{programmeName(p)}</option>{/each}
    </select>
  </label>
  <label>
    State
    <select name="state" onchange={submit}>
      <option value="" selected={!data.state}>Any state</option>
      {#each states as s (s)}<option value={s} selected={data.state === s}>{experimentStateLabel(s)}</option>{/each}
    </select>
  </label>
  <noscript><button>Show</button></noscript>
</form>

{#if data.experiments.length === 0}
  <p data-testid="empty">No experiments{data.state ? ` in state ${data.state}` : ""}.</p>
{:else}
  <div class="table-wrap">
    <table data-testid="experiments">
      <thead>
        <tr><th>Experiment</th><th>Programme</th><th>Where it is</th><th>Verdict</th><th>Finished</th></tr>
      </thead>
      <tbody>
        {#each data.experiments as e (e.id)}
          <tr>
            <td><a href="/experiments/{e.id}" class="mono">{e.id}</a></td>
            <td>{programmeName(e.protocol)} <span class="muted">v{e.protocolVersion}</span></td>
            <td><Badge tone={stateTone(e.state)} label={experimentStateLabel(e.state)} /></td>
            <td>
              {#if e.verdict}<Badge tone={verdictTone(e.verdict.status)} label={verdictTitle(e.verdict.status)} />{:else}<span class="muted">None yet</span>{/if}
            </td>
            <td class="muted">{humanTime(e.finishedAt)}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
{/if}

<style>
  .filter { display: flex; gap: 1rem 1.5rem; flex-wrap: wrap; margin: 0 0 1.2rem; }
  .filter label { display: flex; flex-direction: column; gap: 0.3rem; font-size: 0.92rem; }
</style>

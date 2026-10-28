<script lang="ts">
  import Badge from "$lib/components/Badge.svelte";
  import Journey from "$lib/components/Journey.svelte";
  import { humanTime, setJourney } from "$lib/plain.ts";
  import { shortHash } from "$lib/view.ts";

  let { data } = $props();
</script>

<svelte:head><title>Candidates · SDP</title></svelte:head>

<h1>Investigations</h1>
<p class="lede">
  Each entry is one exploration of a field under a set of tests. Everything here is <strong>exploratory</strong>: a
  candidate becomes evidence only through a hypothesis test planned in advance.
</p>

{#if data.sets.length === 0}
  <p data-testid="empty">No fields have been explored yet. <a href="/start#field">Explore one</a>.</p>
{:else}
  <div class="table-wrap">
    <table data-testid="sets">
      <thead>
        <tr><th>Exploration</th><th>Tests</th><th>Field</th><th>Progress</th><th>Objects</th><th>Candidates</th><th>Started</th></tr>
      </thead>
      <tbody>
        {#each data.sets as s (s.id)}
          <tr>
            <td><a href="/investigations/{s.id}" class="mono">{s.id}</a></td>
            <td>{s.profileId} <span class="muted">v{s.profileVersion}</span><br /><span class="mono muted" title={s.profileHash}>{shortHash(s.profileHash)}</span></td>
            <td class="mono">{s.fieldId}</td>
            <td><Journey steps={setJourney(s, null, data.openBySet[s.id] ?? 0)} label="Progress of {s.id}" /></td>
            <td class="num">{s.objectsExamined}</td>
            <td class="num">{s.investigationIds.length}</td>
            <td class="muted">{humanTime(s.startedAt)}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
  <p class="muted note"><Badge tone="warn" label="Exploratory" /> applies to every candidate on these pages.</p>
{/if}

<style>
  .note { margin-top: 1rem; }
</style>

<script lang="ts">
  import Badge from "$lib/components/Badge.svelte";
  import { shortHash, stageTone } from "$lib/view.ts";

  let { data } = $props();
</script>

<svelte:head><title>Investigations · SDP evidence</title></svelte:head>

<h1>Investigations</h1>
<p class="muted">
  Each set is one run of Investigate over a field under a versioned profile. Everything here is
  <strong>EXPLORATORY</strong>: a candidate becomes evidence only through a predeclared experiment.
</p>

{#if data.sets.length === 0}
  <p data-testid="empty">No investigation sets yet. Run <code>sdp investigate --region … --profile …</code>.</p>
{:else}
  <div class="table-wrap">
    <table data-testid="sets">
      <thead>
        <tr><th>Set</th><th>Profile</th><th>Field</th><th>Stages</th><th>Examined</th><th>Candidates</th><th>Started</th></tr>
      </thead>
      <tbody>
        {#each data.sets as s (s.id)}
          <tr>
            <td><a href="/investigations/{s.id}" class="mono">{s.id}</a></td>
            <td>{s.profileId} <span class="muted">{s.profileVersion}</span><br /><span class="mono muted" title={s.profileHash}>{shortHash(s.profileHash)}</span></td>
            <td class="mono">{s.fieldId}</td>
            <td>
              {#each s.stages as st (st.stage)}<Badge tone={stageTone(st.state)} label={st.stage} /> {/each}
            </td>
            <td class="mono">{s.objectsExamined}</td>
            <td class="mono">{s.investigationIds.length}</td>
            <td class="muted">{s.startedAt}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
{/if}

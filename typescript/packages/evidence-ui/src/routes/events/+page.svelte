<script lang="ts">
  import RecordsTabs from "$lib/components/RecordsTabs.svelte";
  import { humanTime } from "$lib/plain.ts";

  let { data } = $props();
  const last = $derived(data.events.at(-1)?.sequence);
</script>

<svelte:head><title>Activity log · SDP</title></svelte:head>

<h1>Records</h1>
<RecordsTabs />
<p class="lede">Everything the system did, oldest first, written at the same moment as the change it describes.</p>

<div class="table-wrap">
  <table data-testid="events">
    <thead><tr><th>#</th><th>Event</th><th>Aggregate</th><th>Occurred</th></tr></thead>
    <tbody>
      {#each data.events as ev (ev.sequence)}
        <tr>
          <td class="mono">{ev.sequence}</td>
          <td>{ev.name}</td>
          <td>
            {#if ev.aggregateId.startsWith("exp-")}<a href="/experiments/{ev.aggregateId}" class="mono">{ev.aggregateId}</a>
            {:else}<span class="mono">{ev.aggregateId}</span>{/if}
          </td>
          <td class="muted">{humanTime(ev.occurredAt)}</td>
        </tr>
      {:else}
        <tr><td colspan="4" class="muted">No events after #{data.after}.</td></tr>
      {/each}
    </tbody>
  </table>
</div>
{#if data.events.length === 200 && last !== undefined}
  <p><a href="/events?after={last}">Later events</a></p>
{/if}

<script lang="ts">
  let { data } = $props();
  const last = $derived(data.events.at(-1)?.sequence);
</script>

<svelte:head><title>Events · SDP evidence</title></svelte:head>

<h1>Domain events</h1>
<p class="muted">Appended in the same transaction as the state change they describe (KRN-010), oldest first.</p>

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
          <td class="muted">{ev.occurredAt}</td>
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

<script lang="ts">
  import Badge from "$lib/components/Badge.svelte";
  import { isSelfApproval, shortHash } from "$lib/view.ts";

  let { data } = $props();
</script>

<svelte:head><title>Ledger · SDP evidence</title></svelte:head>

<h1>Ledger</h1>
<p class="muted">
  Signed, hash-chained entries: one per frozen predeclaration and one per reviewer approval. Signatures and hashes
  are verified by <code>sdp ledger verify</code>; this page checks that the chain is unbroken.
</p>

{#if data.problems.length}
  <ul class="problems" data-testid="chain-problems">
    {#each data.problems as p (p.entry + p.message)}<li><span class="mono">{p.entry}</span>: {p.message}</li>{/each}
  </ul>
{:else}
  <p data-testid="chain-ok"><Badge tone="good" label="chain intact" /> {data.entries.length} entries</p>
{/if}

<div class="table-wrap">
  <table data-testid="ledger">
    <thead>
      <tr><th>#</th><th>Kind</th><th>Subject</th><th>Signer</th><th>Signature</th><th>Recorded</th><th>Previous</th></tr>
    </thead>
    <tbody>
      {#each data.entries as entry (entry.id)}
        <tr>
          <td class="mono">{entry.sequence}</td>
          <td>{entry.kind}{#if isSelfApproval(entry)} <Badge tone="warn" label="self-approval" />{/if}</td>
          <td>
            {#if entry.kind === "approval"}<a href="/experiments/{entry.subjectId}" class="mono">{entry.subjectId}</a>
            {:else}<span class="mono">{entry.subjectId}</span>{/if}
            <div class="mono muted" title={entry.subjectHash}>{shortHash(entry.subjectHash)}</div>
          </td>
          <td>{entry.signer}</td>
          <td class="mono">{entry.signatureMethod}</td>
          <td class="muted">{entry.recordedAt}</td>
          <td class="mono muted" title={entry.previousEntryHash ?? ""}>{shortHash(entry.previousEntryHash)}</td>
        </tr>
      {/each}
    </tbody>
  </table>
</div>

<style>
  .problems { color: var(--bad); }
</style>

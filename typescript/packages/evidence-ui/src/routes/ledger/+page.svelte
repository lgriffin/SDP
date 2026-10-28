<script lang="ts">
  import Badge from "$lib/components/Badge.svelte";
  import RecordsTabs from "$lib/components/RecordsTabs.svelte";
  import { humanTime } from "$lib/plain.ts";
  import { isSelfApproval, shortHash } from "$lib/view.ts";

  let { data } = $props();
</script>

<svelte:head><title>Ledger · SDP</title></svelte:head>

<h1>Records</h1>
<RecordsTabs />
<p class="lede">
  The signed ledger holds one entry for every set of checks frozen before a run, and one for every reviewer's
  approval. Each entry names the one before it, so nothing can be quietly changed or removed.
</p>
<p class="muted">This page checks that the chain is unbroken; <code>sdp ledger verify</code> also checks every signature.</p>

{#if data.problems.length}
  <ul class="problems" data-testid="chain-problems">
    {#each data.problems as p (p.entry + p.message)}<li><span class="mono">{p.entry}</span>: {p.message}</li>{/each}
  </ul>
{:else}
  <p data-testid="chain-ok"><Badge tone="good" label="Chain intact" /> {data.entries.length} entries</p>
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
          <td class="muted">{humanTime(entry.recordedAt)}</td>
          <td class="mono muted" title={entry.previousEntryHash ?? ""}>{shortHash(entry.previousEntryHash)}</td>
        </tr>
      {/each}
    </tbody>
  </table>
</div>

<style>
  .problems { color: var(--bad); }
</style>

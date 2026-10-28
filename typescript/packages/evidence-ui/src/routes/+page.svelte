<script lang="ts">
  import Badge from "$lib/components/Badge.svelte";
  import Journey from "$lib/components/Journey.svelte";
  import {
    experimentJourney,
    experimentNextStep,
    experimentStateLabel,
    fieldLabel,
    humanTime,
    programmeName,
    setJourney,
    verdictTitle,
    waitingItems,
  } from "$lib/plain.ts";
  import { verdictTone } from "$lib/view.ts";

  let { data } = $props();
  const waiting = $derived(waitingItems(data.experiments, data.sets, data.openBySet, data.plans));
  const planFor = (setId: string) => data.plans.find((p) => p.setId === setId) ?? null;
</script>

<svelte:head><title>Home · SDP</title></svelte:head>

<div class="intro">
  <div>
    <h1>Your work</h1>
    <p class="lede">Everything that has been started, where each piece is, and the next step for it.</p>
  </div>
  <a class="button" href="/start">Start something new</a>
</div>

<section aria-labelledby="waiting-h">
  <h2 id="waiting-h">Waiting for you</h2>
  {#if waiting.length === 0}
    <p class="muted" data-testid="nothing-waiting">Nothing is waiting for you right now.</p>
  {:else}
    <ul class="waiting" data-testid="waiting">
      {#each waiting as item (item.href + item.action)}
        <li>
          <div>
            <a href={item.href} class="title">{item.title}</a>
            <div class="muted">{item.detail}</div>
          </div>
          <a class="button secondary" href={item.href}>{item.action}</a>
        </li>
      {/each}
    </ul>
  {/if}
</section>

<section aria-labelledby="work-h">
  <h2 id="work-h">Hypothesis tests</h2>
  {#if data.experiments.length === 0}
    <p class="muted">No experiments yet. <a href="/start">Start one</a>.</p>
  {:else}
    <ul class="work" data-testid="work-experiments">
      {#each data.experiments as e (e.id)}
        {@const step = experimentNextStep(e)}
        <li>
          <div class="what">
            <a href="/experiments/{e.id}" class="title">{programmeName(e.protocol)}</a>
            <div class="muted meta"><span class="mono">{e.id}</span> · {e.finishedAt ? `finished ${humanTime(e.finishedAt)}` : experimentStateLabel(e.state)}</div>
          </div>
          <div class="where"><Journey steps={experimentJourney(e.state)} label="Progress of {e.id}" /></div>
          <div class="outcome">
            {#if e.verdict}<Badge tone={verdictTone(e.verdict.status)} label={verdictTitle(e.verdict.status)} />
            {:else if e.state === "failed"}<Badge tone="bad" label="Run failed" />{/if}
          </div>
          <div class="next">
            {#if step.kind !== "none" && step.kind !== "wait"}
              <a class="button secondary" href="/experiments/{e.id}">{step.label}</a>
            {/if}
          </div>
        </li>
      {/each}
    </ul>
  {/if}

  <h2>Field investigations</h2>
  {#if data.sets.length === 0}
    <p class="muted">No fields explored yet. <a href="/start#field">Explore one</a>.</p>
  {:else}
    <ul class="work" data-testid="work-sets">
      {#each data.sets as s (s.id)}
        {@const open = data.openBySet[s.id] ?? 0}
        <li>
          <div class="what">
            <a href="/investigations/{s.id}" class="title">{s.profileId} on {s.fieldId}</a>
            <div class="muted meta"><span class="mono">{s.id}</span> · started {humanTime(s.startedAt)} · {s.objectsExamined} objects examined</div>
          </div>
          <div class="where"><Journey steps={setJourney(s, planFor(s.id), open)} label="Progress of {s.id}" /></div>
          <div class="outcome"><Badge tone="warn" label="Exploratory" /></div>
          <div class="next">
            {#if open > 0}<a class="button secondary" href="/investigations/{s.id}">Review {open} candidate{open === 1 ? "" : "s"}</a>{/if}
          </div>
        </li>
      {/each}
    </ul>
  {/if}
</section>

{#if !data.planner}
  <p class="muted planner-note">Download plans will appear here once this server offers the planner.</p>
{/if}

<style>
  .intro { display: flex; justify-content: space-between; align-items: flex-start; gap: 1rem; flex-wrap: wrap; }
  .intro .lede { margin-bottom: 0; }
  ul { list-style: none; margin: 0; padding: 0; }
  .waiting { display: grid; gap: 0.6rem; }
  .waiting li {
    display: flex; justify-content: space-between; align-items: center; gap: 1rem; flex-wrap: wrap;
    background: var(--surface); border: 1px solid color-mix(in srgb, var(--warn) 30%, var(--line));
    border-radius: var(--radius); padding: 0.8rem 1rem; box-shadow: var(--shadow);
  }
  .title { font-weight: 650; font-size: 1.02rem; }
  .work { background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); box-shadow: var(--shadow); }
  .work li {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto auto;
    grid-template-areas: "what outcome next" "where outcome next";
    gap: 0.5rem 1.2rem;
    align-items: center;
    padding: 0.9rem 1.1rem;
    border-bottom: 1px solid var(--line);
  }
  .work li:last-child { border-bottom: 0; }
  .what { grid-area: what; }
  .where { grid-area: where; }
  .outcome { grid-area: outcome; }
  .next { grid-area: next; justify-self: end; }
  .meta { font-size: 0.9rem; overflow-wrap: anywhere; }
  .planner-note { margin-top: 2rem; }
  @media (max-width: 700px) {
    .work li { grid-template-columns: minmax(0, 1fr) auto; grid-template-areas: "what outcome" "where where" "next next"; }
    .next { justify-self: start; }
  }
</style>

<script lang="ts">
  import { enhance } from "$app/forms";
  import CopyCommand from "$lib/components/CopyCommand.svelte";
  import { experimentStateLabel, programmeName } from "$lib/plain.ts";

  let { data, form } = $props();
  const values = $derived((form?.values ?? {}) as Record<string, string>);
  const v = (name: string, fallback: string) => values[name] ?? fallback;
  const invalid = (name: string) => (form?.field === name ? true : undefined);
  let chosen = $state("");
  const profileKey = $derived(chosen || values.profile || data.profiles[0]?.key || "");
  const description = $derived(data.profiles.find((p) => p.key === profileKey)?.description ?? "");
  let busy = $state(false);
  const working = () => {
    busy = true;
    return async ({ update }: { update: () => Promise<void> }) => {
      await update();
      busy = false;
    };
  };
</script>

<svelte:head><title>Start · SDP</title></svelte:head>

<h1>Start something new</h1>
<p class="lede">Explore a patch of sky for candidates, or run a hypothesis test that has already been planned.</p>

<section class="sheet" aria-labelledby="field">
  <h2 id="field">Explore a field</h2>
  <p>
    SDP finds every object in the field, gathers what the archives hold on each, runs the tests you choose, and
    ranks the candidates. What it finds is <strong>exploratory</strong>: a starting point for a proper test, never a
    conclusion.
  </p>

  {#if data.profiles.length === 0}
    <p class="error-text" data-testid="no-profiles">
      No test profiles are available. Ask whoever runs this server to set <code>SDP_PROFILES_DIR</code> to the folder of profile files.
    </p>
  {:else}
    <form method="POST" action="?/{data.planner ? 'plan' : 'investigate'}" use:enhance={working} data-testid="field-form" novalidate>
      {#if form?.error}<p class="error-text" role="alert" data-testid="form-error">{form.error}</p>{/if}

      <div class="grid">
        <label class="wide">
          Name for this field <span class="hint">Optional, so you can recognise it later, e.g. “Hyades outskirts”.</span>
          <input name="label" value={v("label", "")} />
        </label>
        <label>
          Right ascension <span class="hint">Centre, in degrees (0 to 360)</span>
          <input name="ra" inputmode="decimal" required value={v("ra", "")} aria-invalid={invalid("ra")} />
        </label>
        <label>
          Declination <span class="hint">Centre, in degrees (−90 to 90)</span>
          <input name="dec" inputmode="decimal" required value={v("dec", "")} aria-invalid={invalid("dec")} />
        </label>
        <label>
          Radius <span class="hint">In degrees; 0.5 is about a full Moon</span>
          <input name="radius" inputmode="decimal" required value={v("radius", "0.5")} aria-invalid={invalid("radius")} />
        </label>
        <label class="wide">
          Tests to run
          <select name="profile" onchange={(e) => (chosen = e.currentTarget.value)} aria-invalid={invalid("profile")}>
            {#each data.profiles as p (p.key)}<option value={p.key} selected={p.key === profileKey}>{p.key}</option>{/each}
          </select>
          {#if description}<span class="hint">{description}</span>{/if}
        </label>
        <label class="wide">
          Your name <span class="hint">Recorded with the run, so others know who started it.</span>
          <input name="actor" autocomplete="name" required value={v("actor", data.actor)} aria-invalid={invalid("actor")} />
        </label>
      </div>

      <details class="more">
        <summary>More options</summary>
        <div class="grid">
          <label>
            Epoch <span class="hint">Julian year the positions refer to</span>
            <input name="epoch" inputmode="decimal" value={v("epoch", "2016.0")} aria-invalid={invalid("epoch")} />
          </label>
          <label>
            Random seed <span class="hint">Keep it to reproduce a run exactly</span>
            <input name="seed" inputmode="numeric" value={v("seed", "0")} aria-invalid={invalid("seed")} />
          </label>
        </div>
      </details>

      <div class="actions">
        {#if data.planner}
          <button disabled={busy}>{busy ? "Working…" : "Plan the downloads"}</button>
          <button class="secondary" formaction="?/investigate" disabled={busy}>Explore without a plan</button>
          <p class="hint">Planning first shows what will be downloaded and how much, before anything is fetched.</p>
        {:else}
          <button disabled={busy}>{busy ? "Exploring… this can take a few minutes" : "Explore this field"}</button>
        {/if}
      </div>
    </form>
  {/if}
</section>

<section class="sheet" aria-labelledby="ready-h">
  <h2 id="ready-h">Run a planned hypothesis test</h2>
  <p>These experiments have their checks frozen and their inputs in place. Open one to run it.</p>
  {#if data.ready.length === 0}
    <p class="muted" data-testid="none-ready">None are waiting to run.</p>
  {:else}
    <ul class="ready" data-testid="ready">
      {#each data.ready as e (e.id)}
        <li>
          <span><strong>{programmeName(e.protocol)}</strong> <span class="mono muted">{e.id}</span> · {experimentStateLabel(e.state)}</span>
          <a class="button secondary" href="/experiments/{e.id}">Open</a>
        </li>
      {/each}
    </ul>
  {/if}
</section>

<section class="sheet" aria-labelledby="new-h">
  <h2 id="new-h">Plan a new hypothesis test</h2>
  <p>
    A new test of Einstein 1919 or the stellar-cluster programme starts by freezing its checks, so nobody can move
    the goalposts after seeing the data. Freezing is signed, so it happens in a terminal, usually by whoever
    maintains the protocol:
  </p>
  <CopyCommand command="sdp predeclaration freeze uc02-einstein-1919 --signer <your-signer-name>" />
  <p class="muted">
    Then <code>sdp experiment define</code> binds the inputs. The experiment appears under “Run a planned hypothesis test” above.
    <a href="/help#freezing">Why freezing matters</a>.
  </p>
</section>

<style>
  section.sheet { margin-bottom: 1.2rem; }
  section.sheet h2 { margin-top: 0; }
  .grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 1rem 1.2rem; margin: 1rem 0; }
  .grid label { display: flex; flex-direction: column; gap: 0.3rem; }
  .grid .wide { grid-column: 1 / -1; }
  .grid input, .grid select { width: 100%; }
  [aria-invalid="true"] { border-color: var(--bad); box-shadow: 0 0 0 2px color-mix(in srgb, var(--bad) 20%, transparent); }
  .more { margin: 0.4rem 0 1rem; }
  .actions { display: flex; flex-wrap: wrap; gap: 0.6rem; align-items: center; }
  .actions .hint { flex-basis: 100%; }
  .ready { list-style: none; padding: 0; margin: 0; }
  .ready li { display: flex; justify-content: space-between; align-items: center; gap: 1rem; flex-wrap: wrap; padding: 0.6rem 0; border-top: 1px solid var(--line); }
  @media (max-width: 700px) {
    .grid { grid-template-columns: 1fr; }
  }
</style>

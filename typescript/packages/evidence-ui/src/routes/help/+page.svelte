<svelte:head><title>Help · SDP</title></svelte:head>

<h1>How SDP works</h1>
<p class="lede">The few ideas behind every screen, in plain words.</p>

<div class="terms">
  <section id="freezing">
    <h2>Freezing the checks first</h2>
    <p>
      Before a hypothesis test runs, its checks are written down and signed: what will be measured, and the limit it
      must meet. This is the <strong>predeclaration</strong>. Because it is frozen first, nobody can move the
      goalposts after seeing the data. Freezing is signed, so it is done in a terminal.
    </p>
  </section>

  <section id="checks">
    <h2>Checks and verdicts</h2>
    <p>
      After a run, each check (a <strong>gate</strong>) compares a measurement with its frozen limit. A
      <strong>required</strong> check that fails rejects the hypothesis. An <strong>advisory</strong> check that fails
      leaves a candidate needing more evidence. The scale under each check shows where the measurement fell and the
      shaded side where it passes.
    </p>
    <ul>
      <li><strong>Verified</strong>: every check passed.</li>
      <li><strong>Rejected</strong>: a required check failed.</li>
      <li><strong>Inconclusive</strong>: a required check could not be decided, for example because its reference values are not yet verified.</li>
      <li><strong>Candidate</strong>: required checks passed, an advisory one did not.</li>
    </ul>
  </section>

  <section id="exploratory">
    <h2>Exploratory candidates</h2>
    <p>
      Exploring a field means looking at the data to see what stands out. Whatever stands out is a
      <strong>candidate</strong>, and it stays <strong>exploratory</strong>: looking first and testing later on the
      same data proves nothing. A candidate becomes a finding only through a new hypothesis test, planned in advance,
      that uses evidence the exploration did not.
    </p>
  </section>

  <section id="null">
    <h2>Null fields: how often things fire by chance</h2>
    <p>
      Every test is also run on shuffled or synthetic versions of the field, where nothing real should turn up. The
      share of objects that still pass is the test's rate by chance. A p-value below α means little if a quarter of a
      null field is also below α.
    </p>
  </section>

  <section id="triage">
    <h2>Reviewing candidates</h2>
    <p>
      <strong>Park</strong> a candidate to come back to it, <strong>close</strong> it when it is not worth pursuing
      (closing is final), or add a note. Every step records who took it and why.
    </p>
  </section>

  <section id="plans">
    <h2>Download plans</h2>
    <p>
      Before fetching data for a field, SDP can write a plan: each file or query, its size, and the test it serves.
      Small plans are approved by anyone; plans above the threshold need a signed approval, because they use
      significant storage and archive time.
    </p>
  </section>

  <section id="terminal">
    <h2>Why some steps need a terminal</h2>
    <p>
      Freezing checks, approving a result, promoting a candidate and approving a large plan are signed with a
      personal key, so they stay on the <code>sdp</code> command line. Wherever that applies, the page shows the
      exact command with a Copy button.
    </p>
  </section>
</div>

<style>
  .terms { display: grid; gap: 0.2rem; max-width: 72ch; }
  section h2 { margin-top: 1.8rem; }
  section:target h2 { color: var(--accent); }
</style>

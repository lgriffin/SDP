<script lang="ts">
  import { page } from "$app/state";
</script>

<svelte:head><title>{page.status === 404 ? "Not found" : "Problem"} · SDP</title></svelte:head>

<h1>{page.status === 404 ? "We could not find that" : "Something went wrong"}</h1>
<p data-testid="error">{page.error?.message}</p>
<p><a class="button secondary" href="/">Back to your work</a></p>

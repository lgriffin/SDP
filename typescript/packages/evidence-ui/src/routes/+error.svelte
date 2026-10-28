<script lang="ts">
  import { page } from "$app/state";
</script>

<h1>{page.status}</h1>
<p data-testid="error">{page.error?.message}</p>
<p><a href="/">Back to experiments</a></p>

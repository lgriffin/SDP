<script lang="ts">
  // A calibrated image, north up, with each Gaia star drawn where its motion puts it on the image's
  // date (INV-020). The pixels come from the image-counterpart test, in the frame it aligned to Gaia,
  // so nothing here converts coordinates: FITS pixel (x, y) is drawn at (x + 0.5, height - 0.5 - y).
  // Shape carries the status as well as colour: circle where expected, dashed circle ambiguous,
  // diamond off its place, square missing; a cross marks the detection the test matched.
  import { STAR_WORDS, starStatus } from "../plain.ts";
  import type { FieldStar } from "../ports.ts";

  let {
    src,
    width,
    height,
    stars,
    setId,
    focus = null,
    crop = 0,
    label,
  }: {
    src: string;
    width: number;
    height: number;
    stars: FieldStar[];
    setId: string;
    /** The star to centre on; with ``crop``, only a square of that many pixels around it is shown. */
    focus?: string | null;
    crop?: number;
    label: string;
  } = $props();

  let markers = $state(true);
  const sx = (x: number) => x + 0.5;
  const sy = (y: number) => height - 0.5 - y;
  const centre = $derived(stars.find((s) => s.object_key === focus) ?? null);
  const box = $derived.by(() => {
    if (!centre || !crop) return { x: 0, y: 0, w: width, h: height };
    const [x, y] = centre.predicted_px;
    return { x: sx(x) - crop / 2, y: sy(y) - crop / 2, w: crop, h: crop };
  });
  // Marker size follows the view, so markers look the same size zoomed in or out.
  const r = $derived(Math.max(box.w, box.h) / 70);
  const shown = $derived(
    stars.filter((s) => {
      const [x, y] = s.predicted_px;
      return sx(x) >= box.x - r && sx(x) <= box.x + box.w + r && sy(y) >= box.y - r && sy(y) <= box.y + box.h + r;
    }),
  );
</script>

<figure class="field-image" data-testid="field-image">
  <svg viewBox="{box.x} {box.y} {box.w} {box.h}" style:aspect-ratio="{box.w} / {box.h}" role="img" aria-label={label} preserveAspectRatio="xMidYMid meet">
    <image href={src} x="0" y="0" {width} {height} preserveAspectRatio="none" style="image-rendering: {crop ? 'pixelated' : 'auto'}" />
    {#if markers}
      {#each shown as s (s.object_key)}
        {@const status = starStatus(s)}
        {@const x = sx(s.predicted_px[0])}
        {@const y = sy(s.predicted_px[1])}
        {@const words = `${s.object_key}: ${STAR_WORDS[status].toLowerCase()}`}
        <svelte:element
          this={s.investigation_id ? "a" : "g"}
          href={s.investigation_id ? `/investigations/${setId}/${s.investigation_id}` : undefined}
          class="star {status}"
          class:focus={s.object_key === focus}
          data-status={status}
          data-key={s.object_key}
          aria-label={s.investigation_id ? words : undefined}
        >
          <title>{words}</title>
          {#if s.counterpart_px && (status !== "matched" || s.object_key === focus)}
            {@const cx = sx(s.counterpart_px[0])}
            {@const cy = sy(s.counterpart_px[1])}
            <line x1={x} y1={y} x2={cx} y2={cy} class="link" />
            <path d="M{cx - r * 0.6},{cy}h{r * 1.2}M{cx},{cy - r * 0.6}v{r * 1.2}" class="cross" />
          {/if}
          {#if status === "missing"}
            <rect x={x - r} y={y - r} width={r * 2} height={r * 2} class="mark" />
          {:else if status === "displaced"}
            <path d="M{x},{y - r * 1.25}L{x + r * 1.25},{y}L{x},{y + r * 1.25}L{x - r * 1.25},{y}Z" class="mark" />
          {:else}
            <circle cx={x} cy={y} {r} class="mark" />
          {/if}
        </svelte:element>
      {/each}
    {/if}
  </svg>
  <figcaption>
    <label class="toggle"><input type="checkbox" bind:checked={markers} /> Show Gaia stars</label>
  </figcaption>
</figure>

<style>
  .field-image { margin: 0; }
  svg { display: block; width: 100%; height: auto; max-height: 78vh; background: #000; border-radius: var(--radius); }
  figcaption { display: flex; justify-content: flex-end; padding-top: 0.4rem; font-size: 0.88rem; color: var(--muted); }
  .toggle { display: inline-flex; gap: 0.4rem; align-items: center; cursor: pointer; }
  .mark, .link, .cross { fill: none; vector-effect: non-scaling-stroke; }
  .mark { stroke-width: 1.6; }
  .matched .mark { stroke: #7fd49b; }
  .ambiguous .mark { stroke: #f2c14e; stroke-dasharray: 4 3; stroke-width: 2; }
  .displaced .mark, .missing .mark { stroke: #ff7a6b; stroke-width: 2.4; }
  .link { stroke: #ffffff; stroke-opacity: 0.7; stroke-width: 1; }
  .cross { stroke: #9ecbff; stroke-width: 1.6; }
  .focus .mark { stroke-width: 3; }
  a.star { cursor: pointer; }
  a.star:focus-visible .mark, a.star:hover .mark { stroke: #ffffff; stroke-width: 3; }
  a.star:focus { outline: none; }
</style>

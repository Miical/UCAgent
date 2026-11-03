<template>
  <section class="overlay-panel">
    <ul v-if="summary.length">
      <li v-for="entry in summary" :key="entry.name">
        <label class="overlay-row">
          <input
            type="checkbox"
            :checked="store.enabledOverlays.has(entry.name)"
            :disabled="!entry.known"
            @change="store.toggleOverlay(entry.name)"
          />
          <span class="overlay-name">{{ entry.name }}</span>
          <span v-if="!entry.known" class="tag-unknown">unknown</span>
        </label>
        <ul v-if="entry.known && entry.name !== 'phys' && legendFor(entry.name).length" class="legend">
          <li v-for="item in legendFor(entry.name)" :key="item.label">
            <span
              class="swatch"
              :class="`swatch-${item.kind || 'fill'}`"
              :style="swatchStyle(item)"
            ></span>
            {{ item.label }}
          </li>
          <li
            v-if="tbScopeNoteFor(entry.name)"
            class="legend-note"
          >
            {{ tbScopeNoteFor(entry.name) }}
          </li>
        </ul>
        <!-- The phys overlay's legend is bivariate (area × power) and
             carries its own scope toggle + provenance block, so it
             replaces the flat swatch list rather than sitting beside
             it. Every other overlay keeps the generic list above. -->
        <PhysLegend v-if="entry.known && entry.name === 'phys'" />
      </li>
    </ul>
    <!-- Live overlays: sourced from the hub at runtime rather than
         from ``overlays_present``, so they list separately and carry
         a provenance line saying where the numbers came from. -->
    <ul v-if="hasLiveCoverage" class="live-overlays">
      <li>
        <label class="overlay-row">
          <input
            type="checkbox"
            data-testid="cov-live-toggle"
            :checked="store.covEnabled"
            @change="store.toggleCoverageTint()"
          />
          <span class="overlay-name">coverage</span>
          <span class="tag-live">live</span>
        </label>
        <p class="overlay-sub">{{ covProvenance }}</p>
        <ul class="legend">
          <li v-for="item in covLegend" :key="item.label">
            <span
              class="swatch"
              :class="`swatch-${item.kind || 'fill'}`"
              :style="swatchStyle(item)"
            ></span>
            {{ item.label }}
          </li>
        </ul>
      </li>
    </ul>
  </section>
</template>

<script setup>
// Per-overlay enable/disable + per-overlay legend slot.
//
// The list is driven by ``graph.overlays_present`` so the panel
// stays correct when a future producer ships an overlay this
// viewer doesn't know about — the entry appears tagged
// ``unknown`` and the checkbox is disabled (toggling it would do
// nothing visible).
import { computed } from 'vue'
import { useViewerStore } from '../store.js'
import { getOverlay, overlaySummary } from '../overlays/index.js'
import { coverageLiveOverlay } from '../overlays/coverage_live.js'
import { covGeneratedDate } from '../covData.js'
import { token, themeVersion } from '../theme.js'
import PhysLegend from './PhysLegend.vue'

const store = useViewerStore()
const summary = computed(() =>
  store.graph ? overlaySummary(store.graph) : [],
)

// --- live coverage ---------------------------------------------------
//
// Not part of ``summary``: ``overlays_present`` describes what the
// PRODUCER baked into view.json, and this comes from the hub after the
// fact. It gets its own entry, tagged ``live``, with a line saying
// which run the numbers are from — the same payload can be minutes or
// days old and the panel is the only place that says so.
const hasLiveCoverage = computed(() => store.covData !== null)
const covProvenance = computed(() => {
  const day = covGeneratedDate(store.covData)
  const base = "from the hub's latest run"
  return day ? `${base} · ${day}` : base
})
const covLegend = computed(() => {
  themeVersion.value // the ramp swatches are resolved tokens, not var()
  return coverageLiveOverlay.legend()
})
function legendFor(name) {
  // Legend swatches are concrete colours an overlay resolved from a
  // token, so a theme flip has to re-run this. Reading themeVersion
  // during render is what registers the flip as a render dependency.
  themeVersion.value
  const overlay = getOverlay(name)
  if (!overlay || typeof overlay.legend !== 'function') return []
  return overlay.legend(store.graph) || []
}

// Phase 6d (#99): in TB view, the DUT-side clock/reset domain maps
// produced by rtl-buddy-cdc don't speak the testbench's scope —
// SDC is the design's timing contract, not the simulation harness's
// stimulus. So the DUT subtree keeps its full overlay tint while
// every TB scope above renders unannotated. Surface that as a small
// footnote on each affected overlay's legend rather than leaving a
// silent gap; users see a legend entry that explains why the TB
// blocks aren't coloured. Returns an empty string (falsy) for
// overlays that aren't affected or when the view is DUT-rooted.
function tbScopeNoteFor(name) {
  if (store.renderedViewMode !== 'tb') return ''
  if (name !== 'clock' && name !== 'reset') return ''
  // Phase 6e (#99): when a `tb_clock_map.json` is in play, the
  // renderer has already tinted TB scopes outside the DUT — at
  // least one non-DUT node carries an overlays.<name> entry. In
  // that case the "no map above DUT" footnote is misleading; hide
  // it and let the legend speak for itself.
  if (_overlayReachesTbScope(name)) return ''
  if (name === 'clock') return '(no clock map above DUT)'
  if (name === 'reset') return '(no reset map above DUT)'
  return ''
}

function _overlayReachesTbScope(overlayName) {
  const g = store.graph
  if (!g) return false
  const anchors = store.dutAnchorIds || []
  const isInsideDut = (id) =>
    anchors.some((a) => id === a || id.startsWith(a + '.'))
  for (const node of g.nodes) {
    if (isInsideDut(node.id)) continue
    const ov = node.overlays && node.overlays[overlayName]
    if (!ov) continue
    // The clock + reset overlays both surface their identity via a
    // non-empty field (``clock`` for clocks, ``reset`` for resets).
    // Anything truthy under the key means the overlay reaches this
    // TB-side node.
    if (overlayName === 'clock' && ov.clock) return true
    if (overlayName === 'reset' && ov.reset) return true
  }
  return false
}

// Per-kind swatch styling. ``fill`` is a solid filled box (clock
// palette pastels, default for entries without a ``kind`` field).
// ``stroke`` is an outlined-only box (reset border markers).
// ``dashed-line`` is a horizontal dashed segment (CDC / RDC edge
// styles). The kind is a hint from each overlay's legend() — see
// overlays/clock.js + overlays/reset.js.
function swatchStyle(item) {
  const kind = item.kind || 'fill'
  if (kind === 'stroke') {
    return { borderColor: item.swatch, borderWidth: '2px', background: 'transparent' }
  }
  if (kind === 'dashed-line' || kind === 'solid-line') {
    return { borderColor: item.swatch }
  }
  return { background: item.swatch }
}
</script>

<style scoped>
.overlay-panel { padding: 0.5rem; }
.overlay-panel h3 {
  margin: 0 0 0.5rem;
  font-size: 0.85rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--fg-muted);
}
.overlay-panel ul { list-style: none; padding: 0; margin: 0; }
.overlay-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-family: var(--font-mono);
  font-size: 0.9rem;
  cursor: pointer;
}
.tag-unknown {
  font-size: 0.7rem;
  background: var(--panel-2);
  color: var(--fg-muted);
  padding: 0.05rem 0.4rem;
  border-radius: 999px;
}
/* ``live`` marks an overlay whose data came from the hub at runtime
   rather than from the loaded view.json. Same chip geometry as the
   ``unknown`` tag, accent-coloured because it is a statement about
   freshness, not a warning. */
.tag-live {
  font-size: 0.7rem;
  background: var(--panel-2);
  color: var(--accent);
  padding: 0.05rem 0.4rem;
  border-radius: 999px;
}
/* Provenance line under a live overlay's checkbox. */
.overlay-sub {
  margin: 0.1rem 0 0.15rem 1.5rem;
  font-size: 0.7rem;
  color: var(--fg-faint);
}
/* The live group follows the payload list with a hairline between
   them, so "produced" and "fetched" don't read as one list. The
   sibling selector is what keeps the rule honest: with no payload
   overlays there is no preceding list, and a divider hanging under
   the heading would be separating nothing from something. */
ul + .live-overlays {
  margin-top: 0.4rem;
  padding-top: 0.4rem;
  border-top: 1px solid var(--line);
}
.legend {
  margin-left: 1.5rem;
  font-size: 0.75rem;
  color: var(--fg-muted);
}
.legend li { display: flex; align-items: center; gap: 0.5rem; padding: 0.1rem 0; }
/* Phase 6d (#99): TB-scope explanatory footnote on the clock/reset
   legends. Italic, smaller, indented so it reads as a side comment
   rather than a swatched legend entry. */
.legend-note {
  font-style: italic;
  font-size: 0.7rem;
  color: var(--fg-faint);
  margin-top: 0.15rem;
  padding-left: 1.25rem;
}
.swatch {
  width: 16px;
  height: 12px;
  border-radius: 2px;
  border: 1px solid var(--line-strong);
  display: inline-block;
  box-sizing: border-box;
}
/* Outlined-only swatch — reset border markers. The border colour
   is injected inline; the fill stays transparent so the kind reads
   as "stroke" rather than "fill". */
.swatch-stroke { border-radius: 2px; }
/* Horizontal dashed line — CDC / RDC edge styles. Render via a
   thick top border on a short, otherwise-borderless box so the
   dashes are visible at 16x12. */
.swatch-dashed-line {
  border: none;
  border-top-style: dashed;
  border-top-width: 3px;
  height: 0;
  width: 18px;
  align-self: center;
  margin-top: 4px;
  border-radius: 0;
}
/* Horizontal solid line — non-toggleable structural styles (e.g.
   port → instance signal flow). Same shape as the dashed
   variant; only the stroke style differs. */
.swatch-solid-line {
  border: none;
  border-top-style: solid;
  border-top-width: 3px;
  height: 0;
  width: 18px;
  align-self: center;
  margin-top: 4px;
  border-radius: 0;
}
.layout-legend { margin-left: 0; }
.empty {
  font-size: 0.85rem;
  color: var(--fg-muted);
  margin: 0;
}
/* The "here's the command" line: quieter than the statement above it,
   and the command itself wraps rather than widening the sidebar. */
.empty-hint {
  margin-top: 0.35rem;
  font-size: 0.75rem;
  color: var(--fg-faint);
  line-height: 1.4;
}
.empty-hint code {
  font-family: var(--font-mono);
  background: var(--panel-2);
  padding: 0.05rem 0.3rem;
  border-radius: var(--radius-1);
  word-break: break-word;
}
</style>

<template>
  <section class="edge-detail" v-if="edge">
    <h3>{{ edge.from }} → {{ edge.to }}</h3>
    <div class="nav-actions">
      <button
        type="button"
        @click="store.select(edge.from)"
        title="Select the source instance"
      >Select source</button>
      <button
        type="button"
        @click="store.select(edge.to)"
        title="Select the destination instance"
      >Select destination</button>
      <button
        type="button"
        @click="store.clearSelection()"
        title="Clear edge selection"
      >Clear</button>
    </div>
    <dl>
      <template v-if="hasPortPairs">
        <dt>Port pairs</dt>
        <dd>
          <ul>
            <li v-for="(pair, idx) in edge.port_pairs" :key="idx">
              <code>
                <span class="port-name">.{{ pair[1] }}</span>
                <span class="port-arrow">({{ pair[0] }})</span>
              </code>
            </li>
          </ul>
        </dd>
      </template>
      <template v-if="cdcCrossings.length">
        <dt>CDC crossings</dt>
        <dd>
          <table class="cdc-table">
            <thead>
              <tr>
                <th>src clock</th><th>→ dst clock</th><th class="num">flops</th><th>sync</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(p, idx) in cdcCrossings" :key="idx">
                <td><code>{{ p.src_clock }}</code></td>
                <td><code>{{ p.dst_clock }}</code></td>
                <td class="num">{{ p.flops }}</td>
                <td
                  :class="p.hazard === false ? 'cdc-safe' : 'cdc-hazard'"
                  :data-cdc-verdict="p.hazard === false ? 'synchronized' : 'hazard'"
                >{{ pairVerdict(p) }}</td>
              </tr>
            </tbody>
          </table>
        </dd>
      </template>
      <template v-for="(payload, name) in otherOverlays" :key="name">
        <dt>overlay: {{ name }}</dt>
        <dd><pre>{{ JSON.stringify(payload, null, 2) }}</pre></dd>
      </template>
      <template v-if="!hasPortPairs && !hasOverlays">
        <dt>Edge</dt>
        <dd class="empty">Structural parent → child edge. No port pairs or overlay payloads recorded.</dd>
      </template>
    </dl>
  </section>
</template>

<script setup>
import { computed } from 'vue'
import { useViewerStore } from '../store.js'

const store = useViewerStore()
const edge = computed(() => store.selectedEdgeObj)
const hasPortPairs = computed(
  () => edge.value && Array.isArray(edge.value.port_pairs) && edge.value.port_pairs.length > 0,
)
const hasOverlays = computed(
  () => edge.value && edge.value.overlays && Object.keys(edge.value.overlays).length > 0,
)
// CDC pairs come from the ``clock`` overlay's ``pairs`` array
// (json_render.py:_clock_edge_contribution). Render them in a
// dedicated table instead of the generic ``JSON.stringify`` fallback
// so users can read the src→dst-clock mapping at a glance.
const cdcCrossings = computed(() => {
  const ov = edge.value?.overlays?.clock
  if (!ov || !Array.isArray(ov.pairs)) return []
  return ov.pairs
})
// The pair's verdict (rtl-buddy-sch#211): the recognised schemes for
// a safe pair, ``⚠CDC`` for a hazard — with any schemes its safe
// crossings had alongside. A pair from a payload that predates
// ``hazard`` is a hazard, as it is drawn.
function pairVerdict(p) {
  const schemes = (Array.isArray(p.synchronizers) ? p.synchronizers : [])
    .map((s) => String(s).toUpperCase())
    .join('+')
  if (p.hazard === false) return schemes || '✓'
  return schemes ? `⚠CDC (+${schemes})` : '⚠CDC'
}
// Suppress the ``clock`` overlay from the generic JSON dump when
// it's already rendered as the CDC table above — keeps the panel
// from showing the same data twice.
const otherOverlays = computed(() => {
  const all = edge.value?.overlays || {}
  if (!cdcCrossings.value.length) return all
  const { clock: _consumed, ...rest } = all
  return rest
})
</script>

<style scoped>
/* No border-top / margin-top here: every detail panel is mounted
   inside a CollapsiblePanel, which already draws the separator. Both
   drawing it produced a double rule at every section boundary. */
.edge-detail { padding: 0.5rem; }
.edge-detail h3 {
  font-family: var(--font-mono);
  font-size: 0.85rem;
  margin: 0 0 0.5rem;
  word-break: break-all;
}
.edge-detail dt {
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--fg-muted);
  margin-top: 0.5rem;
}
.edge-detail dd { margin: 0.1rem 0 0; font-size: 0.85rem; }
.edge-detail dd ul { margin: 0; padding-left: 1rem; }
.edge-detail dd pre {
  background: var(--panel-2);
  padding: 0.25rem 0.5rem;
  border-radius: var(--radius-2);
  font-size: 0.75rem;
}
.nav-actions { display: flex; gap: 0.25rem; margin-bottom: 0.5rem; flex-wrap: wrap; }
.nav-actions button {
  border: 1px solid var(--line-strong);
  background: var(--panel);
  color: var(--fg);
  padding: 0.15rem 0.5rem;
  cursor: pointer;
  border-radius: var(--radius-2);
  font-size: 0.75rem;
}
.empty { color: var(--fg-muted); }
.cdc-hazard { color: var(--err); font-family: var(--font-mono); }
.cdc-safe { color: var(--reset-sync); font-family: var(--font-mono); }
.port-name { color: var(--fg); }
.port-arrow { color: var(--fg-muted); font-size: 0.75rem; margin-left: 0.25rem; }
</style>

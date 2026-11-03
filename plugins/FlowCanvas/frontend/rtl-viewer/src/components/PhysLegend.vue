<template>
  <div class="phys-legend" data-testid="phys-legend">
    <!-- Self-vs-subtree, for the POWER channel. Two explicit buttons
         rather than one toggle so each click is idempotent and the
         current scope is readable without hovering. -->
    <div class="phys-scope" role="group" aria-label="phys power scope">
      <span class="scope-label">power</span>
      <button
        v-for="scope in PHYS_SCOPES"
        :key="scope"
        type="button"
        class="scope-btn"
        :class="{ on: store.physScope === scope }"
        :aria-pressed="store.physScope === scope"
        :title="SCOPE_TITLE[scope]"
        @click="store.setPhysScope(scope)"
      >{{ scope }}</button>
    </div>
    <!-- The bivariate key: area saturation across, power ring down.
         Each cell is one box carrying both channels, so a reader
         matches a node to a cell instead of interpolating two ramps
         in their head. -->
    <table class="bivariate">
      <tbody>
        <tr v-for="(row, r) in key.rows" :key="r">
          <th scope="row">{{ key.powerLabels[r] }}</th>
          <td v-for="(cell, c) in row.cells" :key="c">
            <span
              class="cell"
              :style="{ background: cell.fill, borderColor: cell.ring }"
              :title="`${key.areaLabels[c]} area · ${key.powerLabels[r]} power`"
            ></span>
          </td>
        </tr>
        <tr class="axis">
          <th scope="row"></th>
          <td v-for="(label, c) in key.areaLabels" :key="c">{{ label }}</td>
        </tr>
      </tbody>
    </table>
    <p class="axis-note">fill = module area · ring = power</p>
    <p v-if="fillNote" class="fill-note">{{ fillNote }}</p>
    <p v-if="provenance" class="provenance" :title="provenanceTitle">{{ provenance }}</p>
    <p v-for="note in notes" :key="note" class="phys-note">{{ note }}</p>
  </div>
</template>

<script setup>
// Bivariate legend for the phys overlay (rtl-buddy/rtl-buddy-sch#22).
//
// Three things the flat swatch list in OverlayPanel can't carry: the
// 3×3 area×power key, the self-vs-subtree toggle, and the
// ``overlay_meta.phys`` provenance + notes — which run, which halves
// it filled, and every sentence the join had to qualify itself with
// (a wrapper top it could not find, rows that matched no scope, a
// half the producing command never wrote).
import { computed } from 'vue'
import { useViewerStore } from '../store.js'
import { bivariateLegend, PHYS_SCOPES, physFillNote } from '../overlays/phys.js'
import { themeVersion } from '../theme.js'

const store = useViewerStore()

// The toggle acts on POWER only. Area is the producer's per-module
// figure, which already includes the submodules', and the view carries
// no instance multiplicity to subtract them back out with — so there
// is no self area to switch to. Saying so on the hover is cheaper than
// letting a reader conclude the area channel is broken.
const SCOPE_TITLE = {
  subtree:
    'Power of every leaf cell under this node (the default). Area is the module roll-up either way.',
  self: 'Power of only the leaf cells this node directly contains. Area is the module roll-up either way.',
}

// The swatches are resolved token values, not ``var()``, so a theme
// flip has to rebuild them — reading themeVersion registers that.
const key = computed(() => {
  themeVersion.value
  return bivariateLegend()
})

const meta = computed(() => {
  const m = store.graph && store.graph.overlay_meta && store.graph.overlay_meta.phys
  return m && typeof m === 'object' ? m : null
})

// Coverage wins the fill; say so here rather than let the reader
// wonder why the saturation channel went flat.
const fillNote = computed(() =>
  physFillNote({ enabledOverlays: store.enabledOverlays }),
)

const provenance = computed(() => {
  const m = meta.value
  if (!m) return ''
  const halves = m.halves || {}
  const have = []
  if (halves.modules) have.push('area')
  if (halves.instances) have.push('power')
  const what = have.length ? have.join(' + ') : 'nothing measured'
  const root = m.join_root ? ` · rooted at ${m.join_root}` : ''
  return `${what}${root}`
})

const provenanceTitle = computed(() => {
  const m = meta.value
  if (!m) return ''
  return m.manifest || m.source || ''
})

const notes = computed(() => {
  const m = meta.value
  return m && Array.isArray(m.notes) ? m.notes : []
})
</script>

<style scoped>
.phys-legend {
  margin-left: 1.5rem;
  font-size: 0.75rem;
  color: var(--fg-muted);
}
.phys-scope {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  margin: 0.15rem 0 0.35rem;
}
.scope-label {
  color: var(--fg-faint);
  margin-right: 0.15rem;
}
.scope-btn {
  font-family: var(--font-mono);
  font-size: 0.7rem;
  padding: 0.05rem 0.4rem;
  border: 1px solid var(--line-strong);
  border-radius: var(--radius-1);
  background: var(--panel-2);
  color: var(--fg-muted);
  cursor: pointer;
}
.scope-btn.on {
  background: var(--accent);
  border-color: var(--accent);
  color: var(--accent-contrast);
}
.bivariate {
  border-collapse: collapse;
  font-size: 0.65rem;
}
.bivariate th {
  font-weight: normal;
  text-align: right;
  padding-right: 0.3rem;
  color: var(--fg-faint);
}
.bivariate td {
  padding: 1px;
  text-align: center;
  color: var(--fg-faint);
}
.cell {
  display: inline-block;
  width: 16px;
  height: 12px;
  border: 2px solid;
  border-radius: 2px;
  box-sizing: border-box;
}
.axis td {
  padding-top: 0.15rem;
}
.axis-note,
.fill-note,
.provenance,
.phys-note {
  margin: 0.2rem 0 0;
  font-size: 0.7rem;
  color: var(--fg-faint);
}
.fill-note,
.phys-note {
  font-style: italic;
  line-height: 1.35;
}
.provenance {
  font-family: var(--font-mono);
  word-break: break-word;
}
</style>

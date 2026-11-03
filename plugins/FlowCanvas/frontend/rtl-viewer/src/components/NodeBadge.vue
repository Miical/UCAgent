<template>
  <div
    ref="rootEl"
    class="node-badge"
    :class="`popup-open-${placement}`"
    :data-severity="worstSeverity"
    :data-test="`node-badge-${nodeId}`"
    role="button"
    tabindex="0"
    @click="$emit('select')"
    @keydown.enter="$emit('select')"
    @keydown.space.prevent="$emit('select')"
    @mouseenter="updatePlacement"
    @focus="updatePlacement"
  >
    <span class="dot rb-sev" :data-severity="worstSeverity" :title="badgeTitle"></span>
    <span v-if="items.length > 1" class="count">{{ items.length }}</span>
    <div class="popup" role="tooltip">
      <ul>
        <li
          v-for="(d, i) in items"
          :key="i"
          :data-severity="d.severity || 'info'"
        >
          <span class="sev rb-sev" :data-severity="d.severity || 'info'">
            {{ (d.severity || 'info').toUpperCase() }}
          </span>
          <span v-if="d.code" class="code">{{ d.code }}</span>
          <span class="msg">{{ d.message }}</span>
          <span class="src">{{ d.source }} · {{ d.file }}:{{ d.line }}</span>
        </li>
      </ul>
    </div>
  </div>
</template>

<script setup>
// One badge per node that carries any diagnostics_set items mapped
// to it by the store's resolver. Collapsed by default (severity-
// coloured dot, item count if >1); hover or focus expands the popup
// listing each item.
//
// Position is set externally via inline ``style``; the parent
// (GraphCanvas) re-computes coords whenever the canvas transform or
// the diagnostics map changes.

import { computed, ref } from 'vue'
import { worstSeverity as pickWorstSeverity } from '../severity.js'

const props = defineProps({
  nodeId: { type: String, required: true },
  items: { type: Array, required: true },
})
defineEmits(['select'])

// Popup placement. The default is bottom-right (``br``) but we
// flip to ``bl`` / ``tr`` / ``tl`` when the badge is near the
// containing ``.graph-canvas``'s right / bottom edges so the
// expanded card stays inside the viewport instead of getting
// clipped by ``.graph-canvas { overflow: hidden }``.
const rootEl = ref(null)
const placement = ref('br')

// Rough popup envelope — kept in step with the CSS min-width and
// the upper-bound message density. Used as a "is there enough
// room?" threshold rather than an exact size, so a small drift
// here is harmless.
const POPUP_MIN_WIDTH_PX = 18 * 16
const POPUP_TYPICAL_HEIGHT_PX = 10 * 16

function updatePlacement() {
  if (!rootEl.value) return
  const badge = rootEl.value.getBoundingClientRect()
  const canvas = rootEl.value.closest('.graph-canvas')
  if (!canvas) return
  const cv = canvas.getBoundingClientRect()
  // How much room each direction relative to the visible canvas.
  // Right/down measured from the badge's near edge so the popup
  // can flow away from the badge in the chosen direction.
  const roomRight = cv.right - badge.right
  const roomLeft = badge.left - cv.left
  const roomDown = cv.bottom - badge.bottom
  const roomUp = badge.top - cv.top
  // Prefer the default (right/down) unless the popup wouldn't fit;
  // flip only when the other side actually has more room. Keeps the
  // common case stable so the user's eye doesn't have to track a
  // popup that bounces between sides as nodes are clicked.
  const horiz =
    roomRight < POPUP_MIN_WIDTH_PX && roomLeft > roomRight ? 'l' : 'r'
  const vert =
    roomDown < POPUP_TYPICAL_HEIGHT_PX && roomUp > roomDown ? 't' : 'b'
  placement.value = vert + horiz
}

// Severity rank: error > warning > info > hint. Pick the worst the
// items contain so the collapsed dot conveys the highest-priority
// finding at a glance. The rank order lives in ``severity.js`` — it
// used to be a second copy of the table DiagnosticsPanel carried.
const worstSeverity = computed(() => pickWorstSeverity(props.items))

const badgeTitle = computed(() => {
  if (props.items.length === 1) {
    const d = props.items[0]
    return `${(d.severity || 'info').toUpperCase()}${d.code ? ' ' + d.code : ''}: ${d.message || ''}`
  }
  return `${props.items.length} diagnostics — hover for details`
})
</script>

<style scoped>
.node-badge {
  /* Sizing tuned so the dot is unmistakable but doesn't crowd
     adjacent nodes at the default 0.4× zoom-out. */
  position: absolute;
  display: inline-flex;
  align-items: center;
  gap: 0.15rem;
  /* Pivot at the dot's centre so the screen-coord we get from the
     parent is the natural "top-right corner of the node" anchor. */
  transform: translate(-50%, -50%);
  cursor: pointer;
  z-index: 5;
  font-family: var(--font-mono);
}
/* Fill comes from the shared ``.rb-sev`` map in app.css; the ring and
   the drop shadow are what keep the dot readable over any node fill an
   overlay may have applied. */
.dot {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  border: 2px solid var(--panel);
  box-shadow: var(--shadow-1);
}
.count {
  font-size: 0.65rem;
  background: var(--fg);
  color: var(--bg);
  padding: 0 0.3rem;
  border-radius: 999px;
  line-height: 1.3;
  font-weight: 600;
  /* Sit visually adjacent to the dot, no extra offset since the
     flex gap already separates them. */
}
.popup {
  position: absolute;
  /* Coordinates are set per placement variant below; here we just
     pin the box style + transition + hidden-by-default state. */
  min-width: 18rem;
  max-width: 28rem;
  background: var(--panel);
  border: 1px solid var(--line-strong);
  border-radius: var(--radius-3);
  box-shadow: var(--shadow-2);
  padding: 0.4rem 0;
  font-size: 0.75rem;
  color: var(--fg);
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.12s ease-out;
}
/* Four placement variants — chosen by ``updatePlacement`` based on
   how much room each direction has within ``.graph-canvas``.
   Default ``br`` (bottom-right) stays the same as the v1 layout. */
.popup-open-br .popup { top: 1rem;   left: 0.5rem;  right: auto;  bottom: auto; }
.popup-open-bl .popup { top: 1rem;   right: 0.5rem; left: auto;   bottom: auto; }
.popup-open-tr .popup { bottom: 1rem; left: 0.5rem; right: auto;  top: auto;    }
.popup-open-tl .popup { bottom: 1rem; right: 0.5rem; left: auto;  top: auto;    }
.node-badge:hover .popup,
.node-badge:focus-within .popup,
.node-badge:focus .popup {
  opacity: 1;
  pointer-events: auto;
}
.popup ul {
  list-style: none;
  margin: 0;
  padding: 0;
}
.popup li {
  display: grid;
  grid-template-columns: max-content max-content 1fr;
  gap: 0.4rem;
  align-items: baseline;
  padding: 0.3rem 0.6rem;
  border-bottom: 1px solid var(--line);
}
.popup li:last-child { border-bottom: none; }
.popup .sev {
  font-size: 0.6rem;
  padding: 0.05rem 0.3rem;
  border-radius: var(--radius-1);
  color: var(--accent-contrast);
  letter-spacing: 0.02em;
}
.popup .code {
  font-size: 0.7rem;
  color: var(--fg);
  font-weight: 600;
}
.popup .msg {
  font-family: inherit;
  font-size: 0.78rem;
  line-height: 1.35;
  /* Wrap long messages so they don't make the popup grow horizontally. */
  white-space: normal;
  word-break: break-word;
}
.popup .src {
  grid-column: 1 / -1;
  margin-top: 0.1rem;
  font-size: 0.65rem;
  color: var(--fg-muted);
}
</style>

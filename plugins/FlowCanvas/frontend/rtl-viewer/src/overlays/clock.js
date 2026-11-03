// 'clock' overlay renderer.
//
// Mirror of the Python-side ClockOverlay (`src/rtl_buddy_view/
// overlays/clock.py`): consumes the per-node ``overlays.clock``
// block emitted by view.json v1's renderer, and the per-edge
// ``overlays.clock.crossing`` flag.
//
// The visual contribution is: fill the node's <g> with a
// clock-keyed pastel; mark edges where ``overlays.clock.crossing``
// is true with a CDC accent. The accent follows the crossing's
// verdict (rtl-buddy-sch#211): ``hazard: false`` — every crossing on
// the edge recognised-safe — paints a solid synchroniser teal, the
// same teal the reset overlay gives synchronisers; anything else
// (including a payload from before ``hazard`` existed) keeps the
// dashed ⚠CDC red.
//
// The palette and the clock→colour assignment live in ``palette.js``
// — shared with the in-JS DOT builder, which bakes the same colours
// into bridge HTML cells. Two copies over two different clock sets
// was how the legend swatch and the node fill came to disagree.

import { buildClockPalette } from '../palette.js'
import { token } from '../theme.js'

/**
 * Is this ``overlays.clock`` edge block a CDC hazard?
 *
 * Only an explicit ``hazard: false`` says no: a view.json written
 * before the field existed carries ``crossing: true`` alone, and every
 * crossing used to be a hazard.
 */
export function isCdcHazard(ov) {
  return !!(ov && ov.crossing && ov.hazard !== false)
}

/** A crossing edge whose every crossing is recognised-safe. */
export function isCdcSynchronized(ov) {
  return !!(ov && ov.crossing && ov.hazard === false)
}

// Same hashing scheme as the dot renderer's ``_palette_color`` so
// the same clock lands in the same colour across the desktop +
// browser views (handy when reviewing a screenshot against a
// terminal capture).
async function djb2(str) {
  let hash = 5381
  for (let i = 0; i < str.length; i++) hash = ((hash << 5) + hash + str.charCodeAt(i)) >>> 0
  return hash
}

export const clockOverlay = {
  name: 'clock',
  /**
   * Apply the overlay's per-node + per-edge contributions to the
   * SVG that ``GraphCanvas`` has rendered. Idempotent: applying
   * twice produces the same DOM state. Tools that toggle the
   * overlay just call this with ``enabled = false`` to clear.
   */
  apply(svgRoot, graph, enabled) {
    const palette = buildClockPalette(graph)
    for (const node of graph.nodes) {
      const ov = node.overlays && node.overlays.clock
      const group = svgRoot.querySelector(
        `[data-node-id="${cssEscape(node.id)}"]`,
      )
      if (!group) continue
      // Skip ALL clusters: painting their background rect fills the
      // whole subtree's scope frame, drowning out the per-leaf
      // colours nested inside. The cluster's role is the labelled
      // frame; clock colour belongs on the leaf boxes (and on bridge
      // HTML-table cells, whose colour is baked into the DOT
      // directly).
      if (group.classList && group.classList.contains('cluster')) {
        const shape0 = group.querySelector('polygon, ellipse, rect, path')
        if (shape0) shape0.style.fill = ''
        group.removeAttribute('data-overlay-clock')
        continue
      }
      const shape = group.querySelector('polygon, ellipse, rect, path')
      if (!shape) continue
      if (enabled && ov && ov.clock && palette.has(ov.clock)) {
        // Only override the inline style — leave the polygon's
        // ``fill=`` attribute alone. Graphviz sets the attribute
        // from the DOT's global ``node [fillcolor=...]`` default
        // (neutral grey), and on toggle-off we restore that floor
        // by clearing inline only. Stripping the attribute itself
        // bottoms out at SVG's default of *black*, which made
        // unchecking the overlay turn modules black (#57 follow-up).
        shape.style.fill = palette.get(ov.clock)
        group.setAttribute('data-overlay-clock', ov.clock)
      } else {
        shape.style.fill = ''
        group.removeAttribute('data-overlay-clock')
      }
    }
    for (const edge of graph.edges) {
      const ov = edge.overlays && edge.overlays.clock
      // Every rendered group for this edge — a multi-pair CDC edge is one
      // DOT edge per clock pair, each routed to its own grid cell
      // (rtl-buddy-sch#197). The group's FIRST path is the visible one;
      // the hover hit twin is inserted after it.
      const groups = svgRoot.querySelectorAll(
        `[data-edge-from="${cssEscape(edge.from)}"][data-edge-to="${cssEscape(edge.to)}"]`,
      )
      for (const group of groups) {
        const path = group.querySelector('path')
        if (!path) continue
        if (enabled && isCdcHazard(ov)) {
          // Inline style, not the ``stroke`` attribute: the canvas
          // gives every edge a themed default stroke via CSS, and a
          // presentation attribute loses to a stylesheet rule. The
          // ``data-overlay-clock`` marker is what the reset overlay
          // reads for precedence — comparing colour strings broke the
          // moment the colour became a token.
          path.style.stroke = token('--err')
          path.setAttribute('stroke-dasharray', '4,3')
          path.setAttribute('data-overlay-clock', 'crossing')
        } else if (enabled && isCdcSynchronized(ov)) {
          // Recognised-safe: solid teal, no dash. Marked
          // ``synchronized`` rather than ``crossing`` so an RDC hazard
          // on the same edge still claims it (hazard beats safe).
          path.style.stroke = token('--reset-sync')
          path.removeAttribute('stroke-dasharray')
          path.setAttribute('data-overlay-clock', 'synchronized')
        } else {
          path.style.stroke = ''
          path.removeAttribute('stroke-dasharray')
          path.removeAttribute('data-overlay-clock')
        }
      }
    }
  },
  /** Per-overlay legend payload for OverlayPanel.vue. */
  legend(graph) {
    const palette = buildClockPalette(graph)
    const entries = Array.from(palette.entries()).map(([label, swatch]) => ({
      label,
      swatch,
      kind: 'fill',
    }))
    // Conditionally surface the CDC edge styles — each only when at
    // least one edge in the current graph is drawn that way. Filters
    // automatically when a new view.json without crossings is loaded.
    const edges = graph?.edges || []
    if (edges.some((e) => isCdcHazard(e.overlays && e.overlays.clock))) {
      entries.push({
        label: 'CDC crossing',
        swatch: token('--err'),
        kind: 'dashed-line',
      })
    }
    if (edges.some((e) => isCdcSynchronized(e.overlays && e.overlays.clock))) {
      entries.push({
        label: 'synchronized CDC',
        swatch: token('--reset-sync'),
        kind: 'solid-line',
      })
    }
    return entries
  },
}

// CSS.escape is widely available but not on every JSDOM build;
// fall back to a conservative escaper for selector use.
function cssEscape(s) {
  if (typeof CSS !== 'undefined' && CSS.escape) return CSS.escape(s)
  return String(s).replace(/[^a-zA-Z0-9_-]/g, (c) => `\\${c}`)
}

// djb2 is unused today but pinned so future palette assignments
// can switch from "first-seen" to "hash-stable" without rewriting
// the surface — see note above.
export { djb2 }

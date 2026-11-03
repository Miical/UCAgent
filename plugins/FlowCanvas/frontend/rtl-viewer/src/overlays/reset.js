// 'reset' overlay renderer.
//
// Mirror of the Python-side ResetOverlay (`src/rtl_buddy_view/
// overlays/reset.py`): consumes the per-node ``overlays.reset``
// block and the per-edge ``overlays.reset.crossing`` flag from
// view.json v1.
//
// Visual contributions:
//   - Synchroniser-set members get a teal border (``--reset-sync``).
//   - RDC-crossing edges get a dashed-orange stroke (``--reset-bind``).
//   - Other flops with a reset binding pick up a faint orange
//     border so the reset overlay reads as a distinct family
//     from the clock overlay's fill-based palette.

import { token } from '../theme.js'

export const resetOverlay = {
  name: 'reset',
  apply(svgRoot, graph, enabled) {
    for (const node of graph.nodes) {
      const ov = node.overlays && node.overlays.reset
      const group = svgRoot.querySelector(
        `[data-node-id="${cssEscape(node.id)}"]`,
      )
      if (!group) continue
      const shape = group.querySelector('polygon, ellipse, rect, path')
      if (!shape) continue
      if (enabled && ov) {
        shape.style.stroke = ov.is_synchronizer
          ? token('--reset-sync')
          : token('--reset-bind')
        shape.style.strokeWidth = '2'
        group.setAttribute('data-overlay-reset', ov.reset || (ov.is_synchronizer ? 'sync' : ''))
      } else {
        shape.style.stroke = ''
        shape.style.strokeWidth = ''
        group.removeAttribute('data-overlay-reset')
      }
    }
    for (const edge of graph.edges) {
      const ov = edge.overlays && edge.overlays.reset
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
        if (enabled && ov && ov.crossing) {
          // Only claim the edge when the clock overlay hasn't marked a
          // CDC hazard on it (CDC red wins on dual-issue edges — same
          // precedence as the dot renderer). A recognised-safe CDC
          // edge (``synchronized``, rtl-buddy-sch#211) is not a hazard,
          // so the RDC warning paints over its teal. The marker
          // attribute is the test, not the stroke colour: the colours
          // are tokens now, so a string compare would break on a theme
          // flip.
          if (path.getAttribute('data-overlay-clock') !== 'crossing') {
            path.style.stroke = token('--reset-bind')
            path.setAttribute('stroke-dasharray', '6,3')
            path.setAttribute('data-overlay-reset', 'crossing')
          }
        } else if (path.getAttribute('data-overlay-reset') === 'crossing') {
          path.style.stroke = ''
          path.removeAttribute('stroke-dasharray')
          path.removeAttribute('data-overlay-reset')
        }
      }
    }
  },
  legend(graph) {
    const entries = []
    let hasBinding = false
    let hasSync = false
    for (const node of graph?.nodes || []) {
      const ov = node.overlays && node.overlays.reset
      if (!ov) continue
      if (ov.is_synchronizer) hasSync = true
      else hasBinding = true
    }
    if (hasBinding) {
      entries.push({
        label: 'reset-binding',
        swatch: token('--reset-bind'),
        kind: 'stroke',
      })
    }
    if (hasSync) {
      entries.push({
        label: 'reset-synchroniser',
        swatch: token('--reset-sync'),
        kind: 'stroke',
      })
    }
    const hasRdcCrossing = (graph?.edges || []).some(
      (e) => e.overlays && e.overlays.reset && e.overlays.reset.crossing,
    )
    if (hasRdcCrossing) {
      entries.push({
        label: 'RDC crossing',
        swatch: token('--reset-bind'),
        kind: 'dashed-line',
      })
    }
    return entries
  },
}

function cssEscape(s) {
  if (typeof CSS !== 'undefined' && CSS.escape) return CSS.escape(s)
  return String(s).replace(/[^a-zA-Z0-9_-]/g, (c) => `\\${c}`)
}

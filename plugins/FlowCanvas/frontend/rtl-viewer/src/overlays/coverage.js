// 'coverage' overlay renderer (Phase 6 — rtl-buddy-view#20).
//
// Mirror of the Python-side CoverageOverlay: consumes the per-node
// ``overlays.coverage`` block emitted by view.json's renderer
// (per-channel ``{covered, total, pct}`` rollups + a Coverview deep
// link) and tints each node by its covered percentage — red 0% →
// green 100%, gray for "no coverage data".
//
// Which channel drives the tint comes from
// ``overlay_meta.coverage.metric`` (the producer's --coverage-metric
// flag); default 'lines'. The per-channel progress bars live in
// NodeDetail.vue, which imports `heatColor` so the bar colours match
// the canvas tint.
//
// The ramp itself lives in ``palette.js`` — the shared hub token sheet
// pins its lightness (``--cov-l``: 82% light, 38% dark) so the
// schematic overlay, the graph pane and the coverage app agree.

import { coverageColor, coverageNoDataColor } from '../palette.js'

/** Continuous coverage tint. Re-exported: NodeDetail.vue imports it. */
export const heatColor = coverageColor

export function tintMetric(graph) {
  const meta = graph && graph.overlay_meta && graph.overlay_meta.coverage
  return (meta && meta.metric) || 'lines'
}

export const coverageOverlay = {
  name: 'coverage',

  /**
   * Tint every node box by its coverage percentage. Same DOM
   * conventions as the clock overlay: inline style only (so
   * toggle-off restores Graphviz's attribute fill), clusters are
   * skipped (the tint belongs on leaf boxes, not subtree frames),
   * and applying twice is idempotent.
   *
   * Nodes with no coverage block get the explicit "no data" gray
   * while the overlay is enabled — visually distinct from both the
   * heat ramp and the untinted default, per the issue's
   * graceful-degradation requirement.
   */
  apply(svgRoot, graph, enabled) {
    const metric = tintMetric(graph)
    const hasCoverage =
      Array.isArray(graph.overlays_present) &&
      graph.overlays_present.includes('coverage')
    for (const node of graph.nodes) {
      const group = svgRoot.querySelector(
        `[data-node-id="${cssEscape(node.id)}"]`,
      )
      if (!group) continue
      if (group.classList && group.classList.contains('cluster')) {
        const shape0 = group.querySelector('polygon, ellipse, rect, path')
        if (shape0) shape0.style.fill = ''
        group.removeAttribute('data-overlay-coverage')
        continue
      }
      const shape = group.querySelector('polygon, ellipse, rect, path')
      if (!shape) continue
      const cov = node.overlays && node.overlays.coverage
      const channel = cov && cov[metric]
      if (enabled && channel && typeof channel.pct === 'number') {
        shape.style.fill = heatColor(channel.pct)
        group.setAttribute('data-overlay-coverage', String(channel.pct))
      } else if (enabled && hasCoverage) {
        shape.style.fill = coverageNoDataColor()
        group.setAttribute('data-overlay-coverage', 'no-data')
      } else {
        shape.style.fill = ''
        group.removeAttribute('data-overlay-coverage')
      }
    }
  },

  /** Heat-ramp anchor swatches + the no-data gray for OverlayPanel. */
  legend(graph) {
    const metric = tintMetric(graph)
    return [
      { label: `0% ${metric}`, swatch: heatColor(0), kind: 'fill' },
      { label: `50% ${metric}`, swatch: heatColor(50), kind: 'fill' },
      { label: `100% ${metric}`, swatch: heatColor(100), kind: 'fill' },
      { label: 'no coverage data', swatch: coverageNoDataColor(), kind: 'fill' },
    ]
  },
}

function cssEscape(s) {
  if (typeof CSS !== 'undefined' && CSS.escape) return CSS.escape(s)
  return String(s).replace(/[^a-zA-Z0-9_-]/g, (c) => `\\${c}`)
}

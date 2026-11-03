// 'coverage-live' overlay renderer — the hub's latest coverage run,
// tinted onto the schematic.
//
// Sibling of overlays/coverage.js, not a replacement for it: that one
// paints the ``overlays.coverage`` block a producer baked into
// view.json, this one paints ``/cov.json`` fetched from the hub at
// load time and joined by module name (see covData.js). They share
// the ramp; they never share a data source. A bundle can carry
// neither, either or both.
//
// The name is never in ``graph.overlays_present`` — the producer
// doesn't know what the hub will have — so the registry entry exists
// for lookup + legend only, and ``GraphCanvas`` applies it explicitly
// after ``applyOverlays``. See ``apply`` for why that order matters.

import { covColor, COV_TINT_METRIC, ratioPct } from '../covData.js'

/** Group attribute recording that WE own this node's fill. */
const MARK = 'data-overlay-coverage-live'

// Overlays that also write a node's fill, and mark the group when
// they do. When one of them owns a fill we must not clear it on our
// way out — see the clear branch in ``apply``.
const FILL_OWNER_MARKS = ['data-overlay-clock', 'data-overlay-coverage']

export const coverageLiveOverlay = {
  name: 'coverage-live',

  /**
   * Tint leaf boxes by their module's LINE coverage.
   *
   * ``context.covByModule`` is the ``Map<module, buckets>`` from
   * ``covData.moduleCoverage`` (the store's ``covByModule`` getter).
   * Without it the overlay has nothing to say and behaves exactly as
   * if it were disabled.
   *
   * What gets tinted, and what deliberately does not:
   *
   *   - CLUSTERS are skipped. Painting a cluster's backdrop floods
   *     the whole subtree frame and drowns the leaves nested inside
   *     it; the same reason the clock overlay skips them. Tinting the
   *     cluster LABEL instead (so a scope carries its subtree's
   *     rolled-up score) is the obvious follow-up and is left for a
   *     later round.
   *   - CDC nodes are skipped, and the test is structural. Graphviz
   *     emits a plain rounded box as exactly ONE <path>; a
   *     ``style="rounded,striped"`` two-tone CDC box as THREE
   *     <polygon>s; an HTML-TABLE CDC grid as a backdrop <path> plus
   *     two <polygon>s per cell. So "the group has exactly one shape"
   *     is precisely "this is a plain box", and coverage yields to
   *     CDC wherever CDC has already said something with colour.
   *
   * Against the other fill-writing overlays the live tint WINS: it is
   * applied after them and overwrites the inline fill. Both default
   * on, so a design with clock colours and coverage data shows
   * coverage until the user unchecks it — the tie had to break
   * somewhere, and coverage is the thing this pane could not show
   * before.
   *
   * ORDERING CONTRACT: the caller runs ``applyOverlays`` first and
   * this second, every time. The clear branch relies on it — it
   * restores the Graphviz attribute floor only for nodes no other
   * overlay claims, and trusts the pass that just ran to have
   * re-established everything else.
   *
   * Idempotent, like every other overlay: applying twice lands on the
   * same DOM.
   */
  apply(svgRoot, graph, enabled, context = {}) {
    if (!svgRoot || !graph || !Array.isArray(graph.nodes)) return
    const byModule = context && context.covByModule instanceof Map ? context.covByModule : null
    for (const node of graph.nodes) {
      const group = svgRoot.querySelector(`[data-node-id="${cssEscape(node.id)}"]`)
      if (!group) continue
      const shapes = group.querySelectorAll('polygon, ellipse, rect, path')
      const isCluster = Boolean(group.classList && group.classList.contains('cluster'))
      const plainBox = !isCluster && shapes.length === 1
      const buckets =
        enabled && byModule && plainBox && typeof node.module === 'string'
          ? byModule.get(node.module) || null
          : null

      if (buckets) {
        const pct = ratioPct(buckets[COV_TINT_METRIC] && buckets[COV_TINT_METRIC].ratio)
        // Inline style only, never the ``fill=`` attribute: clearing
        // an attribute bottoms out at SVG's default black, whereas
        // clearing the inline style falls back to the neutral fill
        // Graphviz baked in (rtl-buddy-view#57).
        shapes[0].style.fill = covColor(pct)
        group.setAttribute(MARK, pct === null ? 'no-data' : String(Math.round(pct)))
      } else if (group.hasAttribute(MARK)) {
        group.removeAttribute(MARK)
        const claimed = FILL_OWNER_MARKS.some((a) => group.hasAttribute(a))
        if (!claimed && shapes.length > 0) shapes[0].style.fill = ''
      }
    }
  },

  /** Ramp anchors + the no-data grey, for OverlayPanel's legend. */
  legend() {
    return [
      { label: '0% lines', swatch: covColor(0), kind: 'fill' },
      { label: '50% lines', swatch: covColor(50), kind: 'fill' },
      { label: '100% lines', swatch: covColor(100), kind: 'fill' },
      { label: 'no coverage data', swatch: covColor(null), kind: 'fill' },
    ]
  },
}

// CSS.escape is widely available but not on every JSDOM build;
// fall back to a conservative escaper for selector use.
function cssEscape(s) {
  if (typeof CSS !== 'undefined' && CSS.escape) return CSS.escape(s)
  return String(s).replace(/[^a-zA-Z0-9_-]/g, (c) => `\\${c}`)
}

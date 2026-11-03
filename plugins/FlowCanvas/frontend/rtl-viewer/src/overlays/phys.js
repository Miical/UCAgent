// 'phys' overlay renderer (Phase 7b — rtl-buddy/rtl-buddy-sch#22).
//
// Mirror of the Python-side PhysOverlay: consumes the per-node
// ``overlays.phys`` block the JSON renderer emits — area and cell
// count joined from the physical model's RTL module rows, power
// rolled up from its leaf instance rows — plus the
// ``overlay_meta.phys`` envelope that says where the numbers came
// from and which halves the producing run filled.
//
// Two channels, deliberately on two different visual axes so they
// can be read at once:
//
//   * FILL saturation by area, relative to the largest area in the
//     rendered subtree. Bigger block, stronger colour.
//   * An outer RING by total power, on the same sequential ramp. Cool
//     (pale) to hot.
//
// The self-vs-subtree toggle is a POWER distinction only. A self area
// would have to be derived by subtracting the children's from the
// module's roll-up, and the view carries no instance multiplicity —
// ``leafm u_arr [3:0]`` is one node where Yosys elaborated four — so
// the subtraction over-reports with no way to detect it. The area
// channel therefore shows the module's roll-up in both scopes; see
// docs/phys-overlay.md §6.
//
// The ramp is the vendored hub tokens (``--heat-h/s/l0/l1``, the ones
// the hub's ``/phy`` pane computes in CSS ``calc``), read through
// ``palette.js`` — there is no page-local ramp here, per
// docs/design-tokens.md.
//
// Composition, which is the whole reason for two axes:
//
//   * CLOCK (hue) — when the clock overlay is enabled too, the fill
//     keeps the clock's hue and takes only its SATURATION from area
//     (``saturateBy``). That is the issue's bivariate design: clock on
//     hue, area on saturation, power on the ring.
//   * COVERAGE (fill) — coverage owns the fill outright and phys does
//     not fight it: with both enabled, phys contributes the ring and
//     the legend only, and ``physFillNote`` is the sentence the
//     overlay panel prints so the reader knows which channel went
//     away.
//   * RESET (border) — the reset overlay strokes the node shape
//     itself. The phys ring is a separate element drawn over it, so
//     both survive: reset's 2px border sits inside the phys ring.
//
// CLUSTERS get the ring and never the fill. A cluster is a subtree
// frame, so a power roll-up belongs on it — but filling its
// background would drown out every leaf colour nested inside, which
// is why the clock and coverage overlays skip clusters entirely.

import { buildClockPalette, heatNoneColor, heatRampColor, saturateBy } from '../palette.js'

/** The two scopes the self-vs-subtree toggle switches between (power). */
export const PHYS_SCOPES = ['subtree', 'self']

/** Default scope: a node stands for its subtree unless asked otherwise. */
export const DEFAULT_PHYS_SCOPE = 'subtree'

/** Ring width in px. Thicker than reset's 2px border it draws over. */
const RING_WIDTH = 4

const RING_MARKER = 'data-phys-ring'
const SHAPE_SELECTOR = 'polygon, ellipse, rect, path'

/** ``'self' | 'subtree'`` from the overlay context, defaulted + validated. */
export function physScope(context) {
  const raw = context && context.physScope
  return PHYS_SCOPES.includes(raw) ? raw : DEFAULT_PHYS_SCOPE
}

/** This node's ``overlays.phys`` block, or ``null``. */
export function physBlock(node) {
  const block = node && node.overlays && node.overlays.phys
  return block && typeof block === 'object' ? block : null
}

/**
 * The area figure. **Scope-independent**, and the argument is accepted
 * only so the two channel readers have one shape.
 *
 * ``area_um2`` is the producer's module area, which already includes
 * the submodules'. There is no self counterpart to switch to: see the
 * note at the top of this module.
 */
export function areaOf(block, _scope) {
  if (!block) return null
  const value = block.area_um2
  return typeof value === 'number' ? value : null
}

/**
 * The total-power figure for a scope — the channel the toggle acts on.
 * The model records leaf values only, so ``total_uw`` is the rows
 * attributed to this node itself and ``subtree_total_uw`` is the
 * roll-up. Both are counted rather than derived, which is why this
 * distinction is sound where the area one is not.
 */
export function powerOf(block, scope) {
  if (!block) return null
  const key = scope === 'self' ? 'total_uw' : 'subtree_total_uw'
  const value = block[key]
  return typeof value === 'number' ? value : null
}

/**
 * ``{area, power}`` maxima over the graph's nodes, for one scope.
 *
 * The fill is "relative to subtree max" per the issue, and the graph
 * the SPA holds IS the rendered subtree (descending re-renders it),
 * so the maximum over ``graph.nodes`` is that max. Zero when nothing
 * is measured — callers treat a zero max as "no fraction to compute".
 */
export function physMax(graph, scope) {
  let area = 0
  let power = 0
  for (const node of (graph && graph.nodes) || []) {
    const block = physBlock(node)
    const a = areaOf(block, scope)
    const p = powerOf(block, scope)
    if (typeof a === 'number' && a > area) area = a
    if (typeof p === 'number' && p > power) power = p
  }
  return { area, power }
}

/** ``value / max`` clamped to 0..1; 0 when there is no usable max. */
export function fractionOf(value, max) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null
  if (typeof max !== 'number' || !(max > 0)) return null
  return Math.max(0, Math.min(1, value / max))
}

/** True when another enabled overlay owns the node fill. */
export function coverageOwnsFill(context) {
  const names = context && context.enabledOverlays
  return names instanceof Set && names.has('coverage')
}

/**
 * The sentence the overlay panel prints when phys gave up the fill.
 *
 * Empty string (falsy) when it did not. Written out rather than
 * signalled as a flag because the panel is where a reader finds out
 * that a channel they asked for is not on screen — the same reason
 * the clock legend's TB footnote is a sentence.
 */
export function physFillNote(context) {
  if (!coverageOwnsFill(context)) return ''
  return '(coverage owns the fill; phys shows the power ring only)'
}

/**
 * Fill colour for an area fraction, composed with the clock hue.
 *
 * With no area to show, a node that the CLOCK overlay has an opinion
 * about keeps the clock's colour untouched. Greying it would be this
 * overlay overwriting a channel it has nothing to say about — and on
 * a power-only model (``rb power`` without a synthesis in the same
 * artefact directory) that is *every* node, so the whole diagram
 * would go flat grey the moment both overlays were ticked.
 */
export function areaFill(fraction, clockColor) {
  if (fraction === null) return clockColor || heatNoneColor()
  if (clockColor) {
    const composed = saturateBy(clockColor, fraction)
    if (composed) return composed
  }
  return heatRampColor(fraction)
}

/** Ring colour for a power fraction. */
export function powerRing(fraction) {
  return fraction === null ? heatNoneColor() : heatRampColor(fraction)
}

export const physOverlay = {
  name: 'phys',

  /**
   * Paint area into the fill and power into an outer ring.
   *
   * Idempotent, and the toggle-off path restores exactly what it
   * found: the inline fill is cleared (so Graphviz's own ``fill=``
   * attribute shows through again) and the ring element is removed
   * rather than recoloured. ``context.physScope`` switches between
   * self and subtree figures — a re-style, never a re-layout, which
   * is why the toggle lives here and not in the layout pass.
   */
  apply(svgRoot, graph, enabled, context = {}) {
    if (!svgRoot || !graph) return
    const scope = physScope(context)
    const max = physMax(graph, scope)
    const cedeFill = coverageOwnsFill(context)
    const names = context.enabledOverlays
    const clockPalette =
      names instanceof Set && names.has('clock') ? buildClockPalette(graph) : null
    const measured = Array.isArray(graph.overlays_present)
      ? graph.overlays_present.includes('phys')
      : false

    for (const node of graph.nodes || []) {
      const group = svgRoot.querySelector(`[data-node-id="${cssEscape(node.id)}"]`)
      if (!group) continue
      const isCluster = !!(group.classList && group.classList.contains('cluster'))
      const shape = group.querySelector(SHAPE_SELECTOR)
      if (!shape) continue
      const block = physBlock(node)

      if (!enabled) {
        if (!cedeFill) shape.style.fill = ''
        _dropRing(group)
        _drop(group, 'data-overlay-phys-area')
        _drop(group, 'data-overlay-phys-power')
        continue
      }

      const areaFraction = fractionOf(areaOf(block, scope), max.area)
      const powerFraction = fractionOf(powerOf(block, scope), max.power)

      if (cedeFill || isCluster) {
        // Nothing to clear: coverage painted this fill, or it is a
        // cluster frame nobody fills.
      } else if (block || measured) {
        const clockName = node.overlays && node.overlays.clock && node.overlays.clock.clock
        const clockColor =
          clockPalette && clockName && clockPalette.has(clockName)
            ? clockPalette.get(clockName)
            : null
        shape.style.fill = areaFill(areaFraction, clockColor)
      } else {
        shape.style.fill = ''
      }

      if (powerFraction === null && !measured) {
        _dropRing(group)
      } else {
        _ring(group, shape, powerRing(powerFraction))
      }

      _set(group, 'data-overlay-phys-area', areaFraction)
      _set(group, 'data-overlay-phys-power', powerFraction)
    }
  },

  /**
   * Flat legend for the overlay panel: the two channels' anchors plus
   * the not-measured grey. The bivariate key the issue asks for is
   * ``bivariateLegend`` below, rendered by ``PhysLegend.vue``; this
   * one keeps the panel's generic swatch list correct for a reader
   * who never opens the key.
   */
  legend(graph, context = {}) {
    // The scope suffix rides on the POWER entries only — the area
    // channel does not change with the toggle, and labelling it
    // "(self)" would promise a figure that does not exist.
    const suffix = physScope(context) === 'self' ? ' (self)' : ' (subtree)'
    return [
      { label: 'small area', swatch: heatRampColor(0), kind: 'fill' },
      { label: 'large area', swatch: heatRampColor(1), kind: 'fill' },
      { label: `low power${suffix}`, swatch: heatRampColor(0), kind: 'stroke' },
      { label: `high power${suffix}`, swatch: heatRampColor(1), kind: 'stroke' },
      { label: 'not measured', swatch: heatNoneColor(), kind: 'fill' },
    ]
  },
}

/** Bivariate legend cells: area saturation across × power ring down. */
export const BIVARIATE_STEPS = [0, 0.5, 1]

/**
 * The 3×3 key ``PhysLegend.vue`` renders — every combination of the
 * two channels at three anchors each, so a reader can name a node's
 * cell rather than interpolating two ramps in their head.
 *
 * Returns rows of cells, hottest power first (the way a ranking
 * reads), with the anchor labels alongside.
 */
export function bivariateLegend() {
  const rows = []
  for (const power of [...BIVARIATE_STEPS].reverse()) {
    rows.push({
      power,
      cells: BIVARIATE_STEPS.map((area) => ({
        area,
        power,
        fill: heatRampColor(area),
        ring: heatRampColor(power),
      })),
    })
  }
  return {
    rows,
    areaLabels: ['small', 'mid', 'large'],
    powerLabels: ['hot', 'mid', 'cool'],
  }
}

function _ring(group, shape, color) {
  let ring = null
  if (typeof group.querySelector === 'function') {
    ring = group.querySelector(`[${RING_MARKER}]`)
  }
  if (!ring) {
    if (typeof shape.cloneNode !== 'function' || typeof group.appendChild !== 'function') {
      // No DOM to clone into (a degraded host): stroke the shape
      // itself. The reset overlay may overwrite it, which is the
      // documented cost of the fallback — never an exception.
      shape.style.stroke = color
      shape.style.strokeWidth = String(RING_WIDTH)
      return
    }
    ring = shape.cloneNode(false)
    ring.setAttribute(RING_MARKER, '1')
    ring.removeAttribute('fill')
    ring.style.fill = 'none'
    ring.style.pointerEvents = 'none'
    group.appendChild(ring)
  }
  ring.style.stroke = color
  ring.style.strokeWidth = String(RING_WIDTH)
}

function _dropRing(group) {
  if (typeof group.querySelector !== 'function') return
  const ring = group.querySelector(`[${RING_MARKER}]`)
  if (ring && typeof group.removeChild === 'function') group.removeChild(ring)
}

function _set(group, name, fraction) {
  if (typeof group.setAttribute !== 'function') return
  if (fraction === null) {
    _drop(group, name)
    return
  }
  group.setAttribute(name, String(Math.round(fraction * 100)))
}

function _drop(group, name) {
  if (typeof group.removeAttribute === 'function') group.removeAttribute(name)
}

function cssEscape(s) {
  if (typeof CSS !== 'undefined' && CSS.escape) return CSS.escape(s)
  return String(s).replace(/[^a-zA-Z0-9_-]/g, (c) => `\\${c}`)
}

// The SPA's colour decisions, each written exactly once.
//
// Before this module the same three mappings lived in several places
// and had already drifted:
//
//   - the AXI backpressure ramp was defined in ``overlays/axi_perf.js``,
//     ``NodeDetail.vue`` and ``AxiPerfView.vue`` with three sets of class
//     names and two different ambers (#f59e0b vs #d97706);
//   - the clock palette existed in ``overlays/clock.js`` and
//     ``layout/viz.js``, over *different* clock sets — so the legend
//     swatch and the DOT-baked HTML cell could disagree about a clock's
//     colour;
//   - the coverage ramp's lightness was a literal in ``overlays/coverage.js``.
//
// Every colour here is read from a design token at call time via
// ``theme.js::token``, so a theme flip re-colours the canvas as soon as
// the caller re-draws.

import { token } from './theme.js'

// ---------------------------------------------------------------------
// Clock-domain palette
// ---------------------------------------------------------------------

/** Token names of the seven clock pastels, in assignment order. */
export const CLOCK_PALETTE_TOKENS = [
  '--clk-1',
  '--clk-2',
  '--clk-3',
  '--clk-4',
  '--clk-5',
  '--clk-6',
  '--clk-7',
]

/** Resolve the clock palette to concrete colours for the current theme. */
export function clockPalette() {
  return CLOCK_PALETTE_TOKENS.map((name) => token(name))
}

/**
 * ``<unconstrained>``-style pseudo clocks: signals the SDC binds to no
 * clock (often resets). They anchor no direction, so they never take a
 * palette slot. Same rule as ``dot.py::_crossing_pairs_into``.
 */
export function isUnconstrained(clockName) {
  return (
    typeof clockName === 'string' &&
    clockName.startsWith('<') &&
    clockName.endsWith('>')
  )
}

/**
 * The canonical clock → colour assignment for a graph.
 *
 * The clock set is the UNION of every place a clock name can appear:
 * per-node ``overlays.clock.clock``, the producer's optional
 * ``overlay_meta.clock.clocks`` manifest, and both ends of every edge's
 * ``overlays.clock.pairs``. Taking the union is what makes the overlay
 * legend, the node fills and the DOT-baked bridge cells agree — indices
 * are assigned over a sorted list, so two callers looking at two
 * different sets would hand the same clock two different colours.
 *
 * Returns ``Map<clockName, colour>``.
 */
export function buildClockPalette(graph) {
  const seen = new Set()
  for (const node of graph?.nodes || []) {
    const ov = node && node.overlays && node.overlays.clock
    if (ov && ov.clock) seen.add(ov.clock)
  }
  const meta = graph?.overlay_meta && graph.overlay_meta.clock
  if (meta && Array.isArray(meta.clocks)) {
    for (const entry of meta.clocks) {
      const name = typeof entry === 'string' ? entry : entry && entry.name
      if (typeof name === 'string' && name.length > 0) seen.add(name)
    }
  }
  for (const edge of graph?.edges || []) {
    const pairs = edge?.overlays?.clock?.pairs
    if (!Array.isArray(pairs)) continue
    for (const p of pairs) {
      if (!p) continue
      if (typeof p.src_clock === 'string' && !isUnconstrained(p.src_clock)) {
        seen.add(p.src_clock)
      }
      if (typeof p.dst_clock === 'string' && !isUnconstrained(p.dst_clock)) {
        seen.add(p.dst_clock)
      }
    }
  }
  const colours = clockPalette()
  const out = new Map()
  Array.from(seen)
    .sort()
    .forEach((name, idx) => out.set(name, colours[idx % colours.length]))
  return out
}

// ---------------------------------------------------------------------
// AXI backpressure ramp
// ---------------------------------------------------------------------

/** Percentage thresholds. Above ``hi`` is red; above ``mid`` is amber. */
export const BP_THRESHOLDS = { mid: 5, hi: 15 }

/** ``'lo' | 'mid' | 'hi'`` for a backpressure percentage. */
export function bpLevel(bpPct) {
  const v = typeof bpPct === 'number' ? bpPct : 0
  if (v > BP_THRESHOLDS.hi) return 'hi'
  if (v > BP_THRESHOLDS.mid) return 'mid'
  return 'lo'
}

/** Concrete colour for a backpressure percentage (canvas strokes). */
export function bpColor(bpPct) {
  const level = bpLevel(bpPct)
  if (level === 'hi') return token('--err')
  if (level === 'mid') return token('--warn')
  return token('--ok')
}

// ---------------------------------------------------------------------
// Coverage ramp
// ---------------------------------------------------------------------

/**
 * Continuous coverage tint: red (0%) → green (100%) at the lightness
 * the active theme pins via ``--cov-l`` (82% light, 38% dark). The
 * shared hub sheet documents this exact expression, so the schematic
 * overlay, the graph pane and the coverage app all land on one ramp.
 */
export function coverageColor(pct) {
  const clamped = Math.max(0, Math.min(100, typeof pct === 'number' ? pct : 0))
  const hue = Math.round(clamped * 1.2)
  return `hsl(${hue}, 70%, ${token('--cov-l')})`
}

/** The "this module has no LCOV data" fill. */
export function coverageNoDataColor() {
  return token('--cov-none')
}

// ---------------------------------------------------------------------
// Physical heat ramp (area + power)
// ---------------------------------------------------------------------

/**
 * The sequential area/power ramp, driven by a 0..1 fraction.
 *
 * SEQUENTIAL where the coverage ramp above is diverging, because the
 * quantity is: coverage has a bad end and a good end and earns a
 * red→green hue sweep, while area and power only have "more". So the
 * hue is fixed (``--heat-h``) and the ramp runs on lightness, from
 * ``--heat-l0`` (a rounding error) to ``--heat-l1`` (the row that owns
 * the design). The four tokens are the vendored hub sheet's, so the
 * schematic overlay and the hub's ``/phy`` pane land on one ramp — the
 * pane computes the same expression in CSS ``calc``.
 */
export function heatRampColor(fraction) {
  const f = Math.max(0, Math.min(1, typeof fraction === 'number' ? fraction : 0))
  const hue = token('--heat-h')
  const sat = token('--heat-s')
  const l0 = _percent(token('--heat-l0'), 96)
  const l1 = _percent(token('--heat-l1'), 66)
  const l = l0 + (l1 - l0) * f
  return `hsl(${hue}, ${sat}, ${_round(l)}%)`
}

/** The "this node was not measured" fill/stroke for the phys overlay. */
export function heatNoneColor() {
  return token('--heat-none')
}

/**
 * ``base``'s hue and lightness, saturated by a 0..1 fraction.
 *
 * This is the clock × area composition the phys overlay's fill uses
 * when the clock overlay is enabled too: the clock keeps the hue (its
 * whole job) and the area drives the saturation, so a big block in
 * ``clk_a`` and a small one in ``clk_a`` read as the same colour at
 * two intensities rather than as two colours. At ``fraction === 1``
 * the result is the clock pastel exactly, which is what keeps the
 * overlay panel's clock legend honest.
 *
 * Returns ``null`` for a colour this can't parse (only ``#rgb`` and
 * ``#rrggbb`` are ever in the clock palette) so the caller can fall
 * back to the sequential ramp rather than emit an invalid colour.
 */
export function saturateBy(base, fraction) {
  const hsl = _hexToHsl(base)
  if (hsl === null) return null
  const f = Math.max(0, Math.min(1, typeof fraction === 'number' ? fraction : 0))
  // 12% is "grey, but still this hue" — low enough to read as
  // near-neutral, high enough that the hue survives the trip.
  const sat = 12 + (hsl.s - 12) * f
  return `hsl(${_round(hsl.h)}, ${_round(Math.max(0, sat))}%, ${_round(hsl.l)}%)`
}

function _percent(raw, fallback) {
  const n = parseFloat(String(raw))
  return Number.isFinite(n) ? n : fallback
}

function _round(n) {
  return Math.round(n * 10) / 10
}

function _hexToHsl(hex) {
  if (typeof hex !== 'string') return null
  const m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(hex.trim())
  if (!m) return null
  const digits = m[1]
  const pairs =
    digits.length === 3
      ? [digits[0] + digits[0], digits[1] + digits[1], digits[2] + digits[2]]
      : [digits.slice(0, 2), digits.slice(2, 4), digits.slice(4, 6)]
  const [r, g, b] = pairs.map((p) => parseInt(p, 16) / 255)
  const max = Math.max(r, g, b)
  const min = Math.min(r, g, b)
  const l = (max + min) / 2
  const d = max - min
  if (d === 0) return { h: 0, s: 0, l: l * 100 }
  const s = d / (1 - Math.abs(2 * l - 1))
  let h
  if (max === r) h = ((g - b) / d) % 6
  else if (max === g) h = (b - r) / d + 2
  else h = (r - g) / d + 4
  h = (h * 60 + 360) % 360
  return { h, s: s * 100, l: l * 100 }
}

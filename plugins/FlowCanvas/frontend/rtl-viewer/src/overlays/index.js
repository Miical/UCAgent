// Overlay registry — JS side. Mirrors the Python OverlayRegistry
// in src/rtl_buddy_view/overlays/__init__.py so the toggles UI has
// a single place to look for built-in overlay names.
//
// Loading an unknown overlay (a name in ``graph.overlays_present``
// that has no JS module here) is a soft miss: the panel shows the
// name with an "unknown" tag and contributes nothing to render —
// never an error. This matches the issue's requirement that the
// viewer keeps working even when the producer ships a new overlay
// the consumer doesn't know about yet.

import { clockOverlay } from './clock.js'
import { coverageOverlay } from './coverage.js'
import { coverageLiveOverlay } from './coverage_live.js'
import { physOverlay } from './phys.js'
import { resetOverlay } from './reset.js'
import { waveOverlay } from './wave.js'

// ``coverage-live`` is a LIVE overlay: its data is the hub's
// ``/cov.json``, fetched at load time, not something a producer can
// bake into view.json. So it is never in ``graph.overlays_present``,
// never appears in ``overlaySummary``, and is never applied by
// ``applyOverlays`` — GraphCanvas applies it explicitly, after this
// pass. It is registered here anyway so the panel can reach its
// ``legend()`` through the same ``getOverlay`` door as everything
// else, and so a future producer that DOES emit the name gets the
// renderer rather than an "unknown" tag.
export const LIVE_OVERLAY_NAMES = ['coverage-live']

const BUILTINS = {
  clock: clockOverlay,
  coverage: coverageOverlay,
  'coverage-live': coverageLiveOverlay,
  phys: physOverlay,
  reset: resetOverlay,
  wave: waveOverlay,
}

export function getOverlay(name) {
  return BUILTINS[name] || null
}

// ``context`` is an optional bag of dynamic state overlays may
// consult — e.g. the live wave-values map sourced from
// ``wave_values_changed`` hub events, or the phys overlay's
// self-vs-subtree scope. The clock/reset/axi-perf overlays ignore
// it. Adding new keys is back-compat by construction.
//
// ``enabledOverlays`` is folded into the context each overlay sees,
// because one of them needs to know what ELSE is on: the phys
// overlay composes with clock (it takes the clock's hue and drives
// only the saturation) and yields the fill entirely to coverage. An
// overlay that asks "am I enabled" still gets that as its own
// boolean argument; this is the neighbours, not the self.
export function applyOverlays(svgRoot, graph, enabledOverlays, context = {}) {
  const shared = { ...context, enabledOverlays }
  // Always include 'wave' in the iteration. The Phase-8 producer
  // writes node.overlays.wave on each node (so graph.overlays_present
  // already lists it), but in the Phase-9 live path the values come
  // from the hub at runtime and ``overlays_present`` may not yet
  // include 'wave'. The overlay's apply() handles both — making it
  // unconditional means a freshly-loaded design with no static wave
  // block can still paint badges as soon as the first hub event
  // arrives.
  const names = new Set(graph.overlays_present)
  names.add('wave')
  for (const name of names) {
    const overlay = getOverlay(name)
    if (!overlay) continue
    overlay.apply(svgRoot, graph, enabledOverlays.has(name), shared)
  }
}

export function overlaySummary(graph) {
  return graph.overlays_present.map((name) => ({
    name,
    known: !!getOverlay(name),
  }))
}

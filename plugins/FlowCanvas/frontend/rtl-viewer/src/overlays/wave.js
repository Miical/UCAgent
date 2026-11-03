// 'wave' overlay renderer (Phase 8 + Phase 9).
//
// Paints per-port value badges on each node's <g> group. Two value
// sources flow in:
//
//   1. ``node.overlays.wave.ports[] = {name, value}`` — the offline
//      Phase-8 producer's pre-computed snapshot at a fixed t_fs.
//      Same shape the ASCII / dot renderers consume.
//   2. ``context.waveValuesByKey[`${wave_scope}.${signal}`]`` — the
//      Phase-9 live cache, refreshed by the hub's
//      ``wave_values_changed`` events. Wins over (1) when both are
//      present, on the assumption the live value is more current
//      than whatever the file was sampled at.
//
// The mapping from a view-side node+port to the wave-side
// (wave_scope, signal) is convention-driven: ``wave_scope`` is the
// instance path (``node.id``) and ``signal`` is ``port.name``. This
// matches what the hub's view→wave resolver produces under the
// default ``tb_prefix`` rules (no prefix transform). If a producer
// wants a different prefix, they can populate
// ``node.overlays.wave.ports[].wave_scope`` and
// ``node.overlays.wave.ports[].signal`` to override the convention —
// the renderer reads those when present.
//
// Visual: a small text block sits at the top-right of each node's
// bounding box, listing ``port=value`` lines for ports with known
// values. When the overlay is disabled (toggled off in the panel)
// the function clears any previously-painted badges so the canvas
// returns to its pristine state.

import { token } from '../theme.js'

const BADGE_CLASS = 'rb-wave-badge'

function cssEscape(s) {
  if (typeof CSS !== 'undefined' && CSS.escape) return CSS.escape(s)
  return String(s).replace(/[^a-zA-Z0-9_-]/g, (c) => `\\${c}`)
}

// Build the (port-name → value) map for a single node, fusing the
// static overlay snapshot with whatever the live wave-values cache
// supplies. Pure function — no DOM access, no store reads — so it's
// trivially testable.
export function resolvePortValues(node, waveValuesByKey) {
  const out = new Map()
  const ov = node?.overlays?.wave
  if (ov && Array.isArray(ov.ports)) {
    for (const p of ov.ports) {
      if (!p || typeof p.name !== 'string') continue
      if (typeof p.value === 'string') out.set(p.name, p.value)
    }
  }
  if (waveValuesByKey && typeof waveValuesByKey === 'object') {
    const cacheKeys = Object.keys(waveValuesByKey)
    // Each port may explicitly carry its (wave_scope, signal) pair
    // (overrides the instance-path convention). Otherwise we walk
    // hierarchy-path prefixes leaf→root looking for a suffix match
    // in the live cache — mirrors ``WaveMap.find_for_port`` in the
    // Python wave_annotations producer. Without this the live path
    // breaks the moment the VCD/FST wraps the design under a
    // testbench (``tb.dut.*`` keys vs. ``design_top.*`` node ids).
    const ports = Array.isArray(node?.ports) ? node.ports : []
    for (const port of ports) {
      if (!port || typeof port.name !== 'string') continue
      // Skip SystemVerilog interface ports — they're bundles, and a
      // single scalar literal next to one is ambiguous. The producer
      // also skips these in ``_wave_node_contribution``, but we
      // double-defend here because the live cache may surface a
      // suffix match for a coincidentally-named signal. (#102.)
      if (port.port_kind === 'interface') continue
      const overrideScope = port.wave_scope
      const overrideSig = port.signal
      let live
      if (
        typeof overrideScope === 'string' && overrideScope.length > 0 &&
        typeof overrideSig === 'string' && overrideSig.length > 0
      ) {
        live = waveValuesByKey[`${overrideScope}.${overrideSig}`]
      } else {
        live = findBySuffix(cacheKeys, waveValuesByKey, node.id, port.name)
      }
      if (typeof live === 'string') out.set(port.name, live)
    }
    // Producer may also surface explicit (wave_scope, signal) pairs
    // inside the overlay block itself — handy when port.name doesn't
    // match the surfer-side variable name. Same convention: latest-
    // writer-wins on collision (live cache wins via the explicit-key
    // route below).
    if (ov && Array.isArray(ov.ports)) {
      for (const p of ov.ports) {
        if (!p || typeof p.name !== 'string') continue
        if (typeof p.wave_scope === 'string' && typeof p.signal === 'string') {
          const live = waveValuesByKey[`${p.wave_scope}.${p.signal}`]
          if (typeof live === 'string') out.set(p.name, live)
        }
      }
    }
  }
  return out
}

function clearBadge(group) {
  // Multiple badges can exist on a single group when each port has
  // its own per-cell badge (block-flow view). Clear them all so the
  // next paint starts from a clean slate.
  for (const el of Array.from(group.querySelectorAll(`.${BADGE_CLASS}`))) {
    el.remove()
  }
}

// Hierarchy-suffix match between a node + port and the live wave-
// values cache. Walks ``node.id`` left→right shedding one segment
// at a time so a port on ``counter.u_ff`` matches a VCD signal at
// ``tb.dut.u_ff.q`` (drop ``counter`` → suffix ``u_ff.q`` → bare
// ``q``). Same shape as the Python ``WaveMap.find_for_port`` so
// offline + live both terminate at the same key.
function findBySuffix(cacheKeys, cache, nodeId, portName) {
  const segments = typeof nodeId === 'string' && nodeId.length > 0
    ? nodeId.split('.')
    : []
  for (let start = 0; start <= segments.length; start++) {
    const tail = segments.slice(start)
    const suffix = tail.length > 0
      ? `${tail.join('.')}.${portName}`
      : portName
    if (typeof cache[suffix] === 'string') return cache[suffix]
    // Find shortest cache key ending with ``.suffix`` (closest to leaf).
    const dotSuffix = `.${suffix}`
    let best = null
    for (const k of cacheKeys) {
      if (k.endsWith(dotSuffix) && (best === null || k.length < best.length)) {
        best = k
      }
    }
    if (best !== null) return cache[best]
  }
  return undefined
}

function paintBadge(svgRoot, group, nodeId, values) {
  // Always start from a clean slate — older badges from a different
  // value set could be stale (per-port badges are layout-sensitive).
  clearBadge(group)
  if (values.size === 0) return

  const NS = 'http://www.w3.org/2000/svg'
  // Block-flow view: viz.js renders each port as its own HTML-table
  // cell wrapped in an <a xlink:href="bf-in:<id>:<port>"> (or
  // ``bf-out:…``). GraphCanvas stamps that as ``data-bf-id``.
  // Anchor the badge BESIDE the specific port cell so the label
  // sits next to the pin it describes — much more readable than
  // one stacked list inside the parent cluster.
  let perPortCount = 0
  for (const [name, value] of values) {
    const cell =
      svgRoot.querySelector(
        `[data-bf-id="bf-in:${cssEscape(nodeId)}:${cssEscape(name)}"]`,
      ) ||
      svgRoot.querySelector(
        `[data-bf-id="bf-out:${cssEscape(nodeId)}:${cssEscape(name)}"]`,
      )
    if (!cell) continue
    let bbox
    try {
      bbox = cell.getBBox()
    } catch {
      continue
    }
    if (!bbox || !isFinite(bbox.x) || !isFinite(bbox.y)) continue
    const isInput = cell.getAttribute('data-bf-id')?.startsWith('bf-in:')
    const text = document.createElementNS(NS, 'text')
    text.setAttribute('class', BADGE_CLASS)
    text.setAttribute('font-family', token('--font-mono'))
    text.setAttribute('font-size', '9')
    text.setAttribute('fill', token('--fg'))
    text.setAttribute('pointer-events', 'none')
    // Inputs sit on the LEFT edge of the design; their label-text
    // should hang OUTSIDE the box to the left of the cell. Outputs
    // sit on the RIGHT edge; label hangs outside to the right.
    const gap = 4
    if (isInput) {
      text.setAttribute('x', String(bbox.x - gap))
      text.setAttribute('text-anchor', 'end')
    } else {
      text.setAttribute('x', String(bbox.x + bbox.width + gap))
      text.setAttribute('text-anchor', 'start')
    }
    // Vertically centre on the cell.
    text.setAttribute('y', String(bbox.y + bbox.height / 2))
    text.setAttribute('dominant-baseline', 'middle')
    text.textContent = `= ${value}`
    group.appendChild(text)
    perPortCount += 1
  }
  if (perPortCount > 0) return

  // Hier-view fallback: no per-port cells, so paint one stacked
  // badge inside the node group's bbox top-left.
  const shape = group.querySelector('polygon, ellipse, rect, path')
  if (!shape) return
  let bbox
  try {
    bbox = shape.getBBox()
  } catch {
    return
  }
  if (!bbox || !isFinite(bbox.x) || !isFinite(bbox.y)) return
  const inset = 4
  const anchorX = bbox.x + inset
  const anchorY = bbox.y + inset
  const badge = document.createElementNS(NS, 'text')
  badge.setAttribute('class', BADGE_CLASS)
  badge.setAttribute('font-family', token('--font-mono'))
  badge.setAttribute('font-size', '10')
  badge.setAttribute('fill', token('--fg'))
  badge.setAttribute('pointer-events', 'none')
  badge.setAttribute('x', String(anchorX))
  badge.setAttribute('y', String(anchorY))
  badge.setAttribute('text-anchor', 'start')
  // Deterministic order: alphabetical by port name. Matches the
  // sort discipline the dot/json renderers already enforce on
  // overlay output, so screenshots are stable run-to-run.
  const lines = Array.from(values.entries())
  lines.sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0))
  let dy = '0.8em'
  for (const [name, value] of lines) {
    const tspan = document.createElementNS(NS, 'tspan')
    tspan.setAttribute('x', String(anchorX))
    tspan.setAttribute('dy', dy)
    tspan.textContent = `${name}=${value}`
    badge.appendChild(tspan)
    dy = '1.1em'
  }
  group.appendChild(badge)
}

export const waveOverlay = {
  name: 'wave',

  /**
   * Paint port-value badges on every node that has known values.
   * Idempotent: applying twice yields the same DOM. Toggling
   * ``enabled`` off clears all badges.
   *
   * ``context.waveValuesByKey`` is the live wave-values map sourced
   * from the hub. Optional — when absent, only static
   * ``node.overlays.wave.ports[]`` values render.
   * ``context.selectedSignal`` is the ``{signal, wave_scope}`` of
   * the last ``signal_selected`` event; the corresponding badge
   * gets an accent stroke so the user can spot it on the canvas.
   */
  apply(svgRoot, graph, enabled, context = {}) {
    if (!svgRoot || !graph || !Array.isArray(graph.nodes)) return
    const waveValuesByKey = context.waveValuesByKey || {}
    const selectedSignal = context.selectedSignal || null

    for (const node of graph.nodes) {
      const group = svgRoot.querySelector(
        `[data-node-id="${cssEscape(node.id)}"]`,
      )
      if (!group) continue
      if (!enabled) {
        clearBadge(group)
        group.removeAttribute('data-wave-selected')
        continue
      }
      const values = resolvePortValues(node, waveValuesByKey)
      paintBadge(svgRoot, group, node.id, values)
      // Selected-signal highlight: tag the node group when one of
      // its ports matches the last signal_selected event. CSS picks
      // it up via ``[data-wave-selected]``.
      let selectedHere = false
      if (
        selectedSignal &&
        typeof selectedSignal.signal === 'string' &&
        typeof selectedSignal.wave_scope === 'string'
      ) {
        const key = `${selectedSignal.wave_scope}.${selectedSignal.signal}`
        const ports = Array.isArray(node.ports) ? node.ports : []
        for (const p of ports) {
          if (!p || typeof p.name !== 'string') continue
          const portKey =
            typeof p.wave_scope === 'string' && typeof p.signal === 'string'
              ? `${p.wave_scope}.${p.signal}`
              : `${node.id}.${p.name}`
          if (portKey === key) { selectedHere = true; break }
        }
      }
      if (selectedHere) {
        group.setAttribute('data-wave-selected', 'true')
      } else {
        group.removeAttribute('data-wave-selected')
      }
    }
  },

  /** Per-overlay legend payload for OverlayPanel.vue. */
  legend(graph) {
    // The wave overlay's contribution is value text, not a colour
    // swatch. Surface the t_fs when the static producer pinned one
    // so the panel can display "@ 100ns" alongside the toggle.
    const ov = graph?.overlay_meta?.wave
    if (ov && typeof ov.t_fs === 'string') {
      return [{ label: `@ ${ov.t_fs} fs`, swatch: null, kind: 'note' }]
    }
    return []
  },
}

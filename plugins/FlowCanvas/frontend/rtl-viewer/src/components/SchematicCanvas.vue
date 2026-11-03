<template>
  <main class="schematic-canvas">
    <div class="canvas-control-bar">
    <div class="canvas-tabs">
      <button
        type="button"
        class="canvas-tab"
        @click="store.setViewMode('hier')"
        title="Nested-cluster hierarchy from the producer's embedded layout"
      >Hierarchy</button>
      <button
        type="button"
        class="canvas-tab"
        @click="store.setViewMode('flow')"
        title="One-level signal flow"
      >Block Flow</button>
      <button
        type="button"
        class="canvas-tab active"
        title="Pin-level schematic laid out in-browser by elkjs"
      >Schematic</button>
    </div>
    <div class="graph-toolbar" v-if="hasPayload">
      <button type="button" @click="panZoom.fit">Reset</button>
      <button
        type="button"
        v-if="collapsedCount > 0"
        @click="store.expandAllSch()"
        :title="`Expand all ${collapsedCount} collapsed block(s)`"
      >Expand all</button>
      <button
        type="button"
        class="sch-export"
        @click="exportSvg"
        title="Download this sheet as a standalone SVG"
      >SVG</button>
      <button
        type="button"
        class="sch-export"
        @click="exportPng"
        :disabled="exporting"
        title="Download this sheet as a 2x PNG"
      >PNG</button>
    </div>
    </div>
    <p v-if="exportError" class="sch-export-error" role="alert">
      Export failed: {{ exportError }}
    </p>

    <!-- Degraded producer: the payload this canvas lays out simply
         isn't in the file. Same explain-shape the other empty states
         use (AxiPerfView's "No AXI performance data", the no-view
         placeholder): name what's missing, then the command. -->
    <section v-if="!hasPayload" class="sch-empty">
      <h2>No schematic payload in this view</h2>
      <p>
        The schematic canvas lays out <code>layout.elk</code>, which this
        <code>view.json</code> doesn't carry — it came from an
        <strong>older rtl-buddy-view</strong>.
      </p>
      <p class="empty-hint">
        Regenerate it with a current one:<br />
        <code>{{ RENDER_FOR_SCHEMATIC_HINT }}</code><br />
        Under the hub, restarting it against the same model is enough.
        The Hierarchy and Block Flow tabs work either way.
      </p>
    </section>

    <div
      v-else
      class="svg-host"
      ref="svgHostEl"
      @click="onClick"
      @dblclick="onDoubleClick"
      @contextmenu="onContextMenu"
      @mouseover="onHover"
      @mouseleave="hover = null"
    >
      <svg
        class="sch-svg"
        ref="svgEl"
        xmlns="http://www.w3.org/2000/svg"
        :width="sheet.frame.width"
        :height="sheet.frame.height"
        :data-layout-ms="layoutMs === null ? null : String(layoutMs)"
      >
        <g ref="rootG">
          <!-- Sheet frame + title block. Part of the SVG, not a CSS
               decoration, because it has to be in the exported file. -->
          <g class="sch-sheet-frame">
            <rect
              :x="sheet.frame.x"
              :y="sheet.frame.y"
              :width="sheet.frame.width"
              :height="sheet.frame.height"
            />
            <rect
              class="sch-title-block"
              :x="sheet.title.x"
              :y="sheet.title.y"
              :width="sheet.title.width"
              :height="sheet.title.height"
            />
            <text
              v-for="(row, i) in sheet.title.rows"
              :key="'t-' + row.label"
              class="sch-title-row"
              :x="sheet.title.x + TITLE_BLOCK_PAD"
              :y="sheet.title.y + TITLE_BLOCK_PAD + 11 + i * TITLE_ROW_HEIGHT"
            ><tspan class="sch-title-label">{{ row.label }}</tspan> {{ row.value }}</text>
          </g>

          <!-- Containment frames (design sheet + compound blocks)
               first, so leaf blocks and wires paint over them. -->
          <g
            v-for="box in frames"
            :key="'f-' + box.id"
            class="sch-frame"
            :class="{ sheet: box.sheet }"
            :data-node-id="box.id"
            :data-rb-selected="box.id === store.selection ? 'true' : null"
          >
            <rect
              :x="box.x"
              :y="box.y"
              :width="box.width"
              :height="box.height"
            />
          </g>

          <!-- Leaf blocks: sharp rectangles, schematic line weight.
               A folded compound is here too — same box, plus the
               ``collapsed`` marker its affordance keys on. -->
          <g
            v-for="box in blocks"
            :key="'b-' + box.id"
            class="sch-block"
            :class="{ blackbox: box.blackbox, collapsed: box.collapsed }"
            :data-node-id="box.id"
            :data-collapsed="box.collapsed ? 'true' : null"
            :data-clock="plateFill(box) ? box.clock : null"
            :data-rb-selected="box.id === store.selection ? 'true' : null"
          >
            <rect
              :x="box.x"
              :y="box.y"
              :width="box.width"
              :height="box.height"
              :style="plateFill(box) ? { fill: plateFill(box) } : null"
            />
          </g>

          <!-- Wires. Buses get the heavier stroke plus a /N slash;
               a clock-domain crossing is dashed in the error colour
               when the clock overlay is on. -->
          <g
            v-for="(wire, i) in model.wires"
            :key="'w-' + i"
            class="sch-wire"
            :class="{
              bus: wire.bus,
              cdc: cdcEdgeIds.has(wire.id),
              'cdc-sync': !cdcEdgeIds.has(wire.id) && cdcSyncEdgeIds.has(wire.id),
              hot: highlight.edges.has(wire.id),
              main: wire.emphasis === 'main',
              side: wire.emphasis === 'side',
              bundle: !!wire.bundle,
            }"
            :data-edge-id="wire.id"
            :data-edge-source="wire.sourceId"
            :data-edge-target="wire.targetId"
            :data-cdc="cdcEdgeIds.has(wire.id)
              ? 'true'
              : cdcSyncEdgeIds.has(wire.id) ? 'synchronized' : null"
            :data-emphasis="wire.emphasis || null"
            :data-bundle="wire.bundle || null"
          >
            <!-- Invisible fat stroke so a 1px wire is hoverable. -->
            <path class="sch-hit" :d="pathOf(wire)" />
            <path :d="pathOf(wire)" />
            <polygon :points="arrowOf(wire)" class="sch-arrow" />
            <template v-if="wire.slash">
              <line
                :x1="slashOf(wire).x - 4"
                :y1="slashOf(wire).y + 5"
                :x2="slashOf(wire).x + 4"
                :y2="slashOf(wire).y - 5"
              />
              <text
                class="sch-slash-text"
                :class="{ symbolic: wire.slashSymbolic }"
                :data-slash-symbolic="wire.slashSymbolic ? 'true' : null"
                :x="slashOf(wire).x + 6"
                :y="slashOf(wire).y - 4"
              >{{ wire.slash }}</text>
            </template>
          </g>

          <!-- Junction dots: ELK's own branch points for merged nets. -->
          <circle
            v-for="(j, i) in model.junctions"
            :key="'j-' + i"
            class="sch-junction"
            :cx="j.x"
            :cy="j.y"
            r="2.7"
          />

          <!-- Pin stubs crossing the border, formal name inside. -->
          <g
            v-for="pin in model.pins"
            :key="'p-' + pin.id"
            class="sch-pin"
            :class="[
              pin.kind,
              {
                unconnected: !pin.connected,
                unwired: pin.connected && !pin.wired,
                'on-frame': pin.onFrame,
                hot: highlight.pins.has(pin.id),
              },
            ]"
            :data-node-id="pin.nodeId"
            :data-port-id="pin.id"
          >
            <line
              :x1="pin.side === 'WEST' ? pin.x - PIN_STUB : pin.x"
              :y1="pin.y"
              :x2="pin.side === 'WEST' ? pin.x : pin.x + PIN_STUB"
              :y2="pin.y"
            />
            <!-- Clock: the standard wedge, drawn inside the border. -->
            <polygon
              v-if="pin.kind === 'clock'"
              class="sch-clock-wedge"
              :points="wedge(pin)"
            />
            <!-- Reset: an inversion bubble when the pin asserts low. -->
            <circle
              v-else-if="pin.activeLow"
              class="sch-bubble"
              :cx="pin.side === 'WEST' ? pin.x - 3 : pin.x + 3"
              :cy="pin.y"
              r="3"
            />
            <text
              :x="pin.side === 'WEST' ? pin.x + PIN_LABEL_GAP : pin.x - PIN_LABEL_GAP"
              :y="pin.y + 3.2"
              :text-anchor="pin.side === 'WEST' ? 'start' : 'end'"
            >{{ pin.name }}</text>
            <!-- No wire reaches this pin — the net it binds is driven
                 by logic this diagram doesn't draw (a procedural
                 block). Name the net outside the border so the pin
                 stays traceable instead of reading as dangling. -->
            <text
              v-if="pin.connected && !pin.wired && pin.net"
              class="sch-pin-net"
              :x="pin.side === 'WEST' ? pin.x - PIN_STUB - 3 : pin.x + PIN_STUB + 3"
              :y="pin.y + 3.2"
              :text-anchor="pin.side === 'WEST' ? 'end' : 'start'"
            >{{ pin.net }}</text>
            <!-- Bus tap: a drawn bundle stands for this pin without
                 landing on it, so mark the join the way every other
                 wire join is marked. -->
            <circle
              v-if="pin.busTap"
              class="sch-junction sch-bus-tap"
              :cx="pin.side === 'WEST' ? pin.x - PIN_STUB : pin.x + PIN_STUB"
              :cy="pin.y"
              r="2.7"
            />
          </g>

          <!-- Off-page connector flags for the design's own ports. -->
          <g
            v-for="flag in flagBoxes"
            :key="'g-' + flag.id"
            class="sch-flag"
            :class="{
              out: flag.out,
              clock: flag.isClock,
              reset: flag.isReset,
              hot: highlight.pins.has(flag.id),
            }"
            :data-node-id="flag.nodeId"
            :data-port-id="flag.id"
          >
            <path :d="flag.d" />
            <text :x="flag.textX" :y="flag.y + 3.4" :text-anchor="flag.anchor">
              {{ flag.name }}
            </text>
          </g>

          <!-- Refdes above, module type + params below, net names on
               their segments. All three come back from ELK with real
               positions, so nothing here guesses at free space. -->
          <g
            v-for="(label, i) in model.labels"
            :key="'l-' + i"
            class="sch-label"
            :class="label.role"
            :data-node-id="label.nodeId"
          >
            <text
              v-for="(line, li) in label.lines"
              :key="li"
              :x="label.role === 'refdes' || label.role === 'net'
                ? label.x
                : label.x + label.width / 2"
              :y="label.y + 9 + li * PARAM_LINE_HEIGHT"
              :text-anchor="label.role === 'refdes' || label.role === 'net'
                ? 'start'
                : 'middle'"
              :class="{ param: label.role === 'type' && li > 0 }"
            >{{ line }}</text>
            <!-- ▾/▸ beside the refdes: the affordance that says this
                 block has an inside. Placed after the measured refdes
                 width so it can never sit on the frame border. -->
            <text
              v-if="label.role === 'refdes' && collapsible.has(label.nodeId)"
              class="sch-toggle"
              :data-collapse-toggle="label.nodeId"
              :x="label.x + label.width + 5"
              :y="label.y + 9"
            >{{ store.schCollapsed.has(label.nodeId) ? '▸' : '▾' }}</text>
          </g>
        </g>
      </svg>
    </div>
  </main>
</template>

<script setup>
// The elkjs schematic canvas (epic rtl-buddy-sch#163, P2).
//
// The one architectural difference from GraphCanvas: **the SVG is
// Vue's**. viz.js hands back a finished SVG string, so GraphCanvas
// injects it and then recovers identity by scraping Graphviz's
// ``<title>`` elements and reversing a producer-supplied
// ``cluster_lookup``. elkjs hands back *geometry*, which means every
// rect, stub and wire is emitted by a template that already knows
// which instance path it belongs to — ``data-node-id`` is bound, not
// reconstructed. That is what makes the P3 work (expand/collapse,
// net hover) tractable at all.
//
// Everything else deliberately matches GraphCanvas so the two tabs
// feel like one canvas: same store actions on click / dblclick /
// right-click, the same ``data-rb-selected`` highlight vocabulary,
// the same toolbar, the same pan/zoom gestures (now shared via
// usePanZoom).
//
// Seam for P3: the layout runs in ``relayout()`` from the payload
// returned by ``visiblePayload`` — a collapse action becomes a filter
// on that computed plus a re-run, with no change to the draw path.
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useViewerStore } from '../store.js'
import { usePanZoom } from '../composables/usePanZoom.js'
import { registerSvgProvider, unregisterSvgProvider } from '../capture.js'
import { token, themeVersion } from '../theme.js'
import { buildClockPalette } from '../palette.js'
import { RENDER_FOR_SCHEMATIC_HINT } from '../cliHints.js'
import {
  FLAG_HEIGHT,
  NO_HIGHLIGHT,
  PARAM_LINE_HEIGHT,
  PIN_LABEL_GAP,
  PIN_STUB,
  TITLE_BLOCK_PAD,
  TITLE_ROW_HEIGHT,
  buildElkGraph,
  collapsibleIds,
  flagPath,
  highlightFor,
  nodeIdOfEndpoint,
  polylinePath,
  resolveMeasure,
  sheetFrame,
  subtreeOf,
  titleBlockRows,
  toSchematic,
} from '../layout/elkSchematic.js'
import {
  exportFileName,
  exportSchematicPng,
  exportSchematicSvg,
} from '../schExport.js'

const store = useViewerStore()
const svgHostEl = ref(null)
const svgEl = ref(null)
const rootG = ref(null)

const EMPTY_MODEL = Object.freeze({
  width: 0,
  height: 0,
  boxes: [],
  pins: [],
  flags: [],
  wires: [],
  junctions: [],
  labels: [],
})
const model = ref(EMPTY_MODEL)

// P3 seam: the payload actually laid out. Today it is the producer's
// tree, scoped by the descend/ascend path; a collapse action prunes
// ``children`` here and calls ``relayout()`` — the draw path below
// never learns about it.
//
// Note this reads ``store.graph``, not ``store.displayGraph``: the
// latter drops ``layout`` wholesale when a scope is set (the embedded
// DOT was laid out for the full design and would redraw the wrong
// picture). The ELK payload has no such problem — it is a tree, and
// the scope is a node in it, so descending is a subtree lookup.
const visiblePayload = computed(() => {
  const elk = store.graph && store.graph.layout && store.graph.layout.elk
  if (!elk || typeof elk !== 'object' || !elk.id) return null
  return subtreeOf(elk, store.rootInstancePath) || elk
})
const hasPayload = computed(() => visiblePayload.value !== null)

const frames = computed(() => model.value.boxes.filter((b) => b.compound))
const blocks = computed(() => model.value.boxes.filter((b) => !b.compound))

// --- collapse (#163 P3) -----------------------------------------------------

// Every compound block in the payload, folded or not. Derived from
// the UNCOLLAPSED payload: a folded block has no children left to
// prove it is compound, and the toggle that unfolds it has to be
// drawn on the folded box.
const collapsible = computed(() => collapsibleIds(visiblePayload.value))
const collapsedCount = computed(() => {
  let n = 0
  for (const id of store.schCollapsed) if (collapsible.value.has(id)) n++
  return n
})

// --- hover highlighting (#163 P3) -------------------------------------------

// ``{edgeId}`` or ``{pinId}`` for whatever the cursor is on, or null.
const hover = ref(null)
const highlight = computed(() =>
  hover.value ? highlightFor(model.value, hover.value) : NO_HIGHLIGHT,
)

// --- clock-domain shading (#163 P4) -----------------------------------------

// The same ``buildClockPalette`` the clock overlay and the DOT builder
// use, over the same graph — so a block is the same colour on this tab
// as it is on Hierarchy. Reading ``themeVersion`` makes a theme flip
// re-resolve the pastels (they are tokens, and this path holds
// concrete values because SVG ``fill`` here is an overlay decision,
// not a static style).
const clockPalette = computed(() => {
  void themeVersion.value
  if (!store.graph || !store.enabledOverlays.has('clock')) return null
  const palette = buildClockPalette(store.graph)
  return palette.size > 0 ? palette : null
})

/**
 * The per-domain tint for a block's plate, or null.
 *
 * Leaf (and folded) blocks only. Tinting a containment frame fills the
 * whole subtree's area and drowns the per-leaf colours nested inside
 * it — the same rule ``overlays/clock.js`` applies when it skips
 * clusters.
 */
function plateFill(box) {
  if (!clockPalette.value || !box || !box.clock) return null
  return clockPalette.value.get(box.clock) || null
}

// Instance path → the source clocks crossing into it, from the
// producer's per-edge ``overlays.clock`` block (view.json v1). That is
// the only place the CDC verdict lives: it comes from the domain map's
// ``async_per_sdc`` crossings, which a clock-name comparison cannot
// reproduce (two names can be the same domain, and an SDC false path
// can make one crossing benign).
//
// ``hazard`` picks which pairs count (rtl-buddy-sch#211): a pair the
// producer marked ``hazard: false`` is recognised-safe; any other pair
// — including one from a payload that predates the field — is a hazard.
function cdcSourcesByTarget(hazard) {
  const out = new Map()
  for (const edge of store.graph?.edges || []) {
    const ov = edge.overlays && edge.overlays.clock
    if (!ov || !ov.crossing) continue
    const set = out.get(edge.to) || new Set()
    for (const pair of ov.pairs || []) {
      if (!pair || typeof pair.src_clock !== 'string') continue
      if ((pair.hazard !== false) === hazard) set.add(pair.src_clock)
    }
    if (set.size) out.set(edge.to, set)
  }
  return out
}

// Edge ids whose wire is a ⚠CDC hazard crossing, resolved once per
// model rather than per wire segment.
const cdcEdgeIds = computed(() => crossingWireIds(cdcSourcesByTarget(true)))
// Edge ids whose wire carries only recognised-safe crossings: drawn in
// the synchroniser teal, never the hazard dash. A wire in both sets is
// a hazard — the template checks ``cdcEdgeIds`` first.
const cdcSyncEdgeIds = computed(() => crossingWireIds(cdcSourcesByTarget(false)))

function crossingWireIds(sourcesByTarget) {
  const ids = new Set()
  if (!clockPalette.value || sourcesByTarget.size === 0) return ids
  const clockOf = new Map(model.value.boxes.map((b) => [b.id, b.clock]))
  // A folded block swallows its children, so a crossing that lands on
  // a descendant now lands on the box: match the target prefix, not
  // just the id.
  const sourcesFor = (nodeId) => {
    const out = new Set()
    for (const [target, clocks] of sourcesByTarget) {
      if (target === nodeId || target.startsWith(nodeId + '.')) {
        for (const c of clocks) out.add(c)
      }
    }
    return out
  }
  for (const wire of model.value.wires) {
    const target = nodeIdOfEndpoint(wire.targetId)
    const source = nodeIdOfEndpoint(wire.sourceId)
    if (!target) continue
    const clocks = sourcesFor(target)
    if (clocks.size === 0) continue
    const srcClock = source ? clockOf.get(source) : null
    // No source clock to check against (a top-level flag, or a block
    // the overlay never bound) — the crossing into the target is the
    // evidence we have, so mark it.
    if (!srcClock || clocks.has(srcClock)) ids.add(wire.id)
  }
  return ids
}

// --- sheet frame + title block (#163 P4) ------------------------------------

const sheet = computed(() =>
  sheetFrame(
    model.value,
    titleBlockRows({
      top: store.graph ? store.graph.top : null,
      toolVersion: store.graph && store.graph.tool ? store.graph.tool.version : null,
      scope: store.rootInstancePath,
      model: store.activeModel,
    }),
  ),
)

// Flag geometry is derived, not laid out: ELK gives us the port's
// point on the sheet border and the pentagon hangs off it.
const flagBoxes = computed(() =>
  model.value.flags.map((f) => {
    const measure = measurer()
    const width = Math.max(46, measure(f.name, 10) + 26)
    const x = f.out ? f.x : f.x - width
    const y = f.y - FLAG_HEIGHT / 2
    return {
      ...f,
      // Both flags point rightward — the direction the signal flows
      // on an LR sheet — so an input's tip lands exactly on its
      // wire. The label sits in the flat body, clear of the tip.
      d: flagPath(x, y, width, FLAG_HEIGHT),
      textX: x + 8,
      anchor: 'start',
    }
  }),
)

let _measure = null
function measurer() {
  if (_measure === null) _measure = resolveMeasure(token('--font-mono'))
  return _measure
}

function pathOf(wire) {
  return polylinePath(wire.points)
}

// Arrowhead at the sink end, oriented along the last segment.
function arrowOf(wire) {
  const pts = wire.points
  const a = pts[pts.length - 2]
  const b = pts[pts.length - 1]
  const ang = Math.atan2(b.y - a.y, b.x - a.x)
  const s = 5
  return [
    `${b.x},${b.y}`,
    `${b.x - s * Math.cos(ang - 0.45)},${b.y - s * Math.sin(ang - 0.45)}`,
    `${b.x - s * Math.cos(ang + 0.45)},${b.y - s * Math.sin(ang + 0.45)}`,
  ].join(' ')
}

// Bus slash sits a third of the way along the first segment — near
// the driver, which is where a schematic reader looks for the width.
function slashOf(wire) {
  const p0 = wire.points[0]
  const p1 = wire.points[1]
  return { x: p0.x + (p1.x - p0.x) * 0.35, y: p0.y + (p1.y - p0.y) * 0.35 }
}

// Clock wedge: an isoceles triangle standing on the border, pointing
// into the block. The convention every datasheet uses for a clocked
// input, and the reason a clock pin can stay visible while its net
// stays unrouted (elk.json §4).
function wedge(pin) {
  const inward = pin.side === 'WEST' ? 1 : -1
  const h = 4.5
  const d = 7
  return [
    `${pin.x},${pin.y - h}`,
    `${pin.x + inward * d},${pin.y}`,
    `${pin.x},${pin.y + h}`,
  ].join(' ')
}

function now() {
  return typeof performance !== 'undefined' && performance.now
    ? performance.now()
    : Date.now()
}

let _elk = null
async function elkEngine() {
  if (_elk === null) {
    const { default: ELK } = await import('elkjs/lib/elk.bundled.js')
    _elk = new ELK()
  }
  return _elk
}

// Wall-clock ms of the last elkjs run, mirrored onto the SVG as
// ``data-layout-ms``. Not decoration: the collapse feature re-runs the
// whole layout on every toggle, and whether that is acceptable on the
// main thread is a measurement, not an opinion. elkjs ships a worker
// build, but the SPA is also shipped as ONE self-contained HTML file
// (embed.py inlines every ``<script src>``), and a worker resolved at
// runtime is exactly the chunk that inlining would miss — so the bar
// for moving off the main thread is "the measurement says we must".
const layoutMs = ref(null)

let _relayoutToken = 0
async function relayout() {
  const payload = visiblePayload.value
  if (!payload) {
    model.value = EMPTY_MODEL
    return
  }
  const mine = ++_relayoutToken
  const graph = buildElkGraph(payload, {
    collapsed: store.schCollapsed,
    measure: measurer(),
  })
  let laid
  const started = now()
  try {
    const elk = await elkEngine()
    laid = await elk.layout(graph)
  } catch (e) {
    store.$patch({ status: 'error', error: `elkjs layout failed: ${e.message}` })
    return
  }
  layoutMs.value = Math.round((now() - started) * 10) / 10
  // A newer relayout finished first (model switch mid-flight) — drop
  // this result rather than painting a stale picture over it.
  if (mine !== _relayoutToken) return
  model.value = toSchematic(laid)
  requestAnimationFrame(() => {
    panZoom.fit()
    bringSelectionIntoView(store.selection)
  })
}

const panZoom = usePanZoom({
  hostEl: svgHostEl,
  getRoot: () => rootG.value,
  // The sheet frame, not the laid-out extent: the border and title
  // block sit outside the content in negative / overflowing
  // coordinates, and fitting to the content alone crops them.
  getContentBox: () => ({ ...sheet.value.frame }),
})

function boxFor(nodeId) {
  return model.value.boxes.find((b) => b.id === nodeId) || null
}

function bringSelectionIntoView(id) {
  if (!id) return
  const box = boxFor(id)
  if (box) panZoom.bringIntoView(box)
}

// --- interactions (parity with GraphCanvas) --------------------------------

function nodeFromEvent(e) {
  const group = e.target.closest('[data-node-id]')
  if (!group) return null
  const id = group.getAttribute('data-node-id')
  if (!id) return null
  const node = store.nodesById.get(id)
  return node ? { id, node } : null
}

function onClick(e) {
  // The ▾/▸ affordance is a control, not part of the block: it folds
  // without touching selection or scope.
  const toggle = e.target.closest('[data-collapse-toggle]')
  if (toggle) {
    e.stopPropagation()
    store.toggleSchCollapsed(toggle.getAttribute('data-collapse-toggle'))
    return
  }
  const hit = nodeFromEvent(e)
  if (hit) {
    store.select(hit.id)
    return
  }
  // Background click clears, exactly as in the hier/flow canvas.
  store.clearSelection()
}

/**
 * Double-click: fold a compound block, descend into anything else.
 *
 * This is the one interaction that deliberately diverges from
 * GraphCanvas. On a canvas that can only show one level at a time,
 * double-click *has* to mean "go in". Here the compound block is
 * already drawn as a containment frame with its children inside it, so
 * "go in" is a no-op you can see — and the useful gesture on a block
 * that is showing its guts is to close it. Descending is still one
 * double-click away on any leaf, and the breadcrumb / hierarchy tree
 * still scope the sheet.
 */
function onDoubleClick(e) {
  const hit = nodeFromEvent(e)
  if (!hit) return
  e.preventDefault()
  store.select(hit.id)
  if (collapsible.value.has(hit.id)) {
    store.toggleSchCollapsed(hit.id)
    return
  }
  store.descend(hit.id)
}

function onHover(e) {
  // A pin wins over the wire that lands on it: the pointer is on the
  // more specific thing, and pin-hover is the stricter query.
  const pin = e.target.closest('[data-port-id]')
  if (pin) {
    hover.value = { pinId: pin.getAttribute('data-port-id') }
    return
  }
  const wire = e.target.closest('[data-edge-id]')
  if (wire) {
    hover.value = { edgeId: wire.getAttribute('data-edge-id') }
    return
  }
  hover.value = null
}

// --- static export (#163 P5) ------------------------------------------------

const exporting = ref(false)
const exportError = ref(null)

function exportBox() {
  return { ...sheet.value.frame }
}

function exportSvg() {
  if (!svgEl.value) return
  exportError.value = null
  try {
    exportSchematicSvg(svgEl.value, {
      viewBox: exportBox(),
      fileName: exportFileName(store.rootInstancePath || store.graph?.top, 'svg'),
    })
  } catch (err) {
    exportError.value = err && err.message ? err.message : String(err)
  }
}

async function exportPng() {
  if (!svgEl.value || exporting.value) return
  exporting.value = true
  try {
    exportError.value = null
    await exportSchematicPng(svgEl.value, {
      viewBox: exportBox(),
      fileName: exportFileName(store.rootInstancePath || store.graph?.top, 'png'),
    })
  } catch (err) {
    // Local, not ``status: 'error'``: a failed download must not
    // replace the drawing with an error screen — the canvas is still
    // perfectly good, only the rasteriser gave up (capture.js bounds
    // it at 5s precisely because that pipeline can wedge).
    exportError.value = err && err.message ? err.message : String(err)
  } finally {
    exporting.value = false
  }
}

function onContextMenu(e) {
  const hit = nodeFromEvent(e)
  if (!hit) return
  e.preventDefault()
  store.select(hit.id)
}

watch(visiblePayload, async (payload, before) => {
  // The host div is behind a ``v-if``: mounting straight into the
  // empty state leaves usePanZoom with nothing to listen on. Wire it
  // up the moment a payload turns the canvas on.
  if (payload && !before) {
    await nextTick()
    panZoom.attach()
  }
  relayout()
})
// Every collapse toggle re-runs the whole layout. Incremental
// re-layout is not a thing elkjs offers, and it would not help here:
// folding a block changes the layer assignment of everything
// downstream of it, so the "unchanged" part of the picture is not
// unchanged. See ``layoutMs`` for why the main thread is fine.
watch(
  () => store.schCollapsed,
  () => relayout(),
)
watch(
  () => store.selection,
  (id) => bringSelectionIntoView(id),
)

// The capture module reads the live SVG through a getter (see
// GraphCanvas): the element identity moves when the canvas
// re-renders, and a function dodges that staleness.
const _captureSvgProvider = () => svgEl.value

onMounted(() => {
  registerSvgProvider(_captureSvgProvider)
  relayout()
})
onBeforeUnmount(() => {
  unregisterSvgProvider(_captureSvgProvider)
})
</script>

<style scoped>
/* No hex literals: every colour below resolves through the vendored
   hub sheet / tokens.css (docs/design-tokens.md). The schematic reads
   as black-on-white in light and inverts cleanly in dark, because the
   SVG is ours — unlike the producer's DOT, there is nothing baked to
   re-tint. */
.schematic-canvas {
  flex: 1;
  position: relative;
  overflow: hidden;
  background: var(--bg);
}
.canvas-tabs {
  position: absolute;
  bottom: 0.5rem;
  left: 0.5rem;
  display: flex;
  align-items: center;
  gap: 0.25rem;
  z-index: 10;
  font-size: 0.85rem;
}
.canvas-tab {
  border: 1px solid var(--line-strong);
  background: var(--panel);
  color: var(--fg);
  padding: 0.25rem 0.6rem;
  cursor: pointer;
  border-radius: var(--radius-2);
  font-family: inherit;
}
.canvas-tab.active {
  background: var(--accent);
  color: var(--accent-contrast);
  border-color: var(--accent);
}
.graph-toolbar {
  position: absolute;
  bottom: 0.5rem;
  right: 0.5rem;
  display: flex;
  gap: 0.25rem;
  z-index: 10;
}
.graph-toolbar button {
  border: 1px solid var(--line-strong);
  background: var(--panel);
  color: var(--fg);
  padding: 0.25rem 0.5rem;
  cursor: pointer;
  border-radius: var(--radius-2);
}
.graph-toolbar button:disabled {
  cursor: default;
  opacity: 0.55;
}
.sch-export-error {
  position: absolute;
  bottom: 3rem;
  right: 0.5rem;
  z-index: 10;
  margin: 0;
  max-width: 40ch;
  padding: 0.3rem 0.55rem;
  font-size: 0.78rem;
  color: var(--fg);
  background: var(--err-bg);
  border: 1px solid var(--err);
  border-radius: var(--radius-2);
}
.svg-host {
  width: 100%;
  height: calc(100% - 3rem);
  margin-bottom: 3rem;
  cursor: grab;
}
.svg-host:active { cursor: grabbing; }
.sch-svg {
  width: 100%;
  height: 100%;
  font-family: var(--font-mono);
}

/* -- schematic dress ------------------------------------------------ */
/* Sharp corners, 1.5px block border, 1px wires, 2.5px buses. Rounded
   corners read as "diagram"; this is meant to read as a schematic. */
.sch-block rect {
  fill: var(--panel);
  stroke: var(--fg);
  stroke-width: 1.5;
}
.sch-block.blackbox rect {
  stroke-dasharray: 5 3;
}
.sch-frame rect {
  fill: none;
  stroke: var(--fg-faint);
  stroke-width: 1.2;
  pointer-events: all;
}
.sch-frame.sheet rect {
  stroke: var(--fg-muted);
  stroke-width: 1.8;
}
.sch-block,
.sch-frame,
.sch-pin,
.sch-flag {
  cursor: pointer;
}

.sch-wire path {
  fill: none;
  stroke: var(--fg);
  stroke-width: 1.1;
}
.sch-wire.bus path {
  stroke-width: 2.5;
}
/* rbsch presentation (#180). Emphasis mirrors the dot figure: the
   author's main path carries weight, side wiring recedes to the
   faint tier so the money path pops. A named bundle without an
   explicit emphasis sits between plain and main — one interface,
   one visually solid wire. The explicit per-net word wins by
   selector order (these three rules are mutually exclusive classes
   at the model layer, so order is documentation, not a fight). */
.sch-wire.bundle path {
  stroke-width: 2;
}
.sch-wire.main path {
  stroke-width: 2.6;
}
.sch-wire.side path {
  stroke: var(--fg-faint);
  stroke-width: 0.8;
}
.sch-wire.side .sch-arrow {
  fill: var(--fg-faint);
}
.sch-wire.side .sch-hit {
  stroke: transparent;
}
/* Hover target. A 1px wire is unhittable with a mouse, so every wire
   carries a fat transparent twin underneath it. ``stroke`` catches the
   pointer even when the paint is transparent. */
.sch-wire .sch-hit {
  stroke: transparent;
  stroke-width: 12;
  pointer-events: stroke;
}
.sch-wire {
  cursor: crosshair;
}
/* ⚠CDC: dashed, in the error colour — the same vocabulary the clock
   overlay paints crossings with on the hier canvas. */
.sch-wire.cdc path {
  stroke: var(--err);
  stroke-dasharray: 5 3;
}
.sch-wire.cdc .sch-arrow {
  fill: var(--err);
}
.sch-wire.cdc .sch-hit {
  stroke: transparent;
  stroke-dasharray: none;
}
/* A recognised-safe crossing (rtl-buddy-sch#211): solid, in the
   synchroniser teal the reset overlay uses — information, not a
   hazard. */
.sch-wire.cdc-sync path {
  stroke: var(--reset-sync);
}
.sch-wire.cdc-sync .sch-arrow {
  fill: var(--reset-sync);
}
.sch-wire.cdc-sync .sch-hit {
  stroke: transparent;
}
.sch-wire line {
  stroke: var(--fg);
  stroke-width: 1.2;
}
.sch-arrow {
  fill: var(--fg);
}
.sch-junction {
  fill: var(--fg);
}
.sch-slash-text {
  fill: var(--fg);
  font-size: 8.5px;
}
/* An algebraic width (``/PTR_W``) is not a measurement — it is the
   declaration's own arithmetic, unresolved because nothing has
   elaborated the design yet. Italic is the one typographic signal
   that says "symbol, not count" without a legend. */
.sch-slash-text.symbolic {
  font-style: italic;
}

.sch-pin line {
  stroke: var(--fg);
  stroke-width: 1.4;
}
.sch-pin text {
  fill: var(--fg-muted);
  font-size: 9px;
}
.sch-pin-net {
  /* The net a pin binds, shown only when no wire reaches it. Dimmer
     and smaller than the formal name inside the box: it is a
     reference, not a label on the symbol. */
  font-size: 7.5px;
  fill: var(--fg-faint);
  font-style: italic;
}
/* Halo. Every text that lives OUTSIDE a block — net names on wires,
   the net reference on an unwired pin — sits in the routing channel,
   so sooner or later a wire or a neighbouring label runs through it.
   Painting the glyph outline in the sheet colour first keeps it
   legible instead of letting the wire read as a strikethrough. Same
   trick a map uses for place names over contour lines. */
.sch-pin-net,
.sch-label.net text,
.sch-pin.on-frame text {
  paint-order: stroke;
  stroke: var(--bg);
  /* Wide enough to clear a bus stroke (2.5px) crossing the glyph,
     not just a 1.1px signal wire — a 3px halo left the fat wires
     reading as a strikethrough through the label. */
  stroke-width: 4.5px;
  stroke-linejoin: round;
}
.sch-pin.hot .sch-pin-net {
  fill: var(--accent);
}
.sch-pin.unconnected line {
  stroke: var(--fg-faint);
  stroke-dasharray: 3 2;
}
.sch-clock-wedge {
  fill: none;
  stroke: var(--fg);
  stroke-width: 1.2;
}
.sch-bubble {
  fill: var(--panel);
  stroke: var(--fg);
  stroke-width: 1.2;
}

.sch-flag path {
  fill: var(--panel-2);
  stroke: var(--fg);
  stroke-width: 1.3;
}
.sch-flag text {
  fill: var(--fg);
  font-size: 10px;
}

.sch-label text {
  fill: var(--fg);
  font-size: 10.5px;
}
.sch-label.refdes text {
  font-size: 11px;
  font-weight: 600;
}
.sch-label.net text {
  fill: var(--fg-muted);
  font-size: 8.5px;
}
.sch-label text.param {
  fill: var(--fg-muted);
  font-size: 9px;
}

/* -- sheet frame + title block --------------------------------------- */
/* Drawn as SVG, not as a CSS border on the host, because it has to be
   in the exported file. */
.sch-sheet-frame rect {
  fill: none;
  stroke: var(--fg-muted);
  stroke-width: 1.2;
}
.sch-sheet-frame rect.sch-title-block {
  fill: var(--panel);
  stroke: var(--fg-muted);
  stroke-width: 1.2;
}
.sch-title-row {
  fill: var(--fg);
  font-size: 9.5px;
}
.sch-title-label {
  fill: var(--fg-faint);
  letter-spacing: 0.06em;
}

/* -- collapse affordance --------------------------------------------- */
.sch-toggle {
  fill: var(--fg-muted);
  font-size: 11px;
  cursor: pointer;
  user-select: none;
}
.sch-toggle:hover {
  fill: var(--accent);
}
/* A folded compound is a leaf-shaped box that isn't a leaf. The
   heavier border is the datasheet convention for a hierarchical
   block, and unlike a shadow it survives the export (which inlines
   stroke properties, not filters). */
.sch-block.collapsed rect {
  stroke-width: 2.4;
}

/* -- net hover highlighting (P3) --------------------------------------
   Same halo vocabulary as the selection mark below — accent stroke
   plus a two-stop accent glow — so "the thing under the cursor" and
   "the thing you picked" read as one family, distinguished by weight
   rather than by hue. */
.sch-wire.hot path {
  stroke: var(--accent);
  stroke-width: 2.2;
  filter: drop-shadow(0 0 3px color-mix(in srgb, var(--accent) 60%, transparent));
}
.sch-wire.hot.bus path {
  stroke-width: 3.4;
}
.sch-wire.hot .sch-hit {
  stroke: transparent;
  filter: none;
}
.sch-wire.hot .sch-arrow {
  fill: var(--accent);
}
.sch-wire.hot text {
  fill: var(--accent);
}
.sch-pin.hot line {
  stroke: var(--accent);
  stroke-width: 2.2;
  filter: drop-shadow(0 0 3px color-mix(in srgb, var(--accent) 60%, transparent));
}
.sch-pin.hot text {
  fill: var(--accent);
  font-weight: 600;
}
.sch-flag.hot path {
  stroke: var(--accent);
  stroke-width: 2;
  filter: drop-shadow(0 0 3px color-mix(in srgb, var(--accent) 60%, transparent));
}

/* Selected-node accent — the same vocabulary GraphCanvas paints:
   ``data-rb-selected`` on the group, a 2.5px accent stroke plus a
   two-stop accent halo so the mark survives whatever fill an overlay
   put underneath it. */
[data-rb-selected] > rect {
  stroke: var(--accent);
  stroke-width: 2.5;
  filter:
    drop-shadow(0 0 3px color-mix(in srgb, var(--accent) 70%, transparent))
    drop-shadow(0 0 9px color-mix(in srgb, var(--accent) 35%, transparent));
}

/* -- empty state ---------------------------------------------------- */
.sch-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 0.6rem;
  height: 100%;
  text-align: center;
  padding: 2rem;
  box-sizing: border-box;
}
.sch-empty h2 {
  margin: 0;
  font-size: 1rem;
}
.sch-empty .empty-hint {
  font-size: 0.85rem;
  color: var(--fg-muted);
  max-width: 60ch;
  line-height: 1.6;
  background: var(--panel-2);
  padding: 0.75rem 1rem;
  border-radius: var(--radius-3);
}
.sch-empty code {
  background: var(--line);
  padding: 0.05rem 0.3rem;
  border-radius: var(--radius-1);
  font-size: 0.85em;
  font-family: var(--font-mono);
}


/* Dedicated compact footer, separated from the drawing surface. */
.canvas-control-bar {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  z-index: 10;
  display: flex;
  align-items: center;
  gap: 6px;
  min-height: 32px;
  padding: 4px 8px;
  box-sizing: border-box;
  background: #fff;
  border-top: 1px solid var(--line);
  overflow-x: auto;
  scrollbar-width: thin;
}
.canvas-control-bar .canvas-tabs,
.canvas-control-bar .graph-toolbar {
  position: static;
  display: flex;
  align-items: center;
  gap: 3px;
  flex-shrink: 0;
}
.canvas-control-bar .graph-toolbar { margin-left: auto; }
.canvas-control-bar button {
  font-size: 10px;
  line-height: 16px;
  padding: 1px 6px;
  border-radius: 4px;
  background: #fff;
  color: #536171;
  border-color: #dce2e8;
  white-space: nowrap;
}
.canvas-control-bar button:hover { background: #f3f5f7; }
.canvas-control-bar .canvas-tab.active {
  background: #edf3ff;
  color: #245bd6;
  border-color: #c5d6fa;
}
.canvas-control-bar .scope-breadcrumb { font-size: 10px; color: #657184; }
.svg-host { height: calc(100% - 32px); margin-bottom: 32px; }

</style>

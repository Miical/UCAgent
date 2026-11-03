// ELK schematic layout — the pure half of the elkjs canvas (#163 P2).
//
// Takes ``view.json``'s ``layout.elk`` payload (docs/elk-json-v1.md)
// and produces two things, in two steps that are testable without a
// browser and without elkjs:
//
//   1. ``buildElkGraph(payload, {collapsed, measure})`` → the graph
//      elkjs lays out. Everything presentational lives here: sides,
//      port order, node sizes, spacings, label boxes, and which
//      compound blocks are folded shut. The payload deliberately
//      ships none of it (§5.1 of the contract) — the consumer is the
//      only party that knows the font it will render in.
//   2. ``toSchematic(laidOut)`` → a flat, absolute-coordinate draw
//      model: boxes, pins, wires, junction dots, off-page flags. The
//      Vue component maps it to SVG elements one-for-one and stamps
//      ``data-node-id`` as it goes, so identity is *born* with the
//      element instead of being scraped back out of a ``<title>``.
//
// P3 adds two more pure steps on top of the same model, for the same
// reason: {@link highlightFor} (which wires and pins a hover lights
// up) and {@link sheetFrame} / {@link titleBlockRows} (the drawing
// border). Both are decisions, and a decision that lives in a
// template cannot be pinned by a test.
//
// No colour decisions here. Every stroke/fill is a CSS class the
// component's scoped style resolves against the token sheet
// (docs/design-tokens.md) — this module emits geometry and semantics
// (``kind: 'clock'``), never a hex literal.

// --- text metrics -----------------------------------------------------------

/** Font stack + sizes the canvas renders with. Measurement must use
 *  the SAME strings or every reserved box is a guess. */
export const PIN_FONT_SIZE = 9
export const TYPE_FONT_SIZE = 10.5
export const REFDES_FONT_SIZE = 11
export const PARAM_FONT_SIZE = 9
export const EDGE_LABEL_FONT_SIZE = 8.5

/**
 * A text measurer backed by a real canvas 2D context.
 *
 * ``document`` is absent in a bare-Node unit test and the 2D context
 * is absent in some headless DOM shims, so the factory returns null
 * rather than throwing; callers fall back to {@link approxMeasure}.
 */
export function makeCanvasMeasurer(fontFamily) {
  if (typeof document === 'undefined' || !document.createElement) return null
  let ctx
  try {
    ctx = document.createElement('canvas').getContext('2d')
  } catch {
    return null
  }
  if (!ctx || typeof ctx.measureText !== 'function') return null
  const cache = new Map()
  const measure = (text, size) => {
    const key = `${size}\u0000${text}`
    const hit = cache.get(key)
    if (hit !== undefined) return hit
    ctx.font = `${size}px ${fontFamily}`
    const w = ctx.measureText(String(text)).width
    // A context that exists but measures nothing (jsdom) is worse than
    // no context at all — it silently collapses every box to zero.
    const out = w > 0 ? w : approxMeasure(text, size)
    cache.set(key, out)
    return out
  }
  // Probe once: happy-dom/jsdom hand back a stub whose measureText
  // always returns 0. Detect it here, not at every call site.
  if (measure('MMMMMMMM', 10) <= 0) return null
  return measure
}

/** Monospace fallback: 0.6em per character is the classic ratio. */
export function approxMeasure(text, size) {
  return String(text).length * size * 0.6
}

/** Whichever measurer is available, wrapped so callers see one shape. */
export function resolveMeasure(fontFamily) {
  return makeCanvasMeasurer(fontFamily) || approxMeasure
}

// --- geometry constants -----------------------------------------------------

/** Vertical pitch between adjacent pins on one side of a block. */
export const PIN_PITCH = 18
/** Length of the stub that crosses the block border. */
export const PIN_STUB = 9
/** Gap between the border and the start of the pin's formal name. */
export const PIN_LABEL_GAP = 5
/** Smallest block a schematic should ever draw. */
export const MIN_NODE_WIDTH = 120
export const MIN_NODE_HEIGHT = 46
/** Height reserved above/below a block for refdes and type+params. */
export const REFDES_BAND = 16
export const TYPE_LINE_HEIGHT = 12
export const PARAM_LINE_HEIGHT = 11
/** Off-page connector flag. */
export const FLAG_HEIGHT = 22
export const FLAG_TIP = 9
/** Sheet frame: gap between the laid-out extent and the border. */
export const SHEET_MARGIN = 18
/** Title block, bottom-right inside the frame. */
export const TITLE_BLOCK_WIDTH = 290
export const TITLE_ROW_HEIGHT = 15
export const TITLE_BLOCK_PAD = 7

/**
 * elkjs layout options for the synthetic root.
 *
 * ``INCLUDE_CHILDREN`` is what makes a compound block a real
 * container rather than a second, disconnected drawing: edges may
 * cross a hierarchy boundary and still be routed once, globally.
 * ``mergeEdges`` is what produces ``junctionPoints`` — one trunk per
 * net with a dot at each branch, instead of N parallel wires.
 */
export const ROOT_LAYOUT_OPTIONS = Object.freeze({
  'elk.algorithm': 'layered',
  'elk.direction': 'RIGHT',
  'elk.edgeRouting': 'ORTHOGONAL',
  'elk.hierarchyHandling': 'INCLUDE_CHILDREN',
  'elk.layered.mergeEdges': 'true',
  'elk.layered.spacing.nodeNodeBetweenLayers': '70',
  'elk.layered.spacing.edgeNodeBetweenLayers': '20',
  'elk.spacing.nodeNode': '38',
  'elk.spacing.edgeNode': '16',
  'elk.spacing.edgeEdge': '14',
  'elk.spacing.portPort': '12',
  'elk.spacing.edgeLabel': '3',
  'elk.spacing.labelNode': '6',
  'elk.padding': '[top=28,left=28,bottom=28,right=28]',
})

/**
 * Port constraint applied to every block.
 *
 * ``FIXED_SIDE`` is the floor the schematic needs — an input must
 * land on the WEST border or the drawing stops being a schematic.
 * We ask for ``FIXED_ORDER`` because the *order within* a side is
 * also a decision we make (see {@link orderPorts}): data pins first,
 * clock and reset last, so the housekeeping pins sit together at the
 * bottom of the block the way a datasheet prints them. Under
 * ``FIXED_SIDE`` alone the layered algorithm is free to permute
 * within the side to shave crossings, and that ordering is exactly
 * what it would spend.
 */
export const PORT_CONSTRAINTS = 'FIXED_ORDER'

/** ``elk.nodeSize.constraints`` — grow a block to fit its pinout. */
const NODE_SIZE_CONSTRAINTS = '[PORTS, PORT_LABELS, MINIMUM_SIZE]'

// --- payload → elkjs input ---------------------------------------------------

/**
 * Which border a pin lands on.
 *
 * ``rb.direction`` is the fact the exporter carries; the side is the
 * consequence we draw. A null direction (interface bundle, blackbox
 * pin) has no declared answer, so fall back to how the net is used
 * in this scope: something the scope *sources* enters from the west.
 */
export function portSideFor(direction, { drives = false, sinks = false } = {}) {
  if (direction === 'input') return 'WEST'
  if (direction === 'output' || direction === 'inout') return 'EAST'
  if (drives && !sinks) return 'WEST'
  if (sinks && !drives) return 'EAST'
  return 'WEST'
}

/** True for a reset whose name says it asserts low (bubble on the pin). */
export function isActiveLow(name) {
  return /(^|_)n_?rst|_n$|_ni$|_b$|_l$/i.test(String(name || ''))
}

/**
 * Data pins first, then clock, then reset — stable within each group.
 *
 * Two reasons, both about reading speed: the signal path is what the
 * eye follows and it should not be interrupted by a clk pin halfway
 * down the block, and clock/reset nets are deliberately NOT routed
 * (elk.json §4), so their stubs are stubs — parking them together at
 * the bottom keeps the unrouted pins from looking like broken wires.
 */
export function orderPorts(ports) {
  const rank = (p) => {
    const rb = p.rb || {}
    if (rb.is_clock) return 1
    if (rb.is_reset) return 2
    return 0
  }
  return ports
    .map((p, i) => ({ p, i }))
    .sort((a, b) => rank(a.p) - rank(b.p) || a.i - b.i)
    .map((e) => e.p)
}

/**
 * Stamp ``elk.port.index`` so {@link orderPorts}'s decision survives.
 *
 * ELK numbers a node's ports **clockwise from the top-left corner**,
 * so a WEST side runs bottom-to-top: handing it our list unchanged
 * puts the clock and reset pins — which we deliberately sorted last —
 * at the *top* of the left border, above the signal path. Number the
 * two sides explicitly instead: EAST top-to-bottom from 0, then WEST
 * continuing round the bottom, so both sides read top-down in the
 * order {@link orderPorts} chose.
 *
 * Mutates each port's ``layoutOptions`` and returns the two sides.
 */
export function assignPortIndices(ports) {
  const east = ports.filter((p) => p.side === 'EAST')
  const west = ports.filter((p) => p.side === 'WEST')
  east.forEach((p, i) => {
    p.layoutOptions['elk.port.index'] = String(i)
  })
  west.forEach((p, i) => {
    p.layoutOptions['elk.port.index'] = String(east.length + (west.length - 1 - i))
  })
  return { west, east }
}

/** ``[[name, value], …]`` → the compact lines drawn under the type. */
export function paramLines(paramOverrides) {
  if (!Array.isArray(paramOverrides)) return []
  return paramOverrides.map(([name, value]) =>
    name ? `${name} ${value}` : String(value),
  )
}

/**
 * The ``/N`` annotation a bus wire carries, or null for a hairline.
 *
 * **The expression wins.** ``rb.bits_expr`` is the width in the names
 * the RTL declares (``/WIDTH``, ``/PTR_W+1``); ``rb.bits`` is what one
 * instantiation folded it to. A reader tracing a bus wants to know
 * which knob sets it — ``/WIDTH`` says that and ``/19`` does not, and
 * the parameterised case is exactly where the number may not exist at
 * all. Compactness is not a worry: the producer already abstains past
 * one symbolic term or 20 characters (elk.json §6.2).
 *
 * One exception, and it is about honesty rather than preference: a
 * bundle the producer resolved to **exactly 1 bit** is drawn as a
 * hairline with no slash, expression or not. A slash means "bus", and
 * we know this one is a single wire.
 */
export function busSlash(bits, bitsExpr = null) {
  if (bits === 1) return null
  const expr = typeof bitsExpr === 'string' ? bitsExpr.trim() : ''
  if (expr) return `/${expr}`
  return typeof bits === 'number' && bits > 1 ? `/${bits}` : null
}

/** True when the drawn slash is a name rather than a count. */
export function isSymbolicSlash(bits, bitsExpr) {
  const slash = busSlash(bits, bitsExpr)
  if (slash === null) return false
  const expr = typeof bitsExpr === 'string' ? bitsExpr.trim() : ''
  return expr !== ''
}

/** The author's bundle name when the edge has one (#180 — one edge
 *  labeled ``cmd_bus`` instead of an N-net list, matching the dot
 *  figure); otherwise the first net name plus a ``+N`` when the
 *  bundle carries more.
 *
 *  ``pins`` is every formal the edge attaches to at either end. A
 *  single-net wire whose net is already the name of a pin it lands
 *  on gets **no** label: the name is printed on that pin, the label
 *  would repeat it right on top of it, and a schematic names a net
 *  once. (In the subsys demo this is the whole
 *  ``result_y``/``result_zf``/… column — five labels stacked over
 *  the five identically-named pins they land on.) A named bundle is
 *  never suppressed: the bundle name is the one thing no pin says.
 */
export function edgeLabelText(nets, bundle = null, pins = null) {
  if (typeof bundle === 'string' && bundle !== '') return bundle
  if (!Array.isArray(nets) || nets.length === 0) return null
  if (nets.length === 1) {
    if (Array.isArray(pins) && pins.includes(nets[0])) return null
    return nets[0]
  }
  return `${nets[0]} +${nets.length - 1}`
}

/**
 * The net reference worth printing beside a pin, or ``null``.
 *
 * Only when it says something the formal doesn't: an implicit
 * ``.clk`` binding and an explicit ``.hwif_in(hwif_in)`` both name
 * the net after the pin, so printing it again is noise in the
 * routing channel — the place a schematic can least afford it.
 */
export function pinNetRef(net, formal) {
  if (typeof net !== 'string' || net === '') return null
  return net === formal ? null : net
}


/** Collect which of a scope's port ids are sourced / sunk by its edges. */
function endpointRoles(node) {
  const drives = new Set()
  const sinks = new Set()
  for (const e of node.edges || []) {
    for (const s of e.sources || []) drives.add(s)
    for (const t of e.targets || []) sinks.add(t)
  }
  return { drives, sinks }
}

function decorateNode(node, ctx, depth) {
  const rb = node.rb || {}
  const measure = ctx.measure
  // A scope's own ports are referenced by ITS edges (a pin the scope
  // drives inward is a `sources` entry), while a child's ports are
  // referenced by the PARENT's edges. Look in both so a null
  // direction has evidence wherever it lives.
  const own = endpointRoles(node)
  const ports = orderPorts(node.ports || []).map((port) => {
    const prb = port.rb || {}
    const side = portSideFor(prb.direction, {
      drives: own.drives.has(port.id) || ctx.parentSinks.has(port.id),
      sinks: own.sinks.has(port.id) || ctx.parentDrives.has(port.id),
    })
    const label = prb.name || ''
    const labelWidth = measure(label, PIN_FONT_SIZE) + PIN_LABEL_GAP * 2
    return {
      id: port.id,
      rb: prb,
      width: 1,
      height: 1,
      side,
      // Root pins are off-page flags, drawn as pentagons on the sheet
      // border; they carry their name in the flag, not as a port label.
      labels: depth === 0 ? [] : [{ text: label, width: labelWidth, height: 11 }],
      layoutOptions: { 'elk.port.side': side },
    }
  })

  const { west, east } = assignPortIndices(ports)
  const westW = west.reduce((m, p) => Math.max(m, p.labels[0]?.width || 0), 0)
  const eastW = east.reduce((m, p) => Math.max(m, p.labels[0]?.width || 0), 0)

  const refdes = rb.display_label || rb.instance_name || rb.module_name || node.id
  const typeText = rb.module_name || ''
  const params = paramLines(rb.param_overrides)

  const typeWidth = measure(typeText, TYPE_FONT_SIZE) + 20
  const refdesWidth = measure(refdes, REFDES_FONT_SIZE) + 8
  const paramWidth = params.reduce(
    (m, line) => Math.max(m, measure(line, PARAM_FONT_SIZE) + 8),
    0,
  )
  const minWidth = Math.max(
    MIN_NODE_WIDTH,
    westW + eastW + 24,
    typeWidth,
    refdesWidth,
    paramWidth,
  )
  const minHeight = Math.max(
    MIN_NODE_HEIGHT,
    Math.max(west.length, east.length) * PIN_PITCH + 16,
  )

  const children = (node.children || []).map((child) =>
    decorateNode(child, { ...ctx, parentDrives: own.drives, parentSinks: own.sinks }, depth + 1),
  )

  const out = {
    id: node.id,
    rb,
    // Carried through so the draw model can tell a leaf apart from a
    // compound block that is merely folded shut — they are the same
    // shape on the sheet but not the same affordance.
    rbCollapsed: !!node[COLLAPSED_MARK],
    ports,
    children,
    edges: (node.edges || []).map((edge) => decorateEdge(edge, ctx)),
    layoutOptions: {
      'elk.portConstraints': PORT_CONSTRAINTS,
      'elk.portLabels.placement': '[INSIDE]',
      'elk.nodeSize.constraints': NODE_SIZE_CONSTRAINTS,
      'elk.nodeSize.minimum': `(${round(minWidth)},${round(minHeight)})`,
      'elk.nodeLabels.placement': '[H_LEFT, V_TOP, OUTSIDE]',
    },
    // Boxes we measured but ELK is free to grow; the draw model reads
    // whatever comes back, never these.
    width: round(minWidth),
    height: round(minHeight),
  }

  if (children.length > 0) {
    // A compound block is a containment frame: pad it so children
    // never touch the border and the refdes band stays legible.
    out.layoutOptions['elk.padding'] = '[top=30,left=26,bottom=30,right=26]'
  }
  if (depth === 0) {
    // The sheet's own pins become off-page flags, which are far taller
    // than the 1px port they hang off. Space them for the flag, not
    // for the port, or a five-port design stacks five pentagons in
    // 20px. Distribute them over the sheet edge while we are at it —
    // the default packs every port at the height its first edge
    // happens to leave from.
    out.layoutOptions['elk.spacing.portPort'] = String(FLAG_HEIGHT + 12)
    out.layoutOptions['elk.portAlignment.default'] = 'DISTRIBUTED'
  }

  // Refdes above, type + params below — as ELK labels, so the layered
  // algorithm folds them into the node's margins and a neighbour
  // cannot be packed into the space the text occupies. (Drawing them
  // as bare SVG afterwards is what makes a mockup look crowded.)
  if (depth > 0) {
    const bottomLines = [typeText, ...params].filter(Boolean)
    out.labels = [
      {
        id: `${node.id}#refdes`,
        text: refdes,
        rbRole: 'refdes',
        width: round(measure(refdes, REFDES_FONT_SIZE)),
        height: REFDES_BAND,
        layoutOptions: { 'elk.nodeLabels.placement': '[H_LEFT, V_TOP, OUTSIDE]' },
      },
      {
        id: `${node.id}#type`,
        text: bottomLines.join('\n'),
        rbRole: 'type',
        rbLines: bottomLines,
        width: round(
          bottomLines.reduce(
            (m, line, i) =>
              Math.max(m, measure(line, i === 0 ? TYPE_FONT_SIZE : PARAM_FONT_SIZE)),
            0,
          ),
        ),
        height: TYPE_LINE_HEIGHT + Math.max(0, bottomLines.length - 1) * PARAM_LINE_HEIGHT,
        layoutOptions: { 'elk.nodeLabels.placement': '[H_CENTER, V_BOTTOM, OUTSIDE]' },
      },
    ]
  }
  return out
}

function decorateEdge(edge, ctx) {
  const rb = edge.rb || {}
  const text = edgeLabelText(rb.nets, rb.bundle, [
    ...(rb.src_pins || []),
    ...(rb.dst_pins || []),
  ])
  const out = {
    id: edge.id,
    sources: [...(edge.sources || [])],
    targets: [...(edge.targets || [])],
    rb,
  }
  if (text) {
    out.labels = [
      {
        id: `${edge.id}#net`,
        text,
        width: round(ctx.measure(text, EDGE_LABEL_FONT_SIZE)),
        height: 10,
      },
    ]
  }
  return out
}

function round(n) {
  return Math.round(n * 100) / 100
}

// --- collapse ----------------------------------------------------------------

/** Internal marker set by {@link collapsePayload}; read by decorateNode. */
const COLLAPSED_MARK = '$collapsed'

/** ``Set`` | iterable | null → ``Set``. */
function toSet(value) {
  if (value instanceof Set) return value
  if (!value) return new Set()
  return new Set(value)
}

/** The node id an endpoint belongs to (``path:pin`` → ``path``). */
export function nodeIdOfEndpoint(endpoint) {
  if (typeof endpoint !== 'string' || endpoint.length === 0) return null
  const i = endpoint.indexOf(':')
  return i === -1 ? endpoint : endpoint.slice(0, i)
}

/**
 * Fold every compound block in ``collapsed`` shut, purely.
 *
 * A collapsed block keeps its **own** ports — the exporter gives every
 * node its full declared pinout (elk.json §3), and the scope's edges
 * already terminate on those port ids rather than reaching through to
 * a grandchild (§4). So folding is: drop ``children``, drop the
 * scope-internal ``edges`` those children were wired by, and leave the
 * parent's edges untouched. They re-terminate on the collapsed box
 * because they were never terminated anywhere else.
 *
 * The endpoint rewrite below is the guard for the case the contract
 * does not promise: an edge naming a node *inside* a collapsed
 * subtree. elkjs throws on an unresolvable endpoint, so such an edge
 * is re-pointed at the box that swallowed it (border attachment, the
 * same degradation §4 already applies when a bundle has no single
 * pin), and an edge whose two ends land in the same box is dropped —
 * it is now internal to a block that isn't showing its internals.
 *
 * Returns ``payload`` itself when nothing collapses, so a re-layout
 * with an empty set allocates nothing.
 */
export function collapsePayload(payload, collapsed) {
  const set = toSet(collapsed)
  if (!payload || set.size === 0) return payload

  // id → the collapsed ancestor that now stands for it.
  const hidden = new Map()
  const hide = (node, root) => {
    for (const child of node.children || []) {
      hidden.set(child.id, root)
      hide(child, root)
    }
  }
  const scan = (node) => {
    if (set.has(node.id) && (node.children || []).length > 0) {
      hide(node, node.id)
      return // a nested collapse under a collapsed box is moot
    }
    for (const child of node.children || []) scan(child)
  }
  scan(payload)
  if (hidden.size === 0) return payload

  const rewrite = (endpoint) => {
    const owner = hidden.get(nodeIdOfEndpoint(endpoint))
    return owner === undefined ? endpoint : owner
  }

  const prune = (node) => {
    if (set.has(node.id) && (node.children || []).length > 0) {
      return { ...node, children: [], edges: [], [COLLAPSED_MARK]: true }
    }
    const edges = []
    for (const edge of node.edges || []) {
      const sources = (edge.sources || []).map(rewrite)
      const targets = (edge.targets || []).map(rewrite)
      if (sources.length > 0 && sources.every((s) => targets.includes(s))) continue
      edges.push({ ...edge, sources, targets })
    }
    return { ...node, children: (node.children || []).map(prune), edges }
  }
  return prune(payload)
}

// --- interface instances ------------------------------------------------------

/**
 * ``payload`` with every interface-instance node dropped (#205).
 *
 * An interface instance (``bus_if shared (.clk(clk));``) is the wire
 * between the blocks bound to it, not a block: the exporter keeps it
 * in the tree for its parameters (#165) and flags it
 * ``rb.is_interface`` (elk.json §2, §4.1), and a dataflow sheet skips
 * it — the same pruning the dot ``--block-diagram`` applies. Drawn, it
 * is an orphan box with no wire on it.
 *
 * The contract promises no edge references such a node or its ports
 * (since #194 the wire runs between the siblings). An edge that did
 * anyway is dropped with it rather than left dangling, because elkjs
 * throws on an endpoint it cannot resolve and one stray reference
 * would blank the whole sheet. The payload root is never dropped: it
 * is the sheet being drawn.
 *
 * Pure; returns ``payload`` itself when it holds no interface
 * instance, so the common case allocates nothing.
 */
export function withoutInterfaceInstances(payload) {
  if (!payload) return payload
  const dropped = new Set()
  const scan = (node) => {
    for (const child of node.children || []) {
      if (child.rb && child.rb.is_interface === true) dropped.add(child.id)
      else scan(child)
    }
  }
  scan(payload)
  if (dropped.size === 0) return payload

  const touches = (endpoint) => dropped.has(nodeIdOfEndpoint(endpoint))
  const prune = (node) => ({
    ...node,
    children: (node.children || []).filter((c) => !dropped.has(c.id)).map(prune),
    edges: (node.edges || []).filter(
      (e) => !(e.sources || []).some(touches) && !(e.targets || []).some(touches),
    ),
  })
  return prune(payload)
}

/**
 * Instance paths of every compound block in ``payload``, collapsed or
 * not — i.e. everything the canvas may offer a ▸/▾ toggle for.
 *
 * Read off the *uncollapsed* payload on purpose: a folded block has no
 * children left to prove it is compound, and the affordance that
 * unfolds it has to survive the fold.
 *
 * The payload root is excluded — it is the sheet, not a block, and a
 * sheet that folds into a box would leave nothing to draw. So is a
 * block whose only children are interface instances: they are never
 * drawn ({@link withoutInterfaceInstances}), so it lays out as a leaf
 * and a toggle on it would fold nothing.
 */
export function collapsibleIds(payload, out = new Set(), depth = 0) {
  if (!payload) return out
  const children = payload.children || []
  const drawn = children.some((c) => !(c.rb && c.rb.is_interface === true))
  if (depth > 0 && drawn && payload.id) out.add(payload.id)
  for (const child of children) collapsibleIds(child, out, depth + 1)
  return out
}

/**
 * Wrap ``payload`` in the synthetic container elkjs needs and attach
 * every presentation decision.
 *
 * The wrapper is mandatory and is not ours to skip: the payload's
 * root owns ports that its own edges reference, and elkjs's JSON
 * importer cannot resolve a port owned by the node handed to
 * ``layout()`` — it throws ``UnsupportedGraphException`` or
 * dereferences null. One level down it resolves cleanly. See
 * docs/elk-json-v1.md §5.6; the exporter deliberately ships the
 * payload without the wrapper because it is an elkjs quirk, not a
 * property of the graph.
 *
 * ``collapsed`` is the set of instance paths folded shut (see
 * {@link collapsePayload}). It is applied *before* decoration, so a
 * collapsed block is sized and pinned exactly like the leaf it now
 * is — there is no second code path for the folded case. Interface
 * instances are dropped first ({@link withoutInterfaceInstances}).
 */
export function buildElkGraph(payload, { collapsed = null, measure = approxMeasure } = {}) {
  if (!payload || typeof payload !== 'object' || !payload.id) return null
  const ctx = { measure, parentDrives: new Set(), parentSinks: new Set() }
  const drawn = collapsePayload(withoutInterfaceInstances(payload), collapsed)
  return {
    id: SCHEMATIC_ROOT_ID,
    layoutOptions: { ...ROOT_LAYOUT_OPTIONS },
    children: [decorateNode(drawn, ctx, 0)],
  }
}

export const SCHEMATIC_ROOT_ID = '$root'

/**
 * The sub-payload rooted at ``instancePath``, or null.
 *
 * Node ids are instance paths, so descending is a lookup in the tree
 * the exporter already nested — no re-derivation, no second request.
 * ``null`` for an unknown path (a scope from a different model), and
 * ``null`` for an empty ``instancePath`` so the caller falls back to
 * the whole design.
 */
export function subtreeOf(payload, instancePath) {
  if (!payload || !instancePath) return null
  if (payload.id === instancePath) return payload
  for (const child of payload.children || []) {
    const hit = subtreeOf(child, instancePath)
    if (hit) return hit
  }
  return null
}

// --- elkjs output → draw model ----------------------------------------------

/**
 * Flatten a laid-out graph into absolute-coordinate drawing
 * primitives.
 *
 * ELK reports child coordinates relative to their parent and edge
 * sections relative to the node whose ``edges`` array holds them, so
 * the walk carries an accumulated origin. Everything the component
 * draws — and everything a test asserts on — comes out of here.
 */
/**
 * Port ids an edge set actually touches, split by how.
 *
 * An edge endpoint is a **port id** when the bundle resolved to one
 * unambiguous pin; when it stands for several pins at once it
 * degrades to the *node* id and the wire lands on the box border,
 * with the pin names surviving only in ``rb.src_pins`` /
 * ``rb.dst_pins`` (contract §4). Both cases mean "this pin is on a
 * wire" — the difference is only where the wire attaches, so the
 * second set is exactly the set of **bus taps**: pins a drawn
 * bundle stands for without landing on them.
 *
 * Returns ``{ wired, tapped }``; ``tapped`` is a subset of
 * ``wired``.
 */
export function wiredPortIds(node, out = { wired: new Set(), tapped: new Set() }) {
  for (const edge of node.edges || []) {
    const rb = edge.rb || {}
    const ends = [
      [(edge.sources || [])[0], rb.src_pins],
      [(edge.targets || [])[0], rb.dst_pins],
    ]
    for (const [ref, formals] of ends) {
      if (!ref) continue
      if (ref.includes(':')) {
        // Landed on the pin itself.
        out.wired.add(ref)
        continue
      }
      // Landed on the box: every formal it stands for is a tap.
      for (const formal of formals || []) {
        const id = `${ref}:${formal}`
        out.wired.add(id)
        out.tapped.add(id)
      }
    }
  }
  for (const child of node.children || []) wiredPortIds(child, out)
  return out
}


export function toSchematic(laidOut) {
  const boxes = []
  const pins = []
  // Filled from the laid-out tree before the walk: which pins a wire
  // actually reaches, and which of those are bus taps.
  let touched = { wired: new Set(), tapped: new Set() }
  const flags = []
  const wires = []
  const junctions = []
  const labels = []

  function walk(node, ox, oy, depth) {
    const x = ox + (node.x || 0)
    const y = oy + (node.y || 0)
    const w = node.width || 0
    const h = node.height || 0
    const rb = node.rb || {}
    const compound = (node.children || []).length > 0

    if (depth > 0) {
      boxes.push({
        id: node.id,
        x,
        y,
        width: w,
        height: h,
        compound,
        // A folded compound draws as a leaf but is not one: it keeps
        // the ▾/▸ affordance and the double-click that unfolds it.
        collapsed: !!node.rbCollapsed,
        collapsible: compound || !!node.rbCollapsed,
        blackbox: !!rb.is_blackbox,
        moduleName: rb.module_name || '',
        instanceName: rb.instance_name || null,
        clock: rb.clock || null,
      })
    } else {
      // The design top is the sheet: a frame, not a block.
      boxes.push({
        id: node.id,
        x,
        y,
        width: w,
        height: h,
        compound: true,
        collapsed: false,
        collapsible: false,
        sheet: true,
        blackbox: false,
        moduleName: rb.module_name || '',
        instanceName: null,
        clock: null,
      })
    }

    for (const label of node.labels || []) {
      if (label.x === undefined) continue
      labels.push({
        nodeId: node.id,
        role: label.rbRole || 'label',
        lines: label.rbLines || [label.text],
        x: x + label.x,
        y: y + label.y,
        width: label.width || 0,
        height: label.height || 0,
      })
    }

    for (const port of node.ports || []) {
      const px = x + (port.x || 0) + (port.width || 0) / 2
      const py = y + (port.y || 0) + (port.height || 0) / 2
      const prb = port.rb || {}
      const side = port.side || (port.x > w / 2 ? 'EAST' : 'WEST')
      if (depth === 0) {
        flags.push({
          id: port.id,
          nodeId: node.id,
          name: prb.name || '',
          x: px,
          y: py,
          side,
          out: side === 'EAST',
          isClock: !!prb.is_clock,
          isReset: !!prb.is_reset,
          activeLow: !!prb.is_reset && isActiveLow(prb.name),
        })
        continue
      }
      const wired = touched.wired.has(port.id)
      pins.push({
        id: port.id,
        nodeId: node.id,
        name: prb.name || '',
        x: px,
        y: py,
        side,
        kind: prb.is_clock ? 'clock' : prb.is_reset ? 'reset' : 'signal',
        activeLow: !!prb.is_reset && isActiveLow(prb.name),
        connected: prb.connected !== false,
        // A compound's own port labels are drawn in its interior —
        // which is the wiring channel — so they need the halo a leaf
        // block's labels don't (a leaf's interior is a filled rect
        // no wire crosses).
        onFrame: compound,
        // A bound pin with no wire reaching it is the normal case
        // for a net the parent's own procedural logic drives — the
        // dataflow analyzer only hops continuous assigns, by design.
        // Carrying the net name keeps such a pin traceable instead
        // of leaving a bare stub the reader can't follow.
        wired,
        // Never the formal name over again: a pin bound to a
        // same-named net (``.hwif_in(hwif_in)``, ``.src_ready``
        // shorthand) already prints that name inside the block, and
        // repeating it outside is the same duplication the wire
        // labels were carrying.
        net: pinNetRef(prb.net, prb.name),
        // A pin a drawn bundle stands for, without the wire landing
        // on it: the schematic bus-tap case, marked with a junction
        // dot the way any other wire join is.
        busTap: touched.tapped.has(port.id),
      })
    }

    for (const edge of node.edges || []) {
      const rb = edge.rb || {}
      const bits = typeof rb.bits === 'number' ? rb.bits : null
      const bitsExpr = typeof rb.bits_expr === 'string' ? rb.bits_expr : null
      const slash = busSlash(bits, bitsExpr)
      const bus = slash !== null
      const slashSymbolic = isSymbolicSlash(bits, bitsExpr)
      for (const section of edge.sections || []) {
        const pts = [
          section.startPoint,
          ...(section.bendPoints || []),
          section.endPoint,
        ].filter(Boolean)
        if (pts.length < 2) continue
        // A section ELK could not route (e.g. an unroutable edge
        // shape a producer slipped through) carries null/NaN
        // coordinates; offsetting garbage draws a stray line
        // escaping the sheet. Drop the whole section — a partial
        // wire is worse than none.
        if (!pts.every((p) => Number.isFinite(p.x) && Number.isFinite(p.y)))
          continue
        wires.push({
          id: edge.id,
          sourceId: (edge.sources || [])[0] || null,
          targetId: (edge.targets || [])[0] || null,
          points: pts.map((p) => ({ x: x + p.x, y: y + p.y })),
          bus,
          slash,
          slashSymbolic,
          bits,
          bitsExpr,
          nets: Array.isArray(rb.nets) ? rb.nets : [],
          // ``rbsch`` presentation (#180): semantics only — the
          // component's scoped style decides what "main" looks
          // like, per this module's no-colour rule.
          emphasis: rb.emphasis === 'main' || rb.emphasis === 'side' ? rb.emphasis : null,
          bundle: typeof rb.bundle === 'string' && rb.bundle !== '' ? rb.bundle : null,
        })
      }
      for (const jp of edge.junctionPoints || []) {
        junctions.push({ edgeId: edge.id, x: x + jp.x, y: y + jp.y })
      }
      for (const label of edge.labels || []) {
        if (label.x === undefined) continue
        labels.push({
          nodeId: null,
          edgeId: edge.id,
          role: 'net',
          lines: [label.text],
          x: x + label.x,
          y: y + label.y,
          width: label.width || 0,
          height: label.height || 0,
        })
      }
    }

    for (const child of node.children || []) walk(child, x, y, depth + 1)
  }

  // The synthetic wrapper is not part of the drawing: start the walk
  // at its single child, the design top.
  const design = (laidOut && laidOut.children && laidOut.children[0]) || null
  if (design) {
    touched = wiredPortIds(design)
    walk(design, laidOut.x || 0, laidOut.y || 0, 0)
  }

  return {
    width: Math.ceil((laidOut && laidOut.width) || 0),
    height: Math.ceil((laidOut && laidOut.height) || 0),
    boxes,
    pins,
    flags,
    wires,
    junctions,
    labels,
  }
}

/** Pentagon path for an off-page connector flag.
 *
 *  The point shows *signal direction*, per off-page-connector
 *  convention — and on an LR sheet every flag flows rightward: an
 *  input points right INTO the sheet (its tip touching the wire it
 *  drives), an output points right OFF it. One shape; which border
 *  the flag hangs on is the caller's positioning decision. The old
 *  form mirrored input flags leftward, which read as flow *out of*
 *  the left border — backwards.
 */
export function flagPath(x, y, width, height) {
  const h = height
  const tip = Math.min(FLAG_TIP, width / 2)
  return `M${x},${y} h${width - tip} l${tip},${h / 2} l${-tip},${h / 2} h${-(width - tip)} z`
}

/** ``points`` → an SVG path string with square corners. */
export function polylinePath(points) {
  return points.map((p, i) => `${i ? 'L' : 'M'}${round(p.x)},${round(p.y)}`).join(' ')
}

// --- hover highlighting -------------------------------------------------------

/** The empty answer, shared so a null hover allocates nothing. */
export const NO_HIGHLIGHT = Object.freeze({
  edges: Object.freeze(new Set()),
  pins: Object.freeze(new Set()),
})

/**
 * Which wires and pins light up for a hover.
 *
 * Two entry points, one answer shape:
 *
 * - ``edgeId`` — the reader is pointing at a wire and means *the net*.
 *   A net is not one path: ELK splits a route into sections (each a
 *   separate wire in the draw model, all carrying the same edge id)
 *   and ``mergeEdges`` folds a fan-out into one trunk with junction
 *   dots, so the siblings leaving the same source pin are visually
 *   the same copper. Both are pulled in.
 * - ``pinId`` — the reader is pointing at a pin and means *everything
 *   attached to it*.
 *
 * Endpoints degrade to a node id when a bundle has no single pin
 * (elk.json §4); those simply match no pin and drop out.
 */
export function highlightFor(model, { edgeId = null, pinId = null } = {}) {
  if (!model || (!edgeId && !pinId)) return NO_HIGHLIGHT
  const wires = model.wires || []
  const edges = new Set()
  const pins = new Set()
  if (edgeId) {
    const sources = new Set()
    for (const w of wires) {
      if (w.id !== edgeId) continue
      edges.add(w.id)
      if (w.sourceId) sources.add(w.sourceId)
    }
    if (sources.size > 0) {
      for (const w of wires) {
        if (w.sourceId && sources.has(w.sourceId)) edges.add(w.id)
      }
    }
  }
  if (pinId) {
    pins.add(pinId)
    for (const w of wires) {
      if (w.sourceId === pinId || w.targetId === pinId) edges.add(w.id)
    }
  }
  for (const w of wires) {
    if (!edges.has(w.id)) continue
    if (w.sourceId) pins.add(w.sourceId)
    if (w.targetId) pins.add(w.targetId)
  }
  return { edges, pins }
}

// --- sheet frame + title block ------------------------------------------------

/**
 * The rows a title block prints, top to bottom.
 *
 * **No date, no time, no sheet-of-N counter.** The no-volatile rule
 * the payload lives under (graph.json / elk.json: nothing that changes
 * between two runs over the same sources) extends to what we draw,
 * because the drawing is an export artefact: a timestamp would make
 * two exports of one design differ byte-for-byte and turn every
 * regenerated figure into a diff.
 */
export function titleBlockRows({ top, toolVersion, scope = null, model = null } = {}) {
  const rows = [{ label: 'DESIGN', value: top || '(unnamed)' }]
  if (scope && scope !== top) rows.push({ label: 'SHEET', value: scope })
  if (model) rows.push({ label: 'MODEL', value: model })
  rows.push({ label: 'TOOL', value: `rtl-buddy-sch ${toolVersion || 'unknown'}` })
  return rows
}

/**
 * Border around the laid-out extent, plus the bottom-right title block.
 *
 * Geometry only — the component draws it, ``schExport`` uses the frame
 * box as the exported SVG's ``viewBox`` (which is why it has to be a
 * value and not a CSS decoration: a border painted by the stylesheet
 * would not be in the file).
 *
 * The frame reserves a band *below* the drawing for the title block
 * rather than overlaying the corner, so a block that ELK happened to
 * place bottom-right is never printed over.
 */
export function sheetFrame(model, rows = []) {
  const w = Math.max(0, (model && model.width) || 0)
  const h = Math.max(0, (model && model.height) || 0)
  const titleHeight = rows.length * TITLE_ROW_HEIGHT + TITLE_BLOCK_PAD * 2
  const frame = {
    x: -SHEET_MARGIN,
    y: -SHEET_MARGIN,
    width: w + SHEET_MARGIN * 2,
    height: h + SHEET_MARGIN * 2 + titleHeight,
  }
  const width = Math.min(TITLE_BLOCK_WIDTH, Math.max(0, frame.width - TITLE_BLOCK_PAD * 2))
  const title = {
    x: frame.x + frame.width - width - TITLE_BLOCK_PAD,
    y: frame.y + frame.height - titleHeight - TITLE_BLOCK_PAD,
    width,
    height: titleHeight,
    rows,
  }
  return { frame, title }
}

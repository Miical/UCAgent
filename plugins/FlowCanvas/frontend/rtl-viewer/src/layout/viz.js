// viz.js (Graphviz WASM) layout wrapper.
//
// Takes a parsed view.json graph, converts it to DOT, runs viz.js,
// and returns an SVG string the GraphCanvas component renders into
// the DOM. We deliberately don't try to parse the SVG into Vue's
// virtual DOM — viz.js produces production-quality SVG already and
// re-wrapping every node/edge in Vue templates would lose that. The
// viewer hangs DOM listeners off the SVG via data attributes after
// it's mounted (see GraphCanvas.vue).
//
// viz.js is loaded lazily on first use so the initial bundle (the
// Vue + Pinia shell) stays small enough to drag-drop a 300 KB
// view.json into the browser before the WASM is fetched.

import { buildClockPalette, isUnconstrained } from '../palette.js'
import { token } from '../theme.js'

let _vizPromise = null

// Typeface for every DOT the SPA builds itself. The producer's
// embedded DOT already sets this; ``graphToDot`` used to set nothing,
// so viz.js fell back to its built-in Times serif — and because a
// descended / scoped view ALWAYS goes through ``graphToDot``
// (``displayGraph`` drops the embedded layout), drilling into a block
// silently changed the schematic's typeface mid-session. Mono is also
// the type rule for data (ids, instance paths, signal names).
export const DOT_FONT = 'Courier,monospace'

// Node text inset, in inches — ``x,y``. Graphviz's default (0.11,0.055)
// leaves barely a character of horizontal padding, so a long instance
// path (``demo_tiny_alu_subsys_csr``) visibly touches, and at some
// font metrics overruns, its box. 0.4 horizontal matches the producer
// DOT's node margin exactly, so the two renderers size boxes the same.
export const NODE_MARGIN = '0.4,0.06'

async function getViz() {
  if (_vizPromise == null) {
    // ``instance()`` returns a single shared Viz instance per
    // page lifetime — viz.js is internally thread-safe and the
    // WASM module's setup cost (~150 ms) is the dominant
    // first-render expense, so we cache aggressively.
    const { instance } = await import('@viz-js/viz')
    _vizPromise = instance()
  }
  return _vizPromise
}

/**
 * Layout ``graph`` (a view.json v1 payload) and return an SVG
 * string. Throws on layout errors; the store catches and surfaces
 * via the toast pipeline.
 *
 * When ``graph.layout.dot`` is a non-empty string (set by Python
 * --format json --embed-layout, default on), the renderer hands it
 * directly to viz.js — the desktop terminal output and the browser
 * schematic then share a single layout (clusters, port-rank
 * anchors, edge labels, clock-domain palette). Producers that omit
 * the field fall back to the in-JS ``graphToDot`` builder, which
 * keeps drag-drop view.json files (no Python producer in the loop)
 * working without a behaviour change.
 */
export async function layoutGraph(graph) {
  const viz = await getViz()
  const dot = pickDot(graph)
  // ``viz.renderSVGElement`` runs the full layout pipeline (dot
  // engine, default) and returns a DOM-ready ``<svg>`` element.
  // The string form is easier to test against snapshots so we
  // serialise here.
  const svg = viz.renderSVGElement(dot)
  return svg.outerHTML
}

/**
 * Lay out an arbitrary DOT string and return the SVG. Lets
 * alternate renderers (block-flow, future formats) share the same
 * viz.js bootstrap path without going through view.json.
 */
export async function layoutDot(dot) {
  const viz = await getViz()
  const svg = viz.renderSVGElement(dot)
  return svg.outerHTML
}

// Pick the DOT source to feed viz.js: prefer the producer-supplied
// ``layout.dot`` when it looks usable; fall back to the in-JS
// builder otherwise. Exported for tests so the precedence rule is
// pinnable without standing up the WASM engine.
export function pickDot(graph) {
  if (hasEmbeddedDot(graph)) return graph.layout.dot
  return graphToDot(graph)
}

/**
 * True when ``pickDot`` will use the producer's embedded DOT rather
 * than the in-JS builder.
 *
 * GraphCanvas needs the distinction for more than layout: the
 * producer's DOT has the Python renderer's light palette baked in and
 * cannot be rebuilt here, so it needs the canvas's ``:deep`` re-tint
 * rules; ``graphToDot`` bakes theme-resolved tokens (including the CDC
 * red on crossing edges, labels and the unconstrained-source marker),
 * and the same blanket re-tint would throw those away.
 */
export function hasEmbeddedDot(graph) {
  const embedded = graph?.layout?.dot
  return typeof embedded === 'string' && embedded.trim().length > 0
}

/**
 * Generate a DOT description of ``graph`` suitable for
 * dot/Graphviz layout. We emit the topology only (nodes + edges) —
 * overlay styling is applied by the GraphCanvas component as CSS
 * classes after layout, so toggling an overlay never re-runs
 * viz.js. (Layout is the expensive step; restyling is free.)
 *
 * The root node (``graph.top``) becomes an outer ``cluster_top``
 * subgraph so the descend / subtree view gets a labelled frame
 * around it (matches the producer-supplied embedded DOT's
 * convention). Without this, the descend canvas was a flat list
 * of children with no scope title.
 */
export function graphToDot(graph) {
  // Colours are resolved from design tokens HERE, at DOT-build time,
  // because Graphviz bakes them into the SVG it emits — there is no
  // ``var(--…)`` to inherit later. GraphCanvas re-runs layout when the
  // theme flips, which is what makes the baked values honest.
  const nodeFill = token('--panel-2')
  const nodeText = token('--fg')
  const frame = token('--fg-faint')
  // Edges are a separate tier from the node/cluster frames. ``--fg-faint``
  // is a hairline colour that works for a box outline sitting on a filled
  // body, but a 1px unfilled polyline in the same value all but vanishes
  // against the canvas. Connectivity is the point of the diagram, so edges
  // (and their arrowheads, which inherit ``color``) get the readable
  // ``--fg-muted`` tier instead.
  const edgeLine = token('--fg-muted')
  const cdc = token('--err')
  // Recognised-safe crossings (rtl-buddy-sch#211) — the synchroniser
  // teal the reset overlay already uses, so "vetted synchroniser"
  // reads the same on both overlays.
  const sync = token('--reset-sync')
  const lines = []
  lines.push('digraph view {')
  lines.push('  rankdir="LR";')
  lines.push('  compound=true;')
  lines.push(
    `  node [shape=box, style="rounded,filled", fillcolor="${nodeFill}", ` +
      `color="${frame}", fontcolor="${nodeText}", fontname="${DOT_FONT}", ` +
      `margin="${NODE_MARGIN}"];`,
  )
  lines.push(
    `  edge [arrowsize=0.7, color="${edgeLine}", fontcolor="${nodeText}", ` +
      `fontname="${DOT_FONT}"];`,
  )
  // Graph-scope ``fontname`` covers the root graph label AND is what
  // subgraphs inherit — but each cluster re-states it below anyway, so
  // the attribute survives anyone reordering these defaults later.
  lines.push(
    `  bgcolor="transparent"; fontcolor="${nodeText}"; fontname="${DOT_FONT}";`,
  )

  const rootId = graph.top
  const rootNode = graph.nodes.find((n) => n.id === rootId)
  const rootLabel = rootNode ? scopeLabel(rootNode) : (rootId || '')

  // Pre-compute clock palette, per-node CDC pair list, and the
  // parent→child tree (derived from dotted instance paths — every
  // node's parent is the longest prefix that's also a node in this
  // graph).
  const palette = buildClockPalette(graph)
  const pairsByNode = collectCdcPairsByNode(graph)
  const childrenById = buildChildrenMap(graph, rootId)

  // Virtual stand-in for crossings with ``<unconstrained>`` source.
  const UNCONSTRAINED_ID = '_rb_unconstrained'
  const needUnconstrained = (graph.edges || []).some((e) =>
    (e?.overlays?.clock?.pairs || []).some(
      (p) => p && isUnconstrained(p.src_clock),
    ),
  )

  // --- nodes (nested clusters) -------------------------------------
  // Top frame is always cluster_top (the descend scope). What sits
  // *inside* depends on whether the root is a leaf, a CDC bridge,
  // or a non-CDC container — see emitSubtree's per-branch logic.
  if (rootNode) {
    lines.push('  subgraph cluster_top {')
    lines.push(`    label="${labelEscape(rootLabel)}";`)
    lines.push('    labelloc="t";')
    lines.push(`    fontcolor="${nodeText}";`)
    lines.push(`    fontname="${DOT_FONT}";`)
    lines.push('    style="rounded";')
    lines.push(`    color="${frame}";`)
    lines.push('    penwidth=2;')
    if (needUnconstrained) {
      lines.push(`    ${dotId(UNCONSTRAINED_ID)} [shape=plaintext, ` +
        `label="?", fontcolor="${cdc}", fontsize=14, ` +
        `tooltip="async crossing whose src_clock is unconstrained in SDC"];`)
    }
    emitInsideRootCluster(
      rootNode, '    ', graph, palette, pairsByNode, childrenById, lines,
      { frame, text: nodeText, neutral: nodeFill },
    )
    lines.push('  }')
  } else {
    if (needUnconstrained) {
      lines.push(`  ${dotId(UNCONSTRAINED_ID)} [shape=plaintext, ` +
        `label="?", fontcolor="${cdc}", fontsize=14, ` +
        `tooltip="async crossing whose src_clock is unconstrained in SDC"];`)
    }
    for (const node of graph.nodes) {
      lines.push('  ' + emitNode(node, palette, pairsByNode, nodeFill))
    }
  }

  // --- edges --------------------------------------------------------
  // Skip plain parent→child containment edges (their relationship is
  // already shown by cluster nesting). Only emit edges that carry
  // CDC overlay info. ``isContainmentEdge`` checks via the prefix
  // rule used in ``buildChildrenMap``.
  for (const edge of graph.edges) {
    const allPairs = edge?.overlays?.clock?.pairs || []
    const realPairs = allPairs.filter(
      (p) => p && typeof p.src_clock === 'string' && !isUnconstrained(p.src_clock),
    )
    const unconPairs = allPairs.filter(
      (p) => p && typeof p.src_clock === 'string' && isUnconstrained(p.src_clock),
    )
    if (realPairs.length === 0 && unconPairs.length === 0) continue
    for (const p of realPairs) {
      const attrs = cdcPairAttrs(p, cdc, sync)
      const fromRef = portRefForPair(edge.from, p, 'outgoing', pairsByNode)
      const toRef = portRefForPair(edge.to, p, 'incoming', pairsByNode)
      lines.push(`  ${fromRef} -> ${toRef}${attrs};`)
    }
    for (const p of unconPairs) {
      const label = `${p.src_clock} → ${p.dst_clock}`
      const attrs = ` [label="${labelEscape(label)}", color="${cdc}", style="dashed", fontcolor="${cdc}", fontsize=9]`
      const toRef = portRefForUnconstrainedDst(edge.to, p, pairsByNode)
      lines.push(`  ${dotId(UNCONSTRAINED_ID)} -> ${toRef}${attrs};`)
    }
  }
  lines.push('}')
  return lines.join('\n')
}

// Derive each node's direct children from dotted instance paths.
// A node B is a direct child of A iff B.id starts with ``A.id + "."``
// AND there is no other node C in the graph whose id is a longer
// prefix of B.id (i.e., C sits between A and B). Returns
// ``Map<parentId, [childId, ...]>`` covering every node in graph.
function buildChildrenMap(graph) {
  const children = new Map()
  const ids = new Set()
  for (const n of graph.nodes) {
    ids.add(n.id)
    children.set(n.id, [])
  }
  for (const n of graph.nodes) {
    let cur = n.id
    while (true) {
      const lastDot = cur.lastIndexOf('.')
      if (lastDot < 0) break
      cur = cur.substring(0, lastDot)
      if (ids.has(cur)) {
        children.get(cur).push(n.id)
        break
      }
    }
  }
  // Stable sort by instance path so the output is deterministic.
  for (const arr of children.values()) arr.sort()
  return children
}

// Render the contents of the root cluster_top. Three cases:
//   - root has no children → just emit the root as a flat node so
//     the scope frame has a visible anchor.
//   - root is a CDC bridge with children → emit root as a flat
//     HTML-TABLE grid + its children at the same level (matches
//     dot.py's CDC bridge convention — the children sit alongside
//     the bridge node, not nested inside it).
//   - root is a non-CDC container with children → recurse via
//     emitSubtree which uses nested ``subgraph cluster_<id>`` for
//     each non-leaf descendant.
function emitInsideRootCluster(
  rootNode, indent, graph, palette, pairsByNode, childrenById, lines, style,
) {
  const rootId = rootNode.id
  const rootChildren = childrenById.get(rootId) || []
  if (rootChildren.length === 0) {
    // Leaf scope — no children to recurse into, render the root as a
    // flat node so cluster_top has something inside it.
    lines.push(indent + emitNode(rootNode, palette, pairsByNode, style.neutral))
    return
  }
  // Container scope (CDC bridge or non-CDC). The scope's identity is
  // already conveyed by cluster_top's ``label=`` and the cluster
  // frame; re-emitting the root as a grid node or anchor would just
  // produce a redundant box alongside its children. Recurse straight
  // into the children — they'll cluster / grid / striped themselves
  // as needed.
  for (const childId of rootChildren) {
    emitSubtree(childId, indent, graph, palette, pairsByNode, childrenById, lines, style)
  }
}

// Recursive subtree emit. Cluster vs flat-node decision is the
// same as for the root, minus the cluster_top framing.
function emitSubtree(
  nodeId, indent, graph, palette, pairsByNode, childrenById, lines, style,
) {
  const node = graph.nodes.find((n) => n.id === nodeId)
  if (!node) return
  const children = childrenById.get(nodeId) || []
  const pairs = pairsByNode.get(nodeId) || []
  if (children.length === 0) {
    lines.push(indent + emitNode(node, palette, pairsByNode, style.neutral))
    return
  }
  if (pairs.length >= 2) {
    // CDC bridge: flat HTML-TABLE + children as siblings.
    lines.push(indent + emitGridNode(node, palette, pairs, style.neutral))
    for (const childId of children) {
      emitSubtree(childId, indent, graph, palette, pairsByNode, childrenById, lines, style)
    }
    return
  }
  // Non-CDC container: nested cluster. The invisible anchor inside
  // lets edges (if any) reference this scope by ``"<id>"``;
  // viz.js auto-routes to the cluster boundary.
  const clusterName = clusterIdFor(nodeId)
  lines.push(`${indent}subgraph ${clusterName} {`)
  lines.push(`${indent}  label="${labelEscape(scopeLabel(node))}";`)
  lines.push(`${indent}  labelloc="t";`)
  lines.push(`${indent}  labeljust="l";`)
  lines.push(`${indent}  fontcolor="${style.text}";`)
  lines.push(`${indent}  fontname="${DOT_FONT}";`)
  lines.push(`${indent}  style="rounded";`)
  lines.push(`${indent}  color="${style.frame}";`)
  lines.push(`${indent}  penwidth=1;`)
  lines.push(
    `${indent}  ${dotId(nodeId)} [shape=point, style=invis, width=0, height=0];`,
  )
  for (const childId of children) {
    emitSubtree(
      childId, indent + '  ', graph, palette, pairsByNode, childrenById, lines, style,
    )
  }
  lines.push(`${indent}}`)
}

// Sanitize an instance path into a cluster name that's a valid DOT
// identifier prefix. Same scheme used by GraphCanvas to recover the
// original instance path from a cluster's ``<title>`` text.
export function clusterIdFor(instanceId) {
  return 'cluster_' + String(instanceId).replace(/[^A-Za-z0-9_]/g, '_')
}

// ---------------------------------------------------------------------------
// Per-node CDC clock-box rendering (mirrors dot.py's _emit_html_grid_node /
// _crossing_pairs_into / _fill_for). Three render paths, picked by the
// number of distinct (src_clock, dst_clock) crossing pairs a node's subtree
// receives:
//
//   0 pairs  → plain rounded box (clock overlay paints fill at runtime)
//   1 pair   → striped two-tone fill ``<src_color>:<dst_color>``
//   2+ pairs → HTML-TABLE: header text row + one (src | dst) row per pair
// ---------------------------------------------------------------------------

// The palette and the clock->colour assignment come from
// ``palette.js``: the DOT generator bakes colours directly into HTML
// cells / striped fills, and the clock overlay paints node fills at
// runtime. Two definitions over two different clock sets is how the
// legend swatch and the bridge cell came to disagree.

// Collect deduped CDC (src, dst) pairs per node by walking each
// edge's ``overlays.clock.pairs`` and attributing the pairs to the
// destination node AND every ancestor up the dotted path. This
// matches dot.py's ``_crossing_pairs_into`` which uses
// ``dst.startswith(instance_path + '.')`` — a crossing terminating
// inside a sub-block also surfaces on the enclosing block, so
// "u_rb has both directions" reads correctly at the rb level even
// though the actual flops live inside sub-syncs.
function collectCdcPairsByNode(graph) {
  const buckets = new Map() // id -> Map<"src|dst", {src,dst,flops}>
  for (const edge of graph.edges || []) {
    const pairs = edge?.overlays?.clock?.pairs
    if (!Array.isArray(pairs) || pairs.length === 0) continue
    let cur = edge.to
    while (cur) {
      if (!buckets.has(cur)) buckets.set(cur, new Map())
      const bucket = buckets.get(cur)
      for (const p of pairs) {
        if (!p || typeof p.src_clock !== 'string') continue
        if (isUnconstrained(p.src_clock)) continue
        const key = `${p.src_clock}|${p.dst_clock}`
        const existing = bucket.get(key)
        if (existing) existing.flops += p.flops || 0
        else bucket.set(key, { ...p })
      }
      const lastDot = cur.lastIndexOf('.')
      if (lastDot < 0) break
      cur = cur.substring(0, lastDot)
    }
  }
  const out = new Map()
  for (const [id, bucket] of buckets) {
    const list = Array.from(bucket.values()).sort(
      (a, b) =>
        a.src_clock.localeCompare(b.src_clock) ||
        a.dst_clock.localeCompare(b.dst_clock),
    )
    out.set(id, list)
  }
  return out
}

function emitNode(node, palette, pairsByNode, neutral) {
  const pairs = pairsByNode.get(node.id) || []
  if (pairs.length >= 2) return emitGridNode(node, palette, pairs, neutral)
  if (pairs.length === 1) return emitStripedNode(node, palette, pairs[0], neutral)
  const label = nodeLabel(node)
  return `${dotId(node.id)} [label="${labelEscape(label)}"];`
}

// HTML-TABLE node label for a CDC bridge node with two or more
// distinct crossing pairs. The grid columns map to PHYSICAL PORT
// SIDES: input clocks on the LEFT, output clocks on the RIGHT
// (rankdir=LR convention). A clock can appear on both sides for a
// bidirectional bridge (FIFO) where it drives both an input and
// an output port.
//
// Derivation rule from the (src, dst) pairs of crossings entering
// this subtree:
//   input_clocks  = { p.src_clock | p ∈ pairs }
//   output_clocks = { p.dst_clock | p ∈ pairs }
// (A flop captures data on dst_clock — that captured signal leaves
// the bridge on dst_clock; the data that entered did so on
// src_clock. For sync flops this collapses to "in=src, out=dst";
// for FIFOs both clocks appear on both sides because both
// directions of pointer crossings get aggregated.)
function emitGridNode(node, palette, pairs, neutral) {
  const header = [node.instance_name || node.module, node.module]
    .filter((line, idx, arr) => idx === 0 || line !== arr[idx - 1])
    .map((line) => htmlLabelEscape(line))
    .join('<BR ALIGN="LEFT"/>')

  const inputClocks = sortedUnique(pairs.map((p) => p.src_clock))
  const outputClocks = sortedUnique(pairs.map((p) => p.dst_clock))
  const rowCount = Math.max(inputClocks.length, outputClocks.length, 1)

  const rows = [
    `<TR><TD COLSPAN="2" BORDER="0" ALIGN="LEFT">` +
      `<FONT FACE="Courier,monospace">${header}<BR ALIGN="LEFT"/></FONT></TD></TR>`,
  ]
  for (let i = 0; i < rowCount; i++) {
    const inClk = inputClocks[i]
    const outClk = outputClocks[i]
    const cells = []
    if (inClk) {
      const color = palette.get(inClk) || neutral
      cells.push(
        `<TD BGCOLOR="${color}" PORT="in_${htmlLabelEscape(inClk)}" WIDTH="60">` +
          `<FONT POINT-SIZE="10">${htmlLabelEscape(inClk)}</FONT></TD>`,
      )
    } else {
      cells.push('<TD BORDER="0"></TD>')
    }
    if (outClk) {
      const color = palette.get(outClk) || neutral
      cells.push(
        `<TD BGCOLOR="${color}" PORT="out_${htmlLabelEscape(outClk)}" WIDTH="60">` +
          `<FONT POINT-SIZE="10">${htmlLabelEscape(outClk)}</FONT></TD>`,
      )
    } else {
      cells.push('<TD BORDER="0"></TD>')
    }
    rows.push(`<TR>${cells.join('')}</TR>`)
  }
  const table =
    '<<TABLE BORDER="1" CELLBORDER="1" CELLSPACING="0" CELLPADDING="4">' +
    rows.join('') +
    '</TABLE>>'
  return `${dotId(node.id)} [label=${table}, shape=plaintext];`
}

function sortedUnique(arr) {
  return Array.from(new Set(arr)).sort()
}

// Striped two-tone fill for nodes with exactly one (src, dst) pair
// — typically a single-stage sync flop where data flows from src to
// dst. ``style="rounded,striped"`` splits the box left-half / right-
// half by the colon-separated palette pair.
function emitStripedNode(node, palette, pair, neutral) {
  const label = nodeLabel(node)
  const srcColor = palette.get(pair.src_clock) || neutral
  const dstColor = palette.get(pair.dst_clock) || neutral
  const attrs = [
    `label="${labelEscape(label)}"`,
    'style="rounded,striped"',
    `fillcolor="${srcColor}:${dstColor}"`,
  ]
  return `${dotId(node.id)} [${attrs.join(', ')}];`
}

// HTML labels in DOT are XML-ish — escape the conventional set.
function htmlLabelEscape(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

// Build the DOT ``node:port`` reference for one endpoint of a CDC
// edge anchored to a specific (src_clock, dst_clock) pair.
//
// Grid layout convention: input clocks on the LEFT (``in_<clk>``),
// output clocks on the RIGHT (``out_<clk>``). For an edge carrying
// the crossing pair, the data is in src_clock domain while in
// flight (the sync flop downstream captures it into dst_clock).
//
//   - ``'outgoing'`` (FROM node): pin to ``out_<src_clock>`` —
//     visually, the arrow leaves the right-side cell labelled with
//     the src clock. The cell exists when this node's grid has
//     ``src_clock`` on its dst-aggregate side (some crossing in
//     this node's subtree captures at this clock).
//   - ``'incoming'`` (TO node): pin to ``in_<src_clock>`` —
//     enters the left-side cell labelled with the src clock.
//
// Plain / striped / no-grid nodes get bare ``"node_id"`` and
// viz.js auto-routes to the box.
function portRefForPair(nodeId, pair, direction, pairsByNode) {
  const id = dotId(nodeId)
  const ownPairs = pairsByNode.get(nodeId) || []
  if (ownPairs.length < 2) return id
  if (!pair || typeof pair.src_clock !== 'string') return id
  const cellSide = direction === 'outgoing' ? 'out' : 'in'
  const clock = pair.src_clock
  // The cell exists in the grid iff this clock appears on the
  // matching side of *some* pair the node aggregated:
  //   - ``in_<clk>`` cells come from ``p.src_clock`` of any pair
  //   - ``out_<clk>`` cells come from ``p.dst_clock`` of any pair
  const hasCell = ownPairs.some((q) =>
    cellSide === 'in' ? q.src_clock === clock : q.dst_clock === clock,
  )
  if (!hasCell) return id
  return `${id}:"${cellSide}_${clock}"`
}

// Special-case anchor for unconstrained pairs. ``src_clock`` is
// ``<unconstrained>`` and not a real cell label, so we anchor on
// the destination side by ``dst_clock`` (the receiving clock where
// the sync flop sits) — the in_<dst_clock> cell exists when the
// destination is a grid node and its dst_clock matches.
function portRefForUnconstrainedDst(nodeId, pair, pairsByNode) {
  const id = dotId(nodeId)
  const ownPairs = pairsByNode.get(nodeId) || []
  if (ownPairs.length < 2) return id
  if (!pair || typeof pair.dst_clock !== 'string') return id
  const clock = pair.dst_clock
  const hasCell = ownPairs.some((q) => q.src_clock === clock)
  if (!hasCell) return id
  return `${id}:"in_${clock}"`
}

// Cluster-frame title: prefer the instance name (familiar to the
// user from the breadcrumb), fall back to module name for
// blackboxes or the top of the full graph where the instance name
// might be empty.
function scopeLabel(node) {
  return node.instance_name || node.module || node.id || ''
}

// DOT attributes for one CDC pair's edge. A pair the producer marked
// ``hazard: false`` (every crossing in it recognised-safe,
// rtl-buddy-sch#211) is a solid teal edge that names its schemes —
// ``2FF clk_a → clk_b`` — with no hazard dash. Anything else, including
// a pair from a view.json written before the field existed, keeps the
// dashed ``--err`` hazard look.
export function cdcPairAttrs(p, cdc, sync) {
  if (p && p.hazard === false) {
    const schemes = (Array.isArray(p.synchronizers) ? p.synchronizers : [])
      .map((s) => String(s).toUpperCase())
      .join('+')
    const label = `${schemes ? schemes + ' ' : ''}${p.src_clock} → ${p.dst_clock}`
    return ` [label="${labelEscape(label)}", color="${sync}", fontcolor="${sync}", fontsize=9]`
  }
  const label = `${p.src_clock} → ${p.dst_clock}`
  return ` [label="${labelEscape(label)}", color="${cdc}", style="dashed", fontcolor="${cdc}", fontsize=9]`
}

// Label for a CDC-flagged edge: dedupe the per-flop pairs (already
// deduped in json_render.py) into ``src → dst`` lines. Multiple
// distinct pairs stack vertically. Returns empty string when the
// edge has no CDC overlay.
function edgeLabel(edge) {
  const ov = edge?.overlays?.clock
  if (!ov || !Array.isArray(ov.pairs) || ov.pairs.length === 0) return ''
  return ov.pairs.map((p) => `${p.src_clock} → ${p.dst_clock}`).join('\\n')
}

function nodeLabel(node) {
  // Two-line label: instance name on top, module name underneath.
  // Blackboxes carry their module name only (no instance, by
  // definition — they're modules we never resolved) plus the
  // ``(blackbox)`` suffix the renderer convention uses.
  const inst = node.instance_name || node.module
  const lines = [inst, node.module]
  if (node.is_blackbox) lines.push('(blackbox)')
  return lines.filter((line, idx) => idx === 0 || line !== lines[idx - 1]).join('\\n')
}

// DOT node IDs must be alphanumeric (no dots); use the instance
// path as a literal quoted string. Quoting always is simpler than
// trying to escape only dotted ids.
export function dotId(id) {
  return `"${dotEscape(id)}"`
}

export function dotEscape(s) {
  return String(s).replace(/\\/g, '\\\\').replace(/"/g, '\\"')
}

// Label-specific escape: quotes only. Keeps ``\n`` newline markers
// intact (Graphviz interprets them as line breaks within the label).
export function labelEscape(s) {
  return String(s).replace(/"/g, '\\"')
}

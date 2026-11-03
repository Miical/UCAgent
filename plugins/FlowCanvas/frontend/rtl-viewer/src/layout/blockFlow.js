// Block-flow DOT generator.
//
// The hier-view shows the full nested-cluster hierarchy from the
// producer's ``layout.dot``. This module produces a *one-level*
// view: pick a scope (an instance path), render its direct
// children as sibling boxes, and connect them via inferred signal
// flow.
//
// Connectivity is derived in the SPA from each child's
// ``ports[].expr`` field (the net expression the parent wired to
// that port). For each unique non-clock/reset net:
//   - ``out``-direction ports on child A drive ``in``-direction
//     ports on child B → emit ``A:p_out -> B:p_in`` with the net
//     as label.
//   - Nets matching a top-level scope input port → emit
//     ``_in_<port> -> child:p_in`` (signal enters the scope).
//   - Nets matching a top-level scope output port → emit
//     ``child:p_out -> _out_<port>`` (signal leaves the scope).
//
// Clock and reset nets are skipped because they fan out to nearly
// every child and would bury the data flow.
//
// Children render as ``shape=Mrecord`` with named record ports so
// every input/output gets its own attachment point along the west
// / east edge — polyline no longer piles every edge onto the
// geometric center of the side.

import { parentPath } from '../instancePath.js'
import { token } from '../theme.js'

const CLOCK_RESET_RE = /(?:^|_)(?:clk|clock|rst|reset)(?:$|_)/i
// Bare-identifier net expressions only — drops slices / concats /
// expressions like ``a[0]`` or ``{x, y}`` so the diagram doesn't
// chase fragments through partial-bus connections. Matches the
// dot renderer's port-signal-flow filter.
const IDENTIFIER_RE = /^[A-Za-z_][A-Za-z0-9_]*$/
// Marker glyph for SystemVerilog interface ports — distinct from
// the scalar ``▶`` so a reader can tell at a glance that the row
// is a bundle (a fan-in of signals) rather than a single wire.
// Paired with italic styling + a warning-tinted background in the cell
// HTML. (rtl-buddy-view#102.)
const INTERFACE_GLYPH = '▶▶'

// Colours are resolved here, at DOT-build time: Graphviz bakes them
// into the emitted SVG, so there is no ``var(--…)`` for the sheet to
// re-resolve later. GraphCanvas re-lays-out on a theme flip.
function dotStyle() {
  return {
    box: token('--panel-2'),
    text: token('--fg'),
    frame: token('--fg-faint'),
    // Edge stroke (and, via ``color``, the arrowhead fill). Was
    // ``--line-strong``, a surface-divider tier that reads as "barely
    // there" once it's a 1px polyline on the open canvas rather than a
    // border between two filled areas. Connectivity is the whole point
    // of the block-flow view, so the edges get the readable text tier.
    line: token('--fg-muted'),
    ifaceBg: token('--warn-bg'),
  }
}

function isClockOrResetName(name) {
  if (typeof name !== 'string') return false
  return CLOCK_RESET_RE.test(name)
}

function dotEscape(s) {
  return String(s).replace(/\\/g, '\\\\').replace(/"/g, '\\"')
}

// Escape for embedding inside an HTML-like Graphviz label
// (``label=<...>`` syntax). The string lives inside table cells,
// so the standard HTML entity set applies.
function htmlEscape(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function dotId(id) {
  return `"${dotEscape(id)}"`
}

// Direct children of ``scopeId``: nodes whose parent path is
// ``scopeId``. Not "exactly one further dot-segment": a generate-scoped
// child ``top.g[0].u_s`` is a direct child of ``top`` (rtl-buddy-sch#209),
// and its ``instance_name`` is what says so (``parentPath``).
function directChildrenOf(graph, scopeId) {
  if (!graph || !graph.nodes || !scopeId) return []
  return graph.nodes.filter(
    (n) => n && typeof n.id === 'string' && n.id !== scopeId && parentPath(n) === scopeId,
  )
}

function findNode(graph, id) {
  return graph && graph.nodes ? graph.nodes.find((n) => n.id === id) : null
}

function isInputDir(d) {
  return d === 'input'
}
function isOutputDir(d) {
  return d === 'output' || d === 'inout'
}

// Interface ports (``test_mem_if.sub m`` style) have no scalar
// direction. Block-flow renders them as a separate group on the
// LEFT side of each child box, italicised and amber-tinted so the
// reader can tell they're bundles rather than wires. We don't
// trace edges for them — that would require resolving the
// interface's internal signals, which the producer (Verible CST)
// doesn't expose. The cell stays clickable (``bf-iface:`` HREF)
// so the SPA can surface the bundle name in NodeDetail.
// (rtl-buddy-view#102.)
function interfacePortsFor(child) {
  if (!child || !child.ports) return []
  const out = []
  const seen = new Set()
  // 1. Native (unflattened) interface bundle ports — ``test_mem_if.sub m``
  //    surfaces as a single ``port_kind:"interface"`` port.
  for (const port of child.ports) {
    if (port && port.port_kind === 'interface') {
      out.push({
        name: port.name,
        interface_type: port.interface_type || '',
        modport: port.modport || null,
      })
      seen.add(port.name)
    }
  }
  // 2. Flattened interface signals (``m.req``, kind ``interface_signal``).
  //    When the interface body is in scope the producer flattens the
  //    bundle into one scalar port per modport signal. Re-group them by
  //    the owning interface-port name (prefix before the first dot) so
  //    the bundle STILL draws as a single ▶▶ interface pin — the anchor
  //    the axi-perf overlay decorates. Without this, a real design
  //    (AXI_BUS interface in scope) would show no interface pin at all.
  for (const port of child.ports) {
    if (!port || port.port_kind !== 'interface_signal') continue
    const name = typeof port.name === 'string' ? port.name : ''
    const base = name.split('.', 1)[0]
    if (!base || seen.has(base)) continue
    seen.add(base)
    out.push({
      name: base,
      interface_type: port.interface_type || '',
      modport: port.modport || null,
    })
  }
  // 3. Manifest-described (synthesized) AXI bundle pins. When the AXI
  //    ports aren't visible to the parser (macro-generated flat ports),
  //    json_render attaches the bundle to this node from the
  //    axi-bundles.yaml description as a synthetic ``bundle_pin``. Draw
  //    each as a ▶▶ interface cell so the same axi-perf overlay paint
  //    decorates it — no profiler-specific RTL stub required.
  const axiPins = child.overlays && child.overlays['axi-perf']
  const bundlePins = axiPins && Array.isArray(axiPins.bundle_pins) ? axiPins.bundle_pins : []
  for (const pin of bundlePins) {
    if (!pin || pin.synthetic !== true || typeof pin.port !== 'string') continue
    if (seen.has(pin.port)) continue
    seen.add(pin.port)
    out.push({
      name: pin.port,
      interface_type: (pin.bundle && pin.bundle.protocol) || 'AXI',
      modport: pin.role || null,
    })
  }
  out.sort((a, b) => a.name.localeCompare(b.name))
  return out
}

function interfacePortCellHtml(ownerId, iface, style) {
  const cellId = `bf-iface:${htmlEscape(ownerId)}:${htmlEscape(iface.name)}`
  const suffix = iface.modport
    ? `${iface.interface_type}.${iface.modport}`
    : iface.interface_type
  // TITLE doubles as the hover tooltip; pack the bundle descriptor
  // there so the user can see ``test_mem_if.sub`` without clicking.
  const title = suffix ? `${cellId} :: ${suffix}` : cellId
  return (
    `<TD HREF="${cellId}" TITLE="${title}" PORT="${htmlEscape(iface.name)}" ` +
    `ALIGN="LEFT" BGCOLOR="${style.ifaceBg}">` +
    `<FONT POINT-SIZE="9"><I>${INTERFACE_GLYPH} ${htmlEscape(iface.name)}</I></FONT></TD>`
  )
}

/**
 * Build the DOT source for a block-flow view of ``scopeId``'s
 * immediate children. Returns a DOT string ready for viz.js.
 *
 * Layout choices mirror the hier-view's standalone settings so the
 * two tabs share the same look-and-feel:
 *   - ``rankdir=LR``, ``splines=polyline`` for schematic-style edges
 *   - ``Courier`` monospace font
 *   - Top-level cluster framing the scope with port anchors at
 *     ``rank=source`` (inputs) / ``rank=sink`` (outputs)
 *   - Children render as ``shape=Mrecord`` with named record ports
 *     so each connection attaches at a distinct slot along the
 *     west / east edge of the box.
 */
export function buildBlockFlowDot(graph, scopeId) {
  if (!graph || !scopeId) return _emptyDigraph('no scope selected')
  const scope = findNode(graph, scopeId)
  if (!scope) return _emptyDigraph(`scope ${scopeId} not in graph`)
  const children = directChildrenOf(graph, scopeId)
  if (children.length === 0) {
    // Leaf module — no children to expand, but still useful to show
    // the module + its declared ports (matches hier-view's behaviour
    // for leaves). Lifts the user out of the "module X has no
    // children" dead-end placeholder when they descend into a leaf.
    return _renderLeafScope(scope)
  }

  // Index ports by net name across all children, partitioned by
  // direction. Skip clock/reset and non-identifier nets.
  const driversByNet = new Map() // net -> [{ child, port }]
  const sinksByNet = new Map() //   net -> [{ child, port }]
  for (const child of children) {
    for (const port of child.ports || []) {
      const expr = typeof port.expr === 'string' ? port.expr.trim() : ''
      if (!expr || !IDENTIFIER_RE.test(expr)) continue
      if (isClockOrResetName(expr)) continue
      if (isOutputDir(port.dir)) {
        if (!driversByNet.has(expr)) driversByNet.set(expr, [])
        driversByNet.get(expr).push({ child, port })
      } else if (isInputDir(port.dir)) {
        if (!sinksByNet.has(expr)) sinksByNet.set(expr, [])
        sinksByNet.get(expr).push({ child, port })
      }
    }
  }

  // Per-child port lists for the Mrecord label. Only ports that
  // actually participate in a derived connection get a record
  // slot — emitting every port would clutter the diagram with
  // unconnected stubs.
  const childInputPorts = new Map() // child.id -> [port name, ...]
  const childOutputPorts = new Map()
  for (const child of children) {
    childInputPorts.set(child.id, [])
    childOutputPorts.set(child.id, [])
  }
  const seenInPort = new Set() // `${child.id}:${port.name}`
  const seenOutPort = new Set()
  for (const drivers of driversByNet.values()) {
    for (const { child, port } of drivers) {
      const key = `${child.id}:${port.name}`
      if (seenOutPort.has(key)) continue
      seenOutPort.add(key)
      childOutputPorts.get(child.id).push(port.name)
    }
  }
  for (const sinks of sinksByNet.values()) {
    for (const { child, port } of sinks) {
      const key = `${child.id}:${port.name}`
      if (seenInPort.has(key)) continue
      seenInPort.add(key)
      childInputPorts.get(child.id).push(port.name)
    }
  }
  // Stable visual ordering: alphabetical within each side.
  for (const list of childInputPorts.values()) list.sort()
  for (const list of childOutputPorts.values()) list.sort()

  // Scope ports become the external interface. Their *names* are
  // also the net names that cross the scope boundary.
  const scopeInputs = new Set()
  const scopeOutputs = new Set()
  for (const port of scope.ports || []) {
    if (isClockOrResetName(port.name)) continue
    if (isInputDir(port.dir)) scopeInputs.add(port.name)
    else if (isOutputDir(port.dir)) scopeOutputs.add(port.name)
  }

  const style = dotStyle()
  const lines = []
  lines.push('digraph block_flow {')
  lines.push('  rankdir="LR";')
  lines.push('  compound=true;')
  // ``splines=true`` = bezier routing. Drops the schematic
  // right-angle look in favour of smooth curves that respect
  // node boundaries reliably (viz.js's ortho router cuts through
  // nodes when an edge spans multiple ranks, and polyline
  // ditto for some layouts).
  lines.push('  splines=true;')
  lines.push('  nodesep=0.5;')
  lines.push('  ranksep=1.44;')
  lines.push('  fontname="Courier,monospace";')
  // ``shape=plaintext`` so the HTML-table label is drawn as-is —
  // the table itself supplies the border, background and per-cell
  // ports. (Mrecord would give rounded corners but doesn't support
  // per-cell fontsize, which we want for compact port labels.)
  lines.push('  bgcolor="transparent";')
  lines.push(
    `  node [shape=plaintext, fontname="Courier,monospace", fontcolor="${style.text}"];`,
  )
  lines.push(
    `  edge [fontname="Courier,monospace", color="${style.line}", ` +
      `fontcolor="${style.text}"];`,
  )
  lines.push('')
  lines.push('  subgraph cluster_flow_scope {')
  // Title shape mirrors the child boxes (instance name on top,
  // module type below) — but when the two would be identical
  // (typical for the design top, where ``instance_name`` is null
  // and the fallback collapses to ``module``), emit one line to
  // avoid the redundant "X / X" stack.
  //
  // ``\l`` is the DOT left-aligned-newline marker. ``dotEscape``
  // doubles backslashes (correct for literal text but wrong for
  // record metachars), so escape the identifiers FIRST and
  // concatenate ``\l`` afterwards.
  const instName = scope.instance_name || ''
  const titleLines =
    instName && instName !== scope.module
      ? [instName, scope.module]
      : [scope.module]
  const title = titleLines.map((l) => `${dotEscape(l)}\\l`).join('')
  lines.push(`    label="${title}";`)
  lines.push('    labelloc="t";')
  lines.push('    labeljust="l";')
  lines.push('    style="rounded";')
  lines.push(`    fontcolor="${style.text}";`)
  lines.push(`    color="${style.frame}";`)
  lines.push('    penwidth=2;')
  lines.push('    margin="20,20";')

  // Port anchors. Same shape as the hier-view's so the eye doesn't
  // have to relearn the convention when switching tabs.
  if (scopeInputs.size > 0) {
    lines.push('    { rank=source;')
    for (const p of [...scopeInputs].sort()) {
      lines.push(
        `      "_in_${p}" [shape=plaintext, label="${dotEscape(p)} ▶", fontsize=9];`,
      )
    }
    lines.push('    }')
  }
  if (scopeOutputs.size > 0) {
    lines.push('    { rank=sink;')
    for (const p of [...scopeOutputs].sort()) {
      lines.push(
        `      "_out_${p}" [shape=plaintext, label="▶ ${dotEscape(p)}", fontsize=9];`,
      )
    }
    lines.push('    }')
  }

  // HTML-table child labels. One row per port slot, with the
  // middle cell spanning all rows via ROWSPAN:
  //
  //   ┌──────────┬─────────┬───────────┐
  //   │ d_in     │  u_a    │      q    │
  //   │ aux_in   │  a_mod  │           │
  //   └──────────┴─────────┴───────────┘
  //
  // Input cells are ALIGN=LEFT, output cells ALIGN=RIGHT so the
  // text hugs the edge of the box on the side where the wire
  // attaches. Port labels use POINT-SIZE=9 to keep the box
  // compact even when a module has many ports.
  const PORT_FONT_SIZE = 9
  const BG = style.box
  for (const child of children) {
    const inPorts = childInputPorts.get(child.id) || []
    const outPorts = childOutputPorts.get(child.id) || []
    // Interface ports never participate in wire-net derivation —
    // they're bundles. Pull them out separately and stack them on
    // the left column below the wire inputs so the box still
    // reflects the full port list of the underlying module.
    const ifacePorts = interfacePortsFor(child)
    const inst = child.instance_name || child.module
    const leftRowCount = inPorts.length + ifacePorts.length
    const rowCount = Math.max(leftRowCount, outPorts.length, 1)
    const rows = []
    for (let i = 0; i < rowCount; i++) {
      const tds = []
      // Left column: wire inputs first, then interface-port rows,
      // then filler. ``HREF`` is the only per-cell attribute viz.js
      // actually propagates to the SVG (becomes ``<a xlink:href>``)
      // — ``ID`` is silently dropped on TDs. Click handlers walk up
      // to the nearest ``<a>`` and route based on the href prefix.
      if (i < inPorts.length) {
        const p = inPorts[i]
        const cellId = `bf-in:${htmlEscape(child.id)}:${htmlEscape(p)}`
        tds.push(
          `<TD HREF="${cellId}" TITLE="${cellId}" PORT="${htmlEscape(p)}" ALIGN="LEFT">` +
            `<FONT POINT-SIZE="${PORT_FONT_SIZE}">▶ ${htmlEscape(p)}</FONT></TD>`,
        )
      } else if (i < leftRowCount) {
        tds.push(
          interfacePortCellHtml(child.id, ifacePorts[i - inPorts.length], style),
        )
      } else {
        tds.push('<TD></TD>')
      }
      // Middle column: instance + module, only on first row.
      if (i === 0) {
        const cellId = `bf-ctr:${htmlEscape(child.id)}`
        tds.push(
          `<TD HREF="${cellId}" TITLE="${cellId}" ROWSPAN="${rowCount}" ` +
            `ALIGN="CENTER" WIDTH="160">` +
            `<B>${htmlEscape(inst)}</B><BR/>${htmlEscape(child.module)}</TD>`,
        )
      }
      // Right column: output port or filler.
      if (i < outPorts.length) {
        const p = outPorts[i]
        const cellId = `bf-out:${htmlEscape(child.id)}:${htmlEscape(p)}`
        tds.push(
          `<TD HREF="${cellId}" TITLE="${cellId}" PORT="${htmlEscape(p)}" ALIGN="RIGHT">` +
            `<FONT POINT-SIZE="${PORT_FONT_SIZE}">${htmlEscape(p)} ▶</FONT></TD>`,
        )
      } else {
        tds.push('<TD></TD>')
      }
      rows.push(`<TR>${tds.join('')}</TR>`)
    }
    const label =
      `<<TABLE BORDER="1" CELLBORDER="1" CELLSPACING="0"` +
      ` CELLPADDING="6" BGCOLOR="${BG}">${rows.join('')}</TABLE>>`
    const attrs = [`label=${label}`, `group="cluster_flow_scope"`]
    if (child.is_blackbox) {
      attrs.push('style="dashed"')
    }
    lines.push(`    ${dotId(child.id)} [${attrs.join(', ')}];`)
  }

  // Each edge gets a stable ``id="bf-edge:<src>:<srcPort>:<dst>:<dstPort>"``
  // attribute so the SPA's port-click highlight can find the edge
  // directly. Graphviz strips port names from the edge ``<title>``
  // (keeps only the compass point), so the title is useless as a
  // lookup key. ``id`` propagates unchanged to the SVG, which is
  // why we use it instead.
  //
  // Boundary edges encode the scope-anchor side with a leading
  // ``_`` to distinguish from real child IDs:
  //   _in_<net>  → leading underscore reserved for anchors
  //   _out_<net>
  const edgeIdForInternal = (drv, snk) =>
    `bf-edge:${drv.child.id}:${drv.port.name}:${snk.child.id}:${snk.port.name}`
  const edgeIdForInput = (net, child, port) =>
    `bf-edge:_in_${net}::${child.id}:${port.name}`
  const edgeIdForOutput = (net, child, port) =>
    `bf-edge:${child.id}:${port.name}:_out_${net}:`

  // External signal-flow: scope input -> child sink, child driver -> scope output.
  const seenIn = new Set()
  const seenOut = new Set()
  for (const [net, sinks] of sinksByNet) {
    if (!scopeInputs.has(net)) continue
    for (const { child, port } of sinks) {
      const key = `${net}->${child.id}:${port.name}`
      if (seenIn.has(key)) continue
      seenIn.add(key)
      lines.push(
        `    "_in_${net}" -> ${dotId(child.id)}:${port.name}:w ` +
          `[id="${edgeIdForInput(net, child, port)}", ` +
          `color="${style.line}", penwidth=1.2, arrowsize=0.6, tailport=e];`,
      )
    }
  }
  for (const [net, drivers] of driversByNet) {
    if (!scopeOutputs.has(net)) continue
    for (const { child, port } of drivers) {
      const key = `${child.id}:${port.name}->${net}`
      if (seenOut.has(key)) continue
      seenOut.add(key)
      lines.push(
        `    ${dotId(child.id)}:${port.name}:e -> "_out_${net}" ` +
          `[id="${edgeIdForOutput(net, child, port)}", ` +
          `color="${style.line}", penwidth=1.2, arrowsize=0.6, headport=w];`,
      )
    }
  }

  // Internal signal-flow: driver -> sink for nets that don't touch
  // the scope boundary. ``label="net_name"`` so the reviewer can
  // tell which signal carries the connection at a glance.
  const seenInternal = new Set()
  for (const [net, drivers] of driversByNet) {
    if (scopeOutputs.has(net)) continue // already emitted above
    const sinks = sinksByNet.get(net) || []
    for (const drv of drivers) {
      for (const snk of sinks) {
        if (drv.child.id === snk.child.id) continue
        const key = `${drv.child.id}:${drv.port.name}->${snk.child.id}:${snk.port.name}`
        if (seenInternal.has(key)) continue
        seenInternal.add(key)
        lines.push(
          `    ${dotId(drv.child.id)}:${drv.port.name}:e -> ` +
            `${dotId(snk.child.id)}:${snk.port.name}:w ` +
            `[id="${edgeIdForInternal(drv, snk)}", ` +
            `label="${dotEscape(net)}", penwidth=1.2, arrowsize=0.7];`,
        )
      }
    }
  }

  lines.push('  }')
  lines.push('}')
  return lines.join('\n')
}

function _emptyDigraph(reason) {
  // viz.js needs a valid digraph even when the scope is empty —
  // shows a placeholder so the canvas isn't a blank rectangle.
  return [
    'digraph block_flow_empty {',
    '  rankdir="LR";',
    '  fontname="Courier,monospace";',
    `  "_empty" [shape=plaintext, label="${dotEscape(reason)}"];`,
    '}',
  ].join('\n')
}

// Render a leaf scope (no children) as a single HTML-table box
// listing all its declared ports — same shape as the child boxes
// in the multi-child case so the eye doesn't have to relearn the
// convention. Skips the cluster frame + boundary anchors (nothing
// to wire to). Ports retain their click identity (``bf-in:`` /
// ``bf-out:`` HREFs) so right-click open-source works for leaves
// too. Clock/reset ports are included here — hier-view shows all
// ports for a leaf, so we match that.
function _renderLeafScope(scope) {
  const style = dotStyle()
  const PORT_FONT_SIZE = 9
  const BG = style.box
  const inPorts = []
  const outPorts = []
  for (const port of scope.ports || []) {
    if (port && port.port_kind === 'interface') continue
    if (isInputDir(port.dir)) inPorts.push(port.name)
    else if (isOutputDir(port.dir)) outPorts.push(port.name)
  }
  inPorts.sort()
  outPorts.sort()
  // Interface ports stack on the left side, below the wire inputs.
  // Same rationale as the multi-child path — see ``interfacePortsFor``.
  const ifacePorts = interfacePortsFor(scope)

  const inst = scope.instance_name || scope.module
  const showTwoLines = scope.instance_name && scope.instance_name !== scope.module
  const middle = showTwoLines
    ? `<B>${htmlEscape(inst)}</B><BR/>${htmlEscape(scope.module)}`
    : `<B>${htmlEscape(scope.module)}</B>`

  const leftRowCount = inPorts.length + ifacePorts.length
  const rowCount = Math.max(leftRowCount, outPorts.length, 1)
  const rows = []
  for (let i = 0; i < rowCount; i++) {
    const tds = []
    if (i < inPorts.length) {
      const p = inPorts[i]
      const cellId = `bf-in:${htmlEscape(scope.id)}:${htmlEscape(p)}`
      tds.push(
        `<TD HREF="${cellId}" TITLE="${cellId}" PORT="${htmlEscape(p)}" ALIGN="LEFT">` +
          `<FONT POINT-SIZE="${PORT_FONT_SIZE}">▶ ${htmlEscape(p)}</FONT></TD>`,
      )
    } else if (i < leftRowCount) {
      tds.push(interfacePortCellHtml(scope.id, ifacePorts[i - inPorts.length], style))
    } else {
      tds.push('<TD></TD>')
    }
    if (i === 0) {
      const cellId = `bf-ctr:${htmlEscape(scope.id)}`
      // WIDTH gives the middle column breathing room for long
      // module / instance names. Without it Graphviz auto-sizes to
      // the FONT-rendered width, which underestimates kerning on
      // some host fonts and collides with the output-port column.
      // 160 pt (~2 inches) fits ``test_module_3``-class names with
      // margin; longer names auto-grow on top.
      tds.push(
        `<TD HREF="${cellId}" TITLE="${cellId}" ROWSPAN="${rowCount}" ` +
          `ALIGN="CENTER" WIDTH="160">${middle}</TD>`,
      )
    }
    if (i < outPorts.length) {
      const p = outPorts[i]
      const cellId = `bf-out:${htmlEscape(scope.id)}:${htmlEscape(p)}`
      tds.push(
        `<TD HREF="${cellId}" TITLE="${cellId}" PORT="${htmlEscape(p)}" ALIGN="RIGHT">` +
          `<FONT POINT-SIZE="${PORT_FONT_SIZE}">${htmlEscape(p)} ▶</FONT></TD>`,
      )
    } else {
      tds.push('<TD></TD>')
    }
    rows.push(`<TR>${tds.join('')}</TR>`)
  }
  const label =
    `<<TABLE BORDER="1" CELLBORDER="1" CELLSPACING="0"` +
    ` CELLPADDING="6" BGCOLOR="${BG}">${rows.join('')}</TABLE>>`

  const lines = [
    'digraph block_flow_leaf {',
    '  rankdir="LR";',
    '  bgcolor="transparent";',
    '  fontname="Courier,monospace";',
    `  node [shape=plaintext, fontname="Courier,monospace", fontcolor="${style.text}"];`,
    `  ${dotId(scope.id)} [label=${label}];`,
    '}',
  ]
  return lines.join('\n')
}

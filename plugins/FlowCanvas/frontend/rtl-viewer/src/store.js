// Pinia store — single source of truth for the viewer.
//
// State shape:
//   - graph: the parsed view.json v1 payload (or null while loading)
//   - status: 'idle' | 'loading' | 'ready' | 'error'
//   - error: human-readable message when status === 'error'
//   - selection: the currently-selected node id (or null)
//   - enabledOverlays: Set<string> of overlay names the user has
//     toggled on. Defaults to all overlays in `graph.overlays_present`.
//   - hubCursorTimeFs: latest cursor_time_changed payload from the hub.
//   - hubScope: latest scope_changed payload from the hub.
//   - diagnosticsBySource: { [source]: items[] } keyed by producer.
//     Latest-writer-wins per source; empty items clears the source.
//   - hubError / hubErrorDismissedAt: surfaces hub `error` envelopes.
//   - covData / covEnabled: the hub's live coverage payload
//     (`/cov.json`, null without a hub) and the canvas tint toggle.
//   - physScope: 'subtree' | 'self' — which figures the phys overlay
//     paints. A re-style, not a re-layout.
//
// Actions:
//   - bootstrap(): kick off the initial load (URL query, inlined
//     data, drag-drop, or file picker — the loader handles
//     precedence).
//   - loadFromUrl(url) / loadFromFile(file) / loadFromText(text):
//     three explicit entry points for the same parse+validate path.
//   - select(id) / clearSelection()
//   - toggleOverlay(name) / toggleCoverageTint() / setPhysScope(scope)
//   - loadCoverage(): one-shot fetch of the hub's /cov.json.
//   - applyHubCursorTime / applyHubSelection / applyHubScope /
//     applyDiagnostics / applyHubError — invoked by useHub on inbound
//     events; centralising them keeps the composable thin and the
//     store the only writer.
//
import { defineStore } from 'pinia'
import { parseViewJson } from './parse.js'
import { leafName } from './instancePath.js'
import { commonSourceRoot } from './sourcePaths.js'
import { moduleCoverage } from './covData.js'


// -------------------------------------------------------------------
// Structured hub error envelopes (rtl-buddy-view#130)
// -------------------------------------------------------------------
//
// A hub that implements the #130 contract answers a failing
// ``GET /view.json`` with ``application/json``:
//
//   {"error": {"kind": "view_generation_failed", "model": "...",
//              "message": "...", "log_path": "...",
//              "log_tail": ["...", "..."]}}
//
// plus ``unknown_model`` (404) and ``no_active_model`` (409). Older
// hubs return a bare ``text/plain`` 500 — we must keep working
// against those, so parsing is strictly best-effort: anything that
// isn't a recognisable envelope degrades to "quote the body text"
// (the generic failure placeholder).
const KNOWN_ERROR_KINDS = new Set([
  'view_generation_failed',
  'unknown_model',
  'no_active_model',
])

/**
 * Best-effort parse of a hub failure body into the structured shape
 * the placeholder renders. Returns ``null`` when the body isn't a
 * #130 envelope (old hub, proxy error page, empty body) — callers
 * then fall back to the raw text.
 */
export function parseHubError(text) {
  if (typeof text !== 'string' || text.length === 0) return null
  let payload
  try {
    payload = JSON.parse(text)
  } catch {
    return null
  }
  const err = payload && typeof payload === 'object' ? payload.error : null
  if (!err || typeof err !== 'object') return null
  if (typeof err.kind !== 'string' || !KNOWN_ERROR_KINDS.has(err.kind)) return null
  return {
    kind: err.kind,
    model: typeof err.model === 'string' ? err.model : null,
    message:
      typeof err.message === 'string' && err.message.length > 0
        ? err.message
        : err.kind.replace(/_/g, ' '),
    logPath: typeof err.log_path === 'string' ? err.log_path : null,
    logTail: Array.isArray(err.log_tail)
      ? err.log_tail.filter((l) => typeof l === 'string')
      : [],
    modelsUrl: typeof err.models_url === 'string' ? err.models_url : null,
  }
}


// -------------------------------------------------------------------
// Diagnostic → node mapping
// -------------------------------------------------------------------
//
// The v1 ``diagnostics_set`` payload carries each item with at least
// ``{ file, line, col, severity, message }``. Producers MAY also
// attach an ``instance_path`` hint for fast-path matching (the hub
// doesn't strip it from forwarded events). To render badges on the
// schematic we need to map each item to one of the nodes currently
// loaded in the graph.
//
// Strategy, in priority order:
//
//   1. ``item.instance_path`` literal → if it names a known node,
//      use it. No file lookup needed.
//   2. File+line range → walk every node whose ``source.file``
//      matches and whose ``[start_line, end_line]`` range contains
//      ``item.line``, then pick the *deepest* match (the smallest
//      enclosing range). "Deepest" because nested instances at the
//      same source produce overlapping ranges; the most specific
//      one is what the producer almost certainly meant.
//   3. No match → null (item still appears in the DiagnosticsPanel
//      sidebar; it just doesn't anchor to a canvas node).
//
// The line range in ``node.source`` is the *instantiation site* in
// the parent module's file, not the instantiated module's body.
// A diagnostic at e.g. ``rtl/fifo.sv:142`` matches the instance
// whose declaration spans line 142 in ``rtl/fifo.sv`` — which works
// for issues reported against an instance declaration but not for
// issues reported against the module body unless the producer also
// hands in ``instance_path``.

function _resolveDiagnosticItemToNodeId(item, nodesByFile, nodeIdSet) {
  if (typeof item?.instance_path === 'string' && item.instance_path) {
    return nodeIdSet.has(item.instance_path) ? item.instance_path : null
  }
  if (typeof item?.file !== 'string' || !item.file) return null
  const candidates = nodesByFile.get(item.file)
  if (!candidates || candidates.length === 0) return null
  const line = typeof item?.line === 'number' ? item.line : null
  if (line === null) return null
  let bestId = null
  let bestSpan = Infinity
  for (const n of candidates) {
    const s = n.source
    if (!s) continue
    if (typeof s.start_line !== 'number' || typeof s.end_line !== 'number') continue
    if (line < s.start_line || line > s.end_line) continue
    const span = s.end_line - s.start_line
    if (span < bestSpan) {
      bestSpan = span
      bestId = n.id
    }
  }
  return bestId
}

// Build the (file → nodes) index once per ``diagnosticsByNode`` read.
// The graph is mutated wholesale on load so we don't cache across
// reads — the work is O(nodes) and the call cadence is gated by
// Vue's reactivity (only fires when ``diagnosticsBySource`` or
// ``graph`` actually changes).
function _diagnosticsByNode(state) {
  const out = {}
  if (!state.graph || !state.graph.nodes) return out
  const nodesByFile = new Map()
  const nodeIdSet = new Set()
  for (const n of state.graph.nodes) {
    nodeIdSet.add(n.id)
    const f = n?.source?.file
    if (!f) continue
    let arr = nodesByFile.get(f)
    if (!arr) {
      arr = []
      nodesByFile.set(f, arr)
    }
    arr.push(n)
  }
  for (const [source, items] of Object.entries(state.diagnosticsBySource)) {
    for (const item of items) {
      const nodeId = _resolveDiagnosticItemToNodeId(item, nodesByFile, nodeIdSet)
      if (nodeId === null) continue
      if (!out[nodeId]) out[nodeId] = []
      out[nodeId].push({ source, ...item })
    }
  }
  return out
}

export const useViewerStore = defineStore('viewer', {
  state: () => ({
    graph: null,
    status: 'idle',
    error: null,
    // Structured context for the failure page. Always carries
    // ``{ status, url }`` (HTTP code + the URL that failed) and, when
    // the hub speaks the rtl-buddy-view#130 error contract, the
    // decoded envelope: ``{ kind, model, message, logPath, logTail,
    // modelsUrl }``. ``kind`` is null against an older hub that
    // answers with a bare text/plain body — the placeholder then
    // falls back to quoting ``error``. ``null`` outside the error
    // state.
    errorMeta: null,
    // True once we have positive evidence that the page is being
    // served by (or is talking to) an rtl-buddy hub: ``GET /models``
    // answered, ``GET /view.json`` returned a #130 envelope, or the
    // /ws handshake completed. Drives the "the hub IS reachable —
    // this just means no view has been requested" variant of the
    // drop-a-file placeholder (rtl-buddy-view#130 state 1).
    hubDetected: false,
    selection: null,
    // Currently-selected edge as ``{from, to}`` or null. Mutually
    // exclusive with ``selection`` so the sidebar shows one detail
    // panel at a time — selecting an edge clears the node, and
    // vice versa.
    selectedEdge: null,
    enabledOverlays: new Set(),
    // When set, the renderer shows only the subtree rooted at this
    // instance path. ``null`` means show the full hierarchy from
    // ``graph.top``. Descend / ascend actions mutate this.
    rootInstancePath: null,
    // Canvas view mode. ``hier`` = nested-cluster tree from the
    // producer's embedded layout. ``flow`` = SPA-derived one-level
    // block-diagram view with sibling-to-sibling connectivity
    // inferred from per-node port expressions. ``sch`` = the elkjs
    // schematic canvas, laid out in-browser from the producer's
    // ``layout.elk`` payload (#163 P2) — the only mode whose SVG the
    // SPA owns element-by-element rather than receiving as a string.
    viewMode: 'hier',
    // Explicit scope for the block-flow view (the instance whose
    // direct children are rendered). ``null`` means show
    // ``graph.top``. Kept independent of ``selection`` so clicking
    // a block in flow view selects it (populating NodeDetail)
    // without changing scope — matching hier-view's click =
    // focus, button = navigate contract.
    flowScope: null,
    // Instance paths whose compound block is folded shut on the
    // schematic canvas (#163 P3). A Set, replaced rather than mutated
    // so Pinia sees the change; keyed by instance path so the state
    // survives every re-layout — elkjs is re-run from scratch on each
    // toggle and nothing about the laid-out graph is carried over.
    //
    // Only the schematic canvas reads it: hier's cluster tree and
    // flow's one-level expansion have their own scope vocabulary
    // (``rootInstancePath`` / ``flowScope``), and folding a block is a
    // different question from descending into it.
    schCollapsed: new Set(),
    // Top-level tab. ``hierarchy`` = the hier/flow canvas with
    // overlays. ``axi-perf`` = the dedicated AxiPerfView tab. Default
    // ``hierarchy`` so the app renders on first load even when no
    // axi-perf overlay is present.
    activeTab: 'hierarchy',
    // Bundle currently selected in the AxiPerfView detail pane, or
    // ``null`` when none is selected.
    selectedAxiBundle: null,
    // Hub-mirrored state. All written by applyHub*/applyDiagnostics
    // actions; consumers read these directly.
    hubCursorTimeFs: null,
    hubScope: null,
    diagnosticsBySource: {},
    hubError: null,
    // Per-signal last-known value sourced from ``wave_values_changed``
    // events. Keyed by ``${wave_scope}.${signal}`` so two signals with
    // the same bare name in different scopes don't collide. Values are
    // SV literal strings (e.g. ``32'hDEAD_BEEF``) — the renderer reads
    // them verbatim. No clear-on-omit: a wave_values_changed event
    // carrying an empty array (or omitting a signal) leaves prior
    // values in place; explicit clearing happens on view-changed /
    // graph reload.
    waveValuesByKey: {},
    // The t_fs at which ``waveValuesByKey`` was last refreshed.
    // Distinct from ``hubCursorTimeFs`` because the hub may publish
    // a cursor move without (yet) any value samples.
    waveValuesTFs: null,
    // Latest ``signal_selected`` hub event payload, kept so the
    // viewer can highlight the selected signal's port on the
    // schematic. Null when nothing selected wave-side yet.
    hubSignalSelected: null,
    // Available models advertised by the hub's ``GET /models``
    // endpoint (issue rtl_buddy#174). Empty when the SPA is running
    // standalone (drag-drop / embed.py / no /models endpoint). The
    // header picker is hidden when this is empty.
    availableModels: [],
    // Whichever model is currently active on the hub. Driven by
    // ``GET /models`` at boot and ``view_changed`` events at runtime.
    // ``null`` when running standalone.
    activeModel: null,
    // Candidate instance paths when a hub ``selection_changed`` event
    // resolves to more than one match (rtl-buddy-view#55). ``null``
    // means no picker is showing. The first entry is also written to
    // ``selection`` so the canvas pans/zooms to the smallest-range
    // match — the picker just lets the user override that default.
    selectionCandidates: null,
    // "Open in marimo" launch state. Drives the AxiPerfView button's
    // spinner / disabled state. ``axiNotebookError`` is a string when
    // the last launch attempt failed (hub returned 4xx/5xx, or the
    // network rejected); the UI shows it as an inline toast and the
    // user can retry.
    axiNotebookLaunching: false,
    axiNotebookError: null,
    // Time window pushed by a marimo brush selection (Phase 3 of
    // axi-profiler#16). ``{t_start_fs, t_end_fs}`` or null. Driven
    // by the event-sync composable's ``time-window`` topic; the SPA
    // surfaces it as an AxiPerfView header chip but never filters
    // its aggregate stats — those are pre-computed, the chip is
    // purely informational ("the notebook is currently focused on
    // this window").
    axiPerfTimeWindow: null,
    // Available tests advertised by the hub's ``GET /tests`` endpoint
    // (rtl-buddy-view#99 / 6b). Each entry is at minimum
    // ``{ name, model, tb, tests_file }``. Empty when the SPA is
    // standalone or the hub hasn't implemented /tests yet — that's
    // how the header DUT/TB toggle stays hidden until the hub catches
    // up.
    availableTests: [],
    // Currently-active test (TB-view mode). Mutually exclusive with
    // ``activeModel`` only at the level of the rendered view: the
    // test pins both a model and a TB top, so switching to a test
    // implicitly fixes the model as well — but the SPA still records
    // the resolved model name in ``activeModel`` for the picker.
    activeTest: null,
    // Owning ``tests.yaml`` of the active test. A test name can repeat
    // across suites (``smoke`` lives in several), so the name alone is
    // ambiguous — this pins the exact entry for both the ``?test=``
    // request (sent as ``&tests_file=``) and the picker's selected-option
    // binding. ``null`` when no test is active or a deep-link gave only a
    // name.
    activeTestFile: null,
    // Current view orientation: ``'dut'`` = the rendered tree is
    // rooted at the DUT (today's only behaviour), ``'tb'`` = rooted
    // at the testbench top with the DUT called out as a subtree.
    // Drives the segmented control in ``ModelPicker.vue`` and the
    // ``?view=`` query persisted on the URL. Derived from the loaded
    // view.json's ``tb_top`` field at install time; mode switches go
    // through ``switchModel`` / ``switchTest``.
    viewModeTb: false,

    // --- live coverage (the hub's /cov.json) ---------------------------
    //
    // Distinct from the baked ``overlays.coverage`` block a producer
    // may have written into view.json: this is whatever the hub has
    // NOW, fetched once per graph load and joined to nodes by module
    // name. ``null`` means "no hub, or the hub has no coverage" —
    // the overlay entry and the NodeDetail section both hide, and
    // nothing about the standalone / embed.py experience changes.
    covData: null,
    // Loading latch: ``loadCoverage`` is idempotent per session, so a
    // model switch doesn't re-fetch and two callers can't race.
    covLoadStarted: false,
    // Tint enabled. Default ON so coverage is visible the moment the
    // data is there (matching the graph pane's auto-tick); once the
    // user has touched the checkbox their choice sticks for the rest
    // of the session, including across model switches.
    projectCoverageMode: 'none',
    projectCoverageState: null,
    projectCoverageError: '',
    projectFunctionalCoverage: { valid:false,covered:0,total:0 },
    projectCoverageInstances: [],
    covEnabled: true,
    covEnabledTouched: false,
    // Phase 7b (rtl-buddy/rtl-buddy-sch#22): which figures the phys
    // overlay paints. ``subtree`` is the default because a node on a
    // hierarchy diagram stands for its scope; ``self`` answers "what
    // does this block itself cost". Switching it repaints the overlay
    // layer only — the layout is untouched, which is the whole point
    // of keeping it out of the layout state above.
    physScope: 'subtree',
  }),
  getters: {
    nodesById: (state) => {
      if (!state.graph) return new Map()
      const m = new Map()
      for (const n of state.graph.nodes) m.set(n.id, n)
      return m
    },
    // Instance paths grouped by the module type they instantiate:
    // ``Map<module, string[]>``. The inverse of ``nodesById``'s key —
    // the hub's ``graph_focus`` targets a design node by MODULE
    // (``module:fifo``), because the knowledge graph has one node per
    // module type while this view has one per instance, so "focus
    // fifo" is a 1→N resolution the SPA has to do itself.
    //
    // Each list is sorted shallowest-first (fewest dots), then
    // lexicographically, so ``[0]`` is the least-nested instance —
    // the same "smallest range wins" default the multi-match
    // ``selection_changed`` picker applies. Sorted here rather than at
    // the call site so every consumer gets the same order.
    //
    // Recomputed when ``graph`` is replaced (a model switch), which is
    // what makes the resolution follow the CURRENTLY loaded view.
    nodeIdsByModule: (state) => {
      const m = new Map()
      if (!state.graph || !Array.isArray(state.graph.nodes)) return m
      for (const n of state.graph.nodes) {
        if (!n || typeof n.module !== 'string' || n.module.length === 0) continue
        if (typeof n.id !== 'string' || n.id.length === 0) continue
        const arr = m.get(n.module)
        if (arr) arr.push(n.id)
        else m.set(n.module, [n.id])
      }
      for (const ids of m.values()) {
        ids.sort((a, b) => {
          const da = a.split('.').length
          const db = b.split('.').length
          if (da !== db) return da - db
          return a < b ? -1 : a > b ? 1 : 0
        })
      }
      return m
    },
    // Live coverage keyed by module name:
    // ``Map<module, {line, branch, toggle, expression, cover}>``,
    // each bucket ``{found, hit, ratio}``. Empty when there is no
    // coverage payload.
    //
    // The join is per-FILE totals attributed to every module the file
    // declares, with Verilator's ``__W4`` parameterization suffixes
    // stripped to source names — see ``covData.moduleCoverage`` for
    // the rule and its approximation. Memoized by being a getter:
    // Pinia caches it until ``covData`` is replaced, so the canvas
    // can re-read it per repaint without rebuilding the map.
    covByModule: (state) => moduleCoverage(state.covData),
    // The graph the canvas actually renders. When rootInstancePath
    // is null, this is the full graph; otherwise it's the subtree
    // rooted at that node.
    displayGraph(state) {
      if (!state.graph) return null
      if (!state.rootInstancePath) return state.graph
      const rootId = state.rootInstancePath
      const prefix = rootId + '.'
      const nodes = state.graph.nodes.filter(
        (n) => n.id === rootId || n.id.startsWith(prefix),
      )
      const ids = new Set(nodes.map((n) => n.id))
      const edges = state.graph.edges.filter(
        (e) => ids.has(e.from) && ids.has(e.to),
      )
      // Drop the producer-supplied embedded DOT (state.graph.layout)
      // when descending into a subtree. That DOT was generated for
      // the FULL graph; pickDot would otherwise hand it back to
      // viz.js verbatim and the canvas would re-render the original
      // hier — overlays would still recolour by the filtered node
      // list, producing "only the subtree node has a clock fill" /
      // "the rest of the hier visually unchanged" (see fix in
      // PR #xx). graphToDot rebuilds DOT from nodes+edges, which is
      // correct for the subtree.
      const { layout: _drop, ...rest } = state.graph
      return { ...rest, top: rootId, nodes, edges }
    },
    selectedNode(state) {
      if (!state.selection) return null
      return this.nodesById.get(state.selection) || null
    },
    selectedEdgeObj(state) {
      if (!state.selectedEdge || !state.graph) return null
      const { from, to } = state.selectedEdge
      return (
        state.graph.edges.find((e) => e.from === from && e.to === to) || null
      )
    },
    // Scope shown in the block-flow view: the explicit
    // ``flowScope`` state when set, otherwise the design top. The
    // flow renderer reads this to decide *which* instance to expand
    // (showing its direct children + their interconnections).
    flowScopeId(state) {
      if (state.flowScope) return state.flowScope
      return state.graph ? state.graph.top : null
    },
    // True when the selected node has at least one child in the
    // current full graph — i.e. descending into it would show
    // something more than the node itself.
    selectedHasChildren(state) {
      if (!state.selection || !state.graph) return false
      const prefix = state.selection + '.'
      for (const n of state.graph.nodes) {
        if (n.id.startsWith(prefix)) return true
      }
      return false
    },
    overlaysPresent: (state) =>
      state.graph ? state.graph.overlays_present : [],
    // Directory every source path in this graph is shown relative to.
    // See sourcePaths.js for the heuristic (common directory prefix,
    // falling back to the top node's directory). Empty string means
    // "no useful root" and consumers show a basename. Recomputed when
    // ``graph`` is replaced, so a model switch re-anchors.
    sourceRoot(state) {
      const g = state.graph
      if (!g || !Array.isArray(g.nodes)) return ''
      const files = []
      let topFile = null
      for (const n of g.nodes) {
        const f = n?.source?.file
        if (typeof f !== 'string' || !f) continue
        files.push(f)
        if (n.id === g.top) topFile = f
      }
      return commonSourceRoot(files, topFile)
    },
    // Instance paths of every DUT anchor in the current graph,
    // derived from ``graph.dut_top`` per view-json v1.1 — the SPA's
    // DUT-boundary renderer (GraphCanvas) walks this list to stamp
    // ``data-rb-dut-anchor`` on the matching SVG groups. Empty when
    // ``dut_top`` is unset (TB-view without --top) or no node in the
    // current display matches it. Recomputed off ``displayGraph`` so
    // the anchors track the descend/ascend scope correctly: a
    // boundary outside the current rooted subtree isn't drawn.
    dutAnchorIds(state) {
      const g = this.displayGraph
      if (!g || typeof g.dut_top !== 'string' || !g.dut_top) return []
      const out = []
      for (const n of g.nodes) {
        if (n && n.module === g.dut_top) out.push(n.id)
      }
      return out
    },
    // Convenience: which mode are we in? Mirrors the rule the docs
    // pin (top == tb_top → TB; otherwise DUT). Kept off the loaded
    // graph rather than the ``viewModeTb`` flag because the flag is
    // a *request* (what the user clicked) while the graph encodes
    // *what was actually rendered* after the round-trip — the truth
    // for UI labelling.
    renderedViewMode(state) {
      const g = state.graph
      if (g && typeof g.tb_top === 'string' && g.top === g.tb_top) {
        return 'tb'
      }
      return 'dut'
    },
    diagnosticsForNode: (state) => (nodeId) => {
      // Flatten diagnostics across all sources that reference this
      // node. Producers may attach an optional ``instance_path``
      // field to each item for fast-path matching; otherwise we
      // resolve via the file+line range carried by every item per
      // the v1 protocol's ``diagnostics_set`` shape (§3).
      if (!nodeId) return []
      const byNode = _diagnosticsByNode(state)
      return byNode[nodeId] ? byNode[nodeId].slice() : []
    },
    diagnosticsByNode: (state) => _diagnosticsByNode(state),
    nodeIdForDiagnosticItem: (state) => (item) => {
      // Used by surfaces that present a clickable diagnostic (the
      // DiagnosticsPanel sidebar list, future inline hover popovers,
      // etc.). Mirrors the resolver `diagnosticsByNode` uses — same
      // priority order, same deepest-range tiebreak — so clicking a
      // sidebar item lands on the same node its on-canvas badge
      // anchors to.
      if (!state.graph || !item) return null
      const nodesByFile = new Map()
      const nodeIdSet = new Set()
      for (const n of state.graph.nodes) {
        nodeIdSet.add(n.id)
        const f = n?.source?.file
        if (!f) continue
        let arr = nodesByFile.get(f)
        if (!arr) {
          arr = []
          nodesByFile.set(f, arr)
        }
        arr.push(n)
      }
      return _resolveDiagnosticItemToNodeId(item, nodesByFile, nodeIdSet)
    },
    diagnosticsFlat: (state) => {
      const out = []
      for (const [source, items] of Object.entries(state.diagnosticsBySource)) {
        for (const item of items) out.push({ source, ...item })
      }
      return out
    },

    // --- instance-hierarchy tree ----------------------------------------
    //
    // The hierarchy panel (HierarchyTree.vue) is derived purely from
    // node ids: ``a.b.c``'s parent is the longest strict dot-prefix
    // that is ITSELF a node of the graph.
    //
    // Sparse hierarchies are the interesting case. A producer may emit
    // ``top`` and ``top.u_a.u_b`` without ``top.u_a`` (elided blackbox
    // interior, a filtered view.json, a hand-written fixture). Such an
    // orphan is re-attached to its NEAREST EXISTING ANCESTOR — here
    // ``top`` — rather than being dropped or promoted to a root; when
    // no ancestor exists at all it becomes an additional root. The
    // panel is the design's index, so nothing may go missing: a
    // slightly-flattened branch is a far smaller lie than an invisible
    // instance. The full dotted path stays on the row's ``data-node-id``
    // and title, so the flattening is visible to anyone who looks.
    //
    // Order within a level follows ``graph.nodes`` order — the
    // producer's declaration order, which is what other tools show.
    treeParentOf(state) {
      const parents = new Map()
      if (!state.graph || !Array.isArray(state.graph.nodes)) return parents
      const ids = new Set()
      for (const n of state.graph.nodes) {
        if (n && typeof n.id === 'string' && n.id) ids.add(n.id)
      }
      for (const id of ids) {
        let cur = id
        let parent = null
        for (;;) {
          const cut = cur.lastIndexOf('.')
          if (cut < 0) break
          cur = cur.slice(0, cut)
          if (ids.has(cur)) {
            parent = cur
            break
          }
        }
        parents.set(id, parent)
      }
      return parents
    },
    // ``Map<parentId, childIds[]>``. Only parents that have children
    // appear as keys.
    treeChildren(state) {
      const children = new Map()
      if (!state.graph || !Array.isArray(state.graph.nodes)) return children
      const parents = this.treeParentOf
      for (const n of state.graph.nodes) {
        if (!n || typeof n.id !== 'string' || !n.id) continue
        const p = parents.get(n.id)
        if (!p) continue
        const arr = children.get(p)
        if (arr) arr.push(n.id)
        else children.set(p, [n.id])
      }
      return children
    },
    // Ids with no parent in the node set, in graph order. Normally a
    // single entry (the design top); more when the graph is sparse.
    treeRoots(state) {
      if (!state.graph || !Array.isArray(state.graph.nodes)) return []
      const parents = this.treeParentOf
      const out = []
      for (const n of state.graph.nodes) {
        if (!n || typeof n.id !== 'string' || !n.id) continue
        if (!parents.get(n.id)) out.push(n.id)
      }
      return out
    },
    // Filter semantics for the tree's search box, as a pure function of
    // the query so it is testable without mounting anything.
    //
    // ``matches`` = nodes whose INSTANCE NAME (last path segment) or
    // MODULE name contains the query, case-insensitively. ``visible``
    // = the matches plus every ancestor of a match, because a match
    // shown without its ancestors would be a list, not a tree; the
    // panel dims the ancestors that are not themselves matches.
    treeMatch(state) {
      const nodes =
        state.graph && Array.isArray(state.graph.nodes) ? state.graph.nodes : []
      const parents = this.treeParentOf
      return (query) => {
        const q = typeof query === 'string' ? query.trim().toLowerCase() : ''
        const matches = new Set()
        if (!q) return { matches, visible: new Set() }
        for (const n of nodes) {
          if (!n || typeof n.id !== 'string' || !n.id) continue
          // The scoped name (``g[1].u_k``, rtl-buddy-sch#209), so a
          // query can pick out one generate iteration.
          const name = leafName(n, n.id)
          const module = typeof n.module === 'string' ? n.module : ''
          if (
            name.toLowerCase().includes(q) ||
            module.toLowerCase().includes(q)
          ) {
            matches.add(n.id)
          }
        }
        const visible = new Set(matches)
        for (const id of matches) {
          let p = parents.get(id) || null
          while (p && !visible.has(p)) {
            visible.add(p)
            p = parents.get(p) || null
          }
        }
        return { matches, visible }
      }
    },
  },
  actions: {
    async bootstrap() {
      const viewUrl = new URLSearchParams(window.location.search).get('view')
      if (viewUrl) await this.loadFromUrl(viewUrl)
    },

    async loadFromUrl(url) {
      this.status = 'loading'
      this.error = null
      try {
        const response = await fetch(url)
        if (!response.ok) {
          // The hub puts the actionable reason in the response body
          // (e.g. an ambiguous-test or unknown-model message). Prefer
          // it over the bare status text so the failure page is useful.
          const body = (await response.text().catch(() => '')).trim()
          // rtl-buddy-view#130: a current hub answers with a JSON
          // error envelope carrying the hier.log tail; an older one
          // with bare text. ``parseHubError`` returns null for the
          // latter and we degrade to quoting the body.
          const structured = parseHubError(body)
          if (structured) {
            this.hubDetected = true
            this._fail(structured.message, {
              status: response.status,
              url,
              ...structured,
            })
            return false
          }
          const detail = body || response.statusText || 'request failed'
          this._fail(detail, { status: response.status, url })
          return false
        }
        const text = await response.text()
        this.loadFromText(text, url)
        return this.status === 'ready'
      } catch (e) {
        // Network-level failure (hub down / restarted / CORS). No HTTP
        // status to report.
        this._fail(`Could not load RTL structure: ${e.message}`, { url })
        return false
      }
    },
    async loadFromFile(file) {
      this.status = 'loading'
      this.error = null
      try {
        const text = await file.text()
        this.loadFromText(text, file.name)
      } catch (e) {
        this._fail(`Could not read ${file.name}: ${e.message}`)
      }
    },
    loadFromText(text, source = '<input>') {
      try {
        const payload = JSON.parse(text)
        this._installGraph(payload)
      } catch (e) {
        // JSON.parse and parseViewJson both throw — surface either
        // with the source label so the toast points at the file.
        this._fail(`${source}: ${e.message}`)
      }
    },
    _installGraph(rawPayload) {
      const graph = parseViewJson(rawPayload)
      this.graph = graph
      this.status = 'ready'
      this.error = null
      this.errorMeta = null
      this.selection = null
      this.selectedEdge = null
      // Open each newly loaded design at its own top-level instance.
      const top = graph.nodes.some(node => node.id === graph.top) ? graph.top : null
      this.rootInstancePath = top
      this.flowScope = top
      this.selection = top
      // Collapse state is per-graph for the same reason: an instance
      // path from the previous design either doesn't exist here or —
      // worse — exists and names something else.
      this.schCollapsed = new Set()
      // Wave values are sampled at a t_fs of the previous design's
      // simulation — keeping them would paint stale literals next to
      // ports on the new design that happen to share a name. Reset
      // alongside the rest of the per-graph state.
      this.waveValuesByKey = {}
      this.waveValuesTFs = null
      this.hubSignalSelected = null
      // Default: every overlay the producer emitted is enabled, so
      // the user sees the full overlay decoration on first open.
      this.enabledOverlays = new Set(graph.overlays_present)
      // Live coverage is per-HUB, not per-graph: fetch it once, the
      // first time a graph lands, and keep it across model switches
      // (the join is by module name, so a different model simply
      // matches a different subset). No-ops without a hub.
    },
    _fail(message, meta = {}) {
      this.status = 'error'
      this.error = message
      this.errorMeta = meta
      this.graph = null
    },
    /**
     * Latch "there is a hub on this origin". Called by the /ws
     * handshake (useHub's ``welcome``) as well as by the HTTP paths
     * that get hub-shaped answers, so the placeholder's wording is
     * right regardless of which one wins the race.
     */

    /**
     * Re-run the request that produced the current failure page —
     * the "Retry" button on the view-generation-failed placeholder
     * (rtl-buddy-view#130). View generation is retried hub-side on a
     * cache miss, so this is the cheap way to pick up a fix (init the
     * submodules, add ``frontend: slang``) without reloading the tab
     * and losing hub state. No-op when we have no URL to retry (a
     * drag-dropped file, an embedded payload).
     */
    async retryLastLoad() {
      const url = this.errorMeta && this.errorMeta.url
      if (!url) return false
      return await this.loadFromUrl(url)
    },
    select(id) {
      this.selection = id
      this.selectedEdge = null
    },
    clearSelection() {
      this.selection = null
      this.selectedEdge = null
    },
    selectEdge(from, to) {
      // Only accept an edge that exists in the current graph;
      // refuses the synthetic port-anchor edges ``"_in_<port>" ->
      // "<child>"`` the producer DOT injects for top-port signal
      // flow (those have no corresponding ``edges[]`` entry).
      if (typeof from !== 'string' || typeof to !== 'string') return
      if (!this.graph) return
      const match = this.graph.edges.find(
        (e) => e.from === from && e.to === to,
      )
      if (!match) return
      this.selectedEdge = { from, to }
      this.selection = null
    },
    descend(id) {
      // Drill the canvas into the subtree rooted at ``id``. No-op
      // when the node has no children (the canvas would render a
      // single floating node).
      //
      // Each view mode has its own scope field — ``rootInstancePath``
      // for hier (cluster-tree subtree filter), ``flowScope`` for
      // block-flow (the expand-one-level target). We update both so
      // switching tabs lands on the same logical scope; otherwise
      // descending in hier and flipping to flow would dump the user
      // back at the design top.
      if (!id) return
      const prefix = id + '.'
      const hasChildren = this.graph?.nodes.some((n) =>
        n.id.startsWith(prefix),
      )
      if (!hasChildren) return
      this.rootInstancePath = id
      this.flowScope = id
      this.selection = id
    },
    ascend() {
      // Pop one level on both scope fields so the two view modes stay
      // in sync (see descend's note), and follow the selection up
      // too — without the selection bump the NodeDetail panel keeps
      // showing the deeper path (e.g. ``ip_dma.i_cor.i_wlg``) even
      // though the canvas is now at ``ip_dma.i_cor``.
      if (!this.rootInstancePath && !this.flowScope) return
      const cur = this.rootInstancePath || this.flowScope
      const parent = cur.replace(/\.[^.]+$/, '')
      const next = parent === cur ? null : parent || null
      this.rootInstancePath = next
      this.flowScope = next
      this.selection = next
    },
    goToTop() {
      this.rootInstancePath = null
      this.flowScope = null
      this.selection = null
    },
    setViewMode(mode) {
      if (mode === 'hier' || mode === 'flow' || mode === 'sch') {
        this.viewMode = mode
      }
    },
    /**
     * Fold a compound block shut, or unfold it (#163 P3).
     *
     * Reassigns the Set instead of mutating it: Pinia's reactivity
     * tracks the property, not the collection's internals, and a
     * silent ``add`` would leave the canvas showing the old picture —
     * the same reason ``toggleOverlay`` reassigns ``enabledOverlays``.
     */
    toggleSchCollapsed(id) {
      if (typeof id !== 'string' || id.length === 0) return
      const next = new Set(this.schCollapsed)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      this.schCollapsed = next
    },
    setSchCollapsed(id, collapsed) {
      if (typeof id !== 'string' || id.length === 0) return
      const next = new Set(this.schCollapsed)
      if (collapsed) next.add(id)
      else next.delete(id)
      this.schCollapsed = next
    },
    expandAllSch() {
      if (this.schCollapsed.size === 0) return
      this.schCollapsed = new Set()
    },


    /**
     * Set the phys overlay's self-vs-subtree scope.
     *
     * A plain setter rather than a toggle so the two radio-style
     * controls in the overlay panel are each idempotent — clicking
     * "subtree" twice must not land on "self".
     */
    setPhysScope(scope) {
      if (scope !== 'self' && scope !== 'subtree') return
      this.physScope = scope
    },

    toggleOverlay(name) {
      if (this.enabledOverlays.has(name)) {
        this.enabledOverlays.delete(name)
      } else {
        this.enabledOverlays.add(name)
      }
      // Pinia tracks deep object reactivity but Set mutations don't
      // trigger reactivity reliably across all consumers; reassign
      // to force an update.
      this.enabledOverlays = new Set(this.enabledOverlays)
    },

    // --- live coverage -------------------------------------------------

    /**
     * Fetch the hub's ``/cov.json`` once per session.
     *
     * Never throws and never surfaces an error: no hub, no coverage
     * route, a 404 or a malformed body all land on ``covData = null``,
     * which reads downstream as "this feature isn't available here".
     * That silence is deliberate — the SPA also runs from embed.py and
     * the Vite dev server, where the absence is normal, not a fault.
     */
    /** Toggle the live coverage tint, and remember that they did. */
    toggleCoverageTint() {
      this.covEnabled = !this.covEnabled
      this.covEnabledTouched = true
    },

    // --- hub-event reducers --------------------------------------------------
    //
    // Each apply* action is invoked by useHub's dispatcher on an
    // inbound envelope. Keeping them on the store (and not in the
    // composable) means tests, devtools, and SSR snapshotting all
    // see the same state — and the composable stays a thin
    // transport.





    presentSelectionCandidates(paths) {
      // Multi-match hub ``selection_changed`` — apply the smallest-
      // range default ([0]) immediately so the canvas reacts in the
      // common case, and stash the full list so the SelectionCandidates
      // popover can offer alternatives. The composable owns the
      // auto-dismiss timer.
      if (!Array.isArray(paths) || paths.length === 0) return
      const filtered = paths.filter(
        (p) => typeof p === 'string' && p.length > 0,
      )
      if (filtered.length === 0) return
      this.selection = filtered[0]
      this.selectedEdge = null
      // Single match → no picker. Acts as the array-collapsing case
      // for callers that don't pre-check (useHub does pre-check, but
      // tests / future callers may not).
      this.selectionCandidates = filtered.length > 1 ? filtered.slice() : null
    },

    chooseSelectionCandidate(path) {
      // User picked one of the multi-match candidates from the popover.
      // Lock the selection and dismiss the picker. The hub broadcast
      // is the composable's job (useHub.chooseSelectionCandidate sends
      // ``selection_changed`` from origin=view); the store just owns
      // the local-side state transition.
      if (typeof path !== 'string' || path.length === 0) return
      this.selection = path
      this.selectedEdge = null
      this.selectionCandidates = null
    },

    dismissSelectionCandidates() {
      this.selectionCandidates = null
    },



    applyDiagnostics(source, items) {
      // Latest-writer-wins per source. Empty `items` clears the
      // source so a producer can withdraw findings (e.g. a re-run
      // of `rb cdc` returns clean).
      if (typeof source !== 'string' || source.length === 0) return
      const next = { ...this.diagnosticsBySource }
      if (Array.isArray(items) && items.length > 0) {
        next[source] = items.slice()
      } else {
        delete next[source]
      }
      this.diagnosticsBySource = next
    },





    applyWaveValues(payload) {
      // ``payload`` is the ``wave_values_changed`` envelope payload:
      // ``{ t_fs, values: [{wave_scope, signal, value}] }``. Merge
      // into ``waveValuesByKey`` rather than replace — the producer
      // is permitted to emit deltas (only the signals that changed
      // at this sample), so wiping the map on every event would
      // strip values the renderer was about to redraw.
      if (!payload || typeof payload !== 'object') return
      const t = payload.t_fs
      const values = Array.isArray(payload.values) ? payload.values : []
      if (typeof t === 'string') this.waveValuesTFs = t
      if (values.length === 0) return
      const next = { ...this.waveValuesByKey }
      for (const v of values) {
        if (
          !v ||
          typeof v.wave_scope !== 'string' ||
          typeof v.signal !== 'string' ||
          typeof v.value !== 'string'
        ) continue
        next[`${v.wave_scope}.${v.signal}`] = v.value
      }
      this.waveValuesByKey = next
      // First wave-values envelope ever → auto-enable the wave
      // overlay. The producer's ``view.json`` doesn't have to list
      // ``wave`` in ``overlays_present`` for the live cascade to
      // paint — receiving values is itself the signal that the user
      // wants the badges visible. Idempotent: re-arming the live
      // path doesn't reset a user-disabled overlay because we only
      // flip the flag the first time the map crosses from empty to
      // non-empty.
      if (!this.enabledOverlays.has('wave')) {
        this.enabledOverlays = new Set(this.enabledOverlays).add('wave')
      }
    },

    setOverlayEnabled(name, enabled) {
      // Reactive helper used by both the local OverlayPanel and the
      // remote ``view_overlay_set`` request. Reassigning the Set is
      // what the canvas watcher hooks onto — mutating in place would
      // not retrigger Pinia's reactivity for ``Set`` consumers.
      if (typeof name !== 'string' || name.length === 0) return
      const next = new Set(this.enabledOverlays)
      if (enabled) {
        next.add(name)
      } else {
        next.delete(name)
      }
      this.enabledOverlays = next
    },

    applySignalSelected(payload) {
      if (
        payload &&
        typeof payload === 'object' &&
        typeof payload.signal === 'string' &&
        typeof payload.wave_scope === 'string'
      ) {
        this.hubSignalSelected = {
          signal: payload.signal,
          wave_scope: payload.wave_scope,
        }
      } else {
        this.hubSignalSelected = null
      }
    },

    resetWaveValues() {
      this.waveValuesByKey = {}
      this.waveValuesTFs = null
      this.hubSignalSelected = null
    },
  },
})

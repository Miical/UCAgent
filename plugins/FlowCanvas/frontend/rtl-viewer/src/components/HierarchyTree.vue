<template>
  <div class="hierarchy-tree">
    <div class="tree-filter">
      <input
        ref="filterEl"
        type="text"
        class="tree-filter-input"
        placeholder="筛选模块实例…（/）"
        aria-label="筛选模块实例"
        v-model="query"
        @keydown.enter.prevent="selectFirstMatch"
        @keydown.esc.prevent.stop="onFilterEscape"
        @keydown.down.prevent="enterTreeFromFilter"
      />
      <!-- ``2 / 6`` while filtering, a bare instance count otherwise —
           "6 / 6" on an unfiltered tree is a ratio that answers a
           question nobody asked. -->
      <span class="tree-count" :data-filtering="filtering ? 'true' : 'false'">
        <template v-if="filtering">{{ matchCount }} / {{ totalCount }}</template>
        <template v-else>{{ totalCount }}</template>
      </span>
    </div>
    <div
      ref="scrollEl"
      class="tree-scroll"
      role="tree"
      aria-label="模块实例层级"
      tabindex="0"
      :aria-activedescendant="cursorId ? rowDomId(cursorId) : undefined"
      @keydown="onKeydown"
    >
      <!-- ``data-inst``, deliberately NOT ``data-node-id``: the latter
           is the canvas's stamp on SVG groups and several document-wide
           queries (overlay badge lookups, the capture module) resolve
           it with ``document.querySelector`` — a sidebar row wearing
           the same attribute would shadow the node the caller meant. -->
      <div
        v-for="row in rows"
        :key="row.id"
        :id="rowDomId(row.id)"
        class="tree-row"
        role="treeitem"
        :aria-level="row.depth + 1"
        :aria-selected="row.id === store.selection ? 'true' : 'false'"
        :aria-expanded="row.hasChildren ? String(row.open) : undefined"
        :data-inst="row.id"
        :data-dimmed="row.dimmed ? 'true' : 'false'"
        :class="{
          'is-selected': row.id === store.selection,
          'is-cursor': row.id === cursorId,
          'is-scope': row.id === store.rootInstancePath,
          'is-dimmed': row.dimmed,
        }"
        :title="row.id"
        :style="{ paddingLeft: 0.25 + row.depth * 0.7 + 'rem' }"
        @click="onRowClick(row)"
        @dblclick="onRowDblClick(row)"
      >
        <button
          v-if="row.hasChildren"
          type="button"
          class="tree-caret"
          :aria-label="(row.open ? '折叠 ' : '展开 ') + row.id"
          :title="row.open ? '折叠' : '展开'"
          tabindex="-1"
          @click.stop="toggle(row.id)"
        >{{ row.open ? '▾' : '▸' }}</button>
        <span v-else class="tree-caret tree-caret-leaf" aria-hidden="true"></span>
        <span class="tree-inst">{{ row.name }}</span>
        <span class="tree-module">{{ row.module }}</span>
        <button class="tree-ai-explain" type="button" :aria-label="'AI 解释设计 '+row.module" title="AI 解释设计" @mouseenter="explainDesign(row,$event,'preview')" @mouseleave="explainDesign(row,$event,'leave')" @click.stop="explainDesign(row,$event,'pin')" @dblclick.stop @keydown.stop><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M13 4H5a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-6M7 9h4M7 13h8M7 17h5"/><path class="design-ai-spark" d="m18 1 1.4 4.6L24 7l-4.6 1.4L18 13l-1.4-4.6L12 7l4.6-1.4Z"/></svg></button>
        <span
          v-if="row.id === store.rootInstancePath"
          class="tree-scope-mark"
          title="当前画布范围"
        >⌂</span>
      </div>
      <p v-if="rows.length === 0" class="tree-empty">
        {{ filtering ? '没有匹配的模块或实例。' : '当前视图没有模块实例。' }}
      </p>
    </div>
  </div>
</template>

<script setup>
/** Ask the host to compose a task using this exact instance and source location. */
function explainDesign(row,event,action){
  const rect=event.currentTarget.getBoundingClientRect(),node=store.nodesById.get(row.id)
  // Pinia source records are reactive proxies; postMessage cannot clone them.
  const source=node?.source ? JSON.parse(JSON.stringify(node.source)) : null
  window.parent.postMessage({type:'flowcanvas-explain-design',action,instance:row.id,module:row.module,source,rect:{left:rect.left,right:rect.right,top:rect.top,bottom:rect.bottom}},location.origin)
}

// The instance-hierarchy outline — the tree every commercial viewer
// is built around, and the SPA's biggest missing affordance.
//
// The tree itself is store-derived (``treeChildren`` / ``treeRoots`` /
// ``treeMatch``); this component owns only the three pieces of *view*
// state a tree needs — which rows are expanded, what is typed in the
// filter, and where the keyboard cursor is.
//
// Two contracts worth stating, because they are what makes the panel
// feel like part of the canvas rather than a second app:
//
//   1. A row click goes through the SAME path a canvas click does —
//      cross-origin ``selection_changed`` broadcast fires either way.
//      A row double-click mirrors the canvas double-click
//      (``select`` + ``descend``, a no-op on leaves).
//   2. Selection is followed, never owned. Whatever moves
//      ``store.selection`` — a canvas click, a hub event, the
//      disambiguation picker — expands this tree's ancestors, moves
//      the cursor, and scrolls the row into view.

import { computed, nextTick, ref, watch } from 'vue'
import { useViewerStore } from '../store.js'
import { leafName } from '../instancePath.js'

const store = useViewerStore()

const query = ref('')
const cursorId = ref(null)
const filterEl = ref(null)
const scrollEl = ref(null)

// Expanded ids. Default: the roots only, i.e. the top and its direct
// children are on screen — deep designs would otherwise open as a wall
// of text. Survives selection changes (it is only ever ADDED to when
// the selection moves), and is rebuilt from scratch on a model switch.
const expanded = ref(new Set())

function defaultExpansion() {
  return new Set(store.treeRoots)
}
expanded.value = defaultExpansion()

const filtering = computed(() => query.value.trim().length > 0)
const filterResult = computed(() =>
  filtering.value ? store.treeMatch(query.value) : null,
)
const totalCount = computed(() => store.graph?.nodes?.length ?? 0)
const matchCount = computed(() => filterResult.value?.matches.size ?? 0)


// Flatten the tree into the visible rows, in DFS order. While
// filtering, "visible" is decided by the match set (ancestors are
// force-opened so every match is reachable) rather than by the
// expansion state — the point of typing is not to then have to click.
const rows = computed(() => {
  const out = []
  const children = store.treeChildren
  const byId = store.nodesById
  const result = filterResult.value
  const walk = (id, depth) => {
    const kids = children.get(id) || []
    const hasChildren = kids.length > 0
    const shown = result
      ? kids.filter((k) => result.visible.has(k))
      : kids
    const open = result ? shown.length > 0 : expanded.value.has(id)
    out.push({
      id,
      depth,
      // ``g[0].u_s``, not ``u_s``: both iterations of a generate loop
      // share the bare name (rtl-buddy-sch#209).
      name: leafName(byId.get(id), id),
      module: byId.get(id)?.module ?? '',
      hasChildren,
      open,
      dimmed: !!result && !result.matches.has(id),
    })
    if (!open) return
    for (const k of shown) walk(k, depth + 1)
  }
  for (const root of store.treeRoots) {
    if (result && !result.visible.has(root)) continue
    walk(root, 0)
  }
  return out
})

// DOM ids for aria-activedescendant. Instance paths contain dots and
// may contain characters that are awkward in an id; keep it to a
// conservative alphabet.
function rowDomId(id) {
  return 'rb-tree-' + String(id).replace(/[^A-Za-z0-9_-]/g, '_')
}

function expand(id) {
  const next = new Set(expanded.value)
  next.add(id)
  expanded.value = next
}

function collapse(id) {
  const next = new Set(expanded.value)
  next.delete(id)
  expanded.value = next
}

function toggle(id) {
  if (expanded.value.has(id)) collapse(id)
  else expand(id)
}

// --- selection ---------------------------------------------------------

function selectRow(id) {
  store.select(id)
  const node = store.nodesById.get(id)
}

function onRowClick(row) {
  cursorId.value = row.id
  selectRow(row.id)
}

function onRowDblClick(row) {
  // The browser has already delivered the first click (hence the
  // selection + broadcast); this only adds the descend, exactly as the
  // canvas's dblclick handler does. ``descend`` no-ops on a leaf.
  store.select(row.id)
  store.descend(row.id)
}

function selectFirstMatch() {
  const first = rows.value.find((r) => !r.dimmed)
  if (!first) return
  cursorId.value = first.id
  selectRow(first.id)
}

function onFilterEscape() {
  // Esc inside the filter clears the filter and stops there — it does
  // NOT fall through to the global Esc (clear selection / dismiss the
  // picker). The global handler already ignores keystrokes whose
  // target is a form control, so "stops there" costs nothing extra;
  // a second Esc on an empty box blurs, which hands the key back.
  if (filtering.value) {
    query.value = ''
    return
  }
  filterEl.value?.blur()
}

function enterTreeFromFilter() {
  const first = rows.value[0]
  if (!first) return
  cursorId.value = cursorId.value || first.id
  scrollEl.value?.focus()
}

// --- keyboard ----------------------------------------------------------

function scrollRowIntoView(id) {
  // Query by the sanitised DOM id rather than the raw instance path:
  // ``rowDomId`` is already restricted to a selector-safe alphabet, so
  // this needs no CSS.escape (which happy-dom does not implement).
  const el = scrollEl.value?.querySelector('#' + rowDomId(id))
  // happy-dom has no layout, so scrollIntoView may be absent.
  if (el && typeof el.scrollIntoView === 'function') {
    el.scrollIntoView({ block: 'nearest' })
  }
}

function onKeydown(event) {
  if (event.ctrlKey || event.metaKey || event.altKey) return
  const list = rows.value
  if (list.length === 0) return
  const idx = list.findIndex((r) => r.id === cursorId.value)
  const row = idx >= 0 ? list[idx] : null
  switch (event.key) {
    case 'ArrowDown':
      cursorId.value = list[idx < 0 ? 0 : Math.min(list.length - 1, idx + 1)].id
      break
    case 'ArrowUp':
      cursorId.value = list[idx < 0 ? 0 : Math.max(0, idx - 1)].id
      break
    case 'ArrowRight': {
      if (!row) {
        cursorId.value = list[0].id
        break
      }
      // Collapsed with children → open it. Already open → step to the
      // first child. While filtering the open state is derived, so the
      // key only ever moves.
      if (!filtering.value && row.hasChildren && !row.open) {
        expand(row.id)
      } else if (idx + 1 < list.length && list[idx + 1].depth > row.depth) {
        cursorId.value = list[idx + 1].id
      }
      break
    }
    case 'ArrowLeft': {
      if (!row) {
        cursorId.value = list[0].id
        break
      }
      if (!filtering.value && row.open && row.hasChildren) {
        collapse(row.id)
      } else {
        const parent = store.treeParentOf.get(row.id)
        if (parent) cursorId.value = parent
      }
      break
    }
    case 'Enter':
      if (row) selectRow(row.id)
      break
    case 'Home':
      cursorId.value = list[0].id
      break
    case 'End':
      cursorId.value = list[list.length - 1].id
      break
    default:
      return
  }
  event.preventDefault()
  if (cursorId.value) nextTick(() => scrollRowIntoView(cursorId.value))
}

// --- reactions ---------------------------------------------------------

// Follow the selection from ANY source: expand its ancestors, park the
// cursor on it, scroll it into view.
watch(
  () => store.selection,
  (id) => {
    if (!id) return
    const parents = store.treeParentOf
    const next = new Set(expanded.value)
    let p = parents.get(id) || null
    while (p) {
      next.add(p)
      p = parents.get(p) || null
    }
    expanded.value = next
    cursorId.value = id
    nextTick(() => scrollRowIntoView(id))
  },
)

// Descending scopes the canvas to a subtree; the tree opens that node
// so the panel shows what the canvas is now showing.
watch(
  () => store.rootInstancePath,
  (id) => {
    if (id) expand(id)
  },
)

// A model switch replaces the graph wholesale: the old expansion set
// names instances that no longer exist, and a filter typed for the
// previous design is noise. Reset all three.
watch(
  () => store.graph,
  () => {
    expanded.value = defaultExpansion()
    query.value = ''
    cursorId.value = null
  },
)

/** Focus the filter box (the ``/`` shortcut's landing point). */
function focusFilter() {
  const el = filterEl.value
  if (!el || typeof el.focus !== 'function') return false
  el.focus()
  if (typeof el.select === 'function') el.select()
  return true
}

defineExpose({ focusFilter })
</script>

<style scoped>
.hierarchy-tree {
  display: flex;
  flex-direction: column;
  min-height: 0;
}

/* -- filter ---------------------------------------------------------- */
.tree-filter {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  padding: 0.15rem 0.25rem 0.35rem;
}
.tree-filter-input {
  flex: 1 1 auto;
  min-width: 0;
  font-family: var(--font-mono);
  font-size: var(--fs-small);
  padding: 0.2rem 0.35rem;
  color: var(--fg);
  background: var(--panel-2);
  border: 1px solid var(--line-strong);
  border-radius: var(--radius-2);
}
.tree-filter-input:focus {
  outline: none;
  border-color: var(--accent);
}
.tree-count {
  flex: 0 0 auto;
  font-family: var(--font-mono);
  font-size: var(--fs-small);
  color: var(--fg-muted);
  font-variant-numeric: tabular-nums;
}
.tree-count[data-filtering='true'] {
  color: var(--accent);
}

/* -- rows ------------------------------------------------------------ */
/* The tree is the one sidebar panel that can be arbitrarily tall, so it
   scrolls inside a capped box — Node detail has to stay reachable
   without scrolling past a thousand instances. */
.tree-empty {
  margin: 0.4rem 0.5rem;
  font-size: var(--fs-small);
  color: var(--fg-muted);
}
</style>

<style scoped src="./hierarchy-rows.css"></style>

<style scoped>
.tree-row{position:relative;padding-right:25px}
.tree-ai-explain{position:absolute;right:4px;top:50%;transform:translateY(-50%);width:16px;height:16px;padding:0;border:0;border-radius:3px;background:transparent;color:var(--fg-muted);opacity:0;cursor:pointer;transition:opacity .15s}
.tree-ai-explain svg{display:block;width:16px;height:16px;fill:none;stroke:currentColor;stroke-width:1.5;stroke-linecap:round;stroke-linejoin:round;overflow:visible}
.tree-ai-explain .design-ai-spark{fill:#9466e8;stroke:#6e83f4;stroke-width:.7;transform-box:fill-box;transform-origin:center;transition:transform .25s}
.tree-row:hover .tree-ai-explain,.tree-row:focus-within .tree-ai-explain{opacity:1}
.tree-ai-explain:hover{color:var(--accent)}
.tree-ai-explain:hover .design-ai-spark{transform:rotate(10deg) scale(1.1)}
@media(hover:none){.tree-ai-explain{opacity:1}}
@media(prefers-reduced-motion:reduce){.tree-ai-explain,.tree-ai-explain .design-ai-spark{transition:none}}
</style>

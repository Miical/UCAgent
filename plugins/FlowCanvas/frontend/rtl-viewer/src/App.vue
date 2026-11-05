<template>
  <div class="app">
    <!-- Keep the body mounted across status transitions so a model
         switch (status='ready' → 'loading' → 'ready') doesn't tear
         down the canvas + sidebar mid-flight. The loading overlay
         renders on top instead, giving the user clear "something is
         happening" feedback while preserving the previous view as a
         visual anchor. -->
    <div v-if="coverageOnly && store.graph" class="coverage-detail-page"><OverlayPanel /></div>
    <div
      class="app-body"
      v-else-if="store.graph"
    >
      <div class="canvas-wrap">
        <!-- One canvas slot, three modes. The schematic is a *mode*,
             not a new hub pane: it draws the same design from the same
             view.json and shares the selection, so a second origin
             would only make two panes evict each other (AGENTS.md
             § hub protocol). The mode tabs live on the canvas itself
             — both components render the same strip. -->
        <SchematicCanvas v-if="store.viewMode === 'sch'" />
        <GraphCanvas v-else />
        <div v-if="store.status === 'loading'" class="loading-overlay">
          <div class="spinner" aria-hidden="true"></div>
          <span>加载中…</span>
        </div>
      </div>
      <div
        class="sidebar-resizer"
        role="separator"
        aria-orientation="horizontal"
        title="拖动调整高度"
        @mousedown.prevent="startResize"
      ></div>
      <aside class="sidebar" :style="{ height: panelHeight + 'px' }">
        <!-- The hierarchy outline goes FIRST: it is the design's index
             and the thing every other panel is about. Verdi/Vivado both
             put it here for the same reason. -->
        <CollapsiblePanel title="层级结构" persist-key="hierarchy">
          <HierarchyTree ref="treeRef" />
        </CollapsiblePanel>
        <CollapsiblePanel
          v-if="!store.selectedEdgeObj"
          title="模块详情"
          persist-key="node-detail"
        >
          <NodeDetail />
        </CollapsiblePanel>
        <CollapsiblePanel
          v-else
          title="连线详情"
          persist-key="edge-detail"
          :badge="'●'"
        >
          <EdgeDetail />
        </CollapsiblePanel>
      </aside>
    </div>
    <div class="loading" v-else-if="store.status === 'loading'">
      <div class="spinner" aria-hidden="true"></div>
      <span>加载中…</span>
    </div>
    <!-- ``idle`` and ``error`` both mean "there is no view on screen";
         the placeholder decides WHICH explanation to give from the
         store's decoded hub-error envelope (rtl-buddy-view#130). -->
    <NoViewPlaceholder v-else />
  </div>
</template>

<script setup>
// Top-level App shell. The state machine is owned by the Pinia
// store; this component is a presentational switch over
// store.status (``idle`` / ``loading`` / ``ready`` / ``error``).
//
// Drag-and-drop is wired at the document level so users can drop
// onto any part of the viewer without targeting a specific zone.

import { onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { useViewerStore } from './store.js'
import GraphCanvas from './components/GraphCanvas.vue'
import SchematicCanvas from './components/SchematicCanvas.vue'
import OverlayPanel from './components/OverlayPanel.vue'
import NodeDetail from './components/NodeDetail.vue'
import EdgeDetail from './components/EdgeDetail.vue'
import NoViewPlaceholder from './components/NoViewPlaceholder.vue'
import CollapsiblePanel from './components/CollapsiblePanel.vue'
import HierarchyTree from './components/HierarchyTree.vue'
import { initTheme } from './theme.js'
import { makeGlobalKeydownHandler } from './keyboard.js'

import { useProjectCoverage } from './composables/useProjectCoverage.js'
const store = useViewerStore()
useProjectCoverage(store)
const coverageOnly = new URLSearchParams(location.search).get('coverageOnly') === '1'
let requestedInstance = new URLSearchParams(location.search).get('focus')
function focusCoverage() {
  if(coverageOnly && store.graph?.nodes.some(n=>n.id===requestedInstance))store.select(requestedInstance)
}
function onCoverageMessage(event) {
  if(event.origin!==location.origin || event.source!==window.parent || event.data?.type!=='flowcanvas-coverage-focus')return
  requestedInstance=event.data.instance;focusCoverage()
}
watch(()=>store.graph,focusCoverage)
onMounted(()=>window.addEventListener('message',onCoverageMessage))
onBeforeUnmount(()=>window.removeEventListener('message',onCoverageMessage))


// Resizable details panel below the drawing.
function loadPanelHeight() {
  try {
    const value = Number(localStorage.getItem('fc-rtl-panel-height'))
    if (value >= 100 && value <= 600) return value
  } catch {}
  return 220
}
const panelHeight = ref(loadPanelHeight())
function startResize(e) {
  const startY = e.clientY
  const startH = panelHeight.value
  const onMove = (ev) => {
    const height = startH - (ev.clientY - startY)
    panelHeight.value = Math.min(Math.max(100, window.innerHeight - 105), Math.max(100, height))
  }
  const onUp = () => {
    document.removeEventListener('mousemove', onMove)
    document.removeEventListener('mouseup', onUp)
    document.body.style.cursor = ''
    document.body.style.userSelect = ''
    try {
      localStorage.setItem('fc-rtl-panel-height', String(panelHeight.value))
    } catch {
      /* best-effort persistence */
    }
  }
  document.addEventListener('mousemove', onMove)
  document.addEventListener('mouseup', onUp)
  document.body.style.cursor = 'row-resize'
  document.body.style.userSelect = 'none'
}



// App switcher (top-bar right). Empty unless the hub is serving us —
// off the hub, ``/`` and ``/gph`` are not ours to link to.

function onDragOver(e) {
  e.preventDefault()
}

function onDrop(e) {
  e.preventDefault()
  const file = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]
  if (file) store.loadFromFile(file)
}

// Global keyboard shortcuts (Esc / u / "/"). The behaviour lives in
// keyboard.js so it can be unit-tested against a bare KeyboardEvent;
// App.vue owns nothing but the listener's lifetime and the one DOM
// handle the shortcuts need (the hierarchy filter box).
const treeRef = ref(null)
const onGlobalKeydown = makeGlobalKeydownHandler({
  store,
  focusTreeFilter: () => treeRef.value?.focusFilter?.() ?? false,
})

onMounted(() => {
  document.addEventListener('dragover', onDragOver)
  document.addEventListener('drop', onDrop)
  if (!coverageOnly) document.addEventListener('keydown', onGlobalKeydown)
  // Watch for OS colour-scheme flips and ``data-theme`` pins so the
  // canvas — whose colours are baked, not inherited — redraws.
  initTheme()

})
onBeforeUnmount(() => {
  document.removeEventListener('dragover', onDragOver)
  document.removeEventListener('drop', onDrop)
  document.removeEventListener('keydown', onGlobalKeydown)
})
</script>

<style>
/* App shell. Global (unscoped) because these class names are the
   chrome contract every hub app implements, and because the layout
   numbers (--header-h, --status-h, --sidebar-w) have to reach the
   children. Colours, radii and type come from tokens.css + the
   vendored hub sheet; the document base lives in app.css. */

/* -- top bar: identity left / controls centre / switcher right ------- */
.app-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  height: var(--header-h);
  box-sizing: border-box;
  padding: 0 1rem;
  background: var(--panel);
  border-bottom: 1px solid var(--line);
}
.app-identity,
.app-controls,
.app-switcher {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  min-width: 0;
}
.app-identity { flex: 0 1 auto; }
.app-controls { flex: 1 1 auto; justify-content: center; }
.app-switcher { flex: 0 0 auto; }
/* The one piece of brand art in the SPA chrome: an 18px chip beside
   the wordmark. Minimal by decree — no hero, no watermark. */
.app-logo {
  display: block;
  flex-shrink: 0;
  image-rendering: -webkit-optimize-contrast;
}
.app-header h1 {
  font-size: var(--fs-head);
  margin: 0;
  font-weight: 600;
  white-space: nowrap;
}
.design-name {
  font-family: var(--font-mono);
  color: var(--fg-muted);
  font-size: var(--fs-small);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.app-tabs { display: flex; gap: 0; }
.app-tabs button {
  background: transparent;
  border: 1px solid transparent;
  border-bottom: 2px solid transparent;
  padding: 0.35rem 0.75rem;
  font-size: 0.85rem;
  color: var(--fg-muted);
  cursor: pointer;
  white-space: nowrap;
}
.app-tabs button:hover:not(:disabled) {
  color: var(--fg);
}
/* One accent family: the tab underline was the SPA's indigo. */
.app-tabs button.active {
  color: var(--accent);
  border-bottom-color: var(--accent);
  font-weight: 600;
}
/* app.css owns ``button:disabled``; the tab only drops the border it
   would otherwise keep from the shared rule. */
.app-tabs button:disabled {
  background: transparent;
  border-color: transparent;
}
/* App switcher: ⌂ hub back to the landing, then the sibling apps the
   hub says it has data for. Rendered only under the hub.

   Byte-for-byte the panes' ``nav.switcher a`` rule (rtl_buddy
   hub/graph_page.html, hub/cov_page.html): --panel-2 fill, --fg text,
   a --line-strong hairline, --radius-2, .25rem/.55rem padding, accent
   on hover. The three apps sit next to each other in a tab strip, so
   a switcher that looks different in one of them reads as a different
   control. The theme toggle rides along — it is the same size of
   button in the same group. */
.switch-link,
.theme-toggle {
  font-family: var(--font-sans);
  font-size: var(--fs-small);
  padding: 0.25rem 0.55rem;
  border: 1px solid var(--line-strong);
  border-radius: var(--radius-2);
  background: var(--panel-2);
  color: var(--fg);
  text-decoration: none;
  cursor: pointer;
  white-space: nowrap;
}
.switch-link:hover,
.theme-toggle:hover {
  color: var(--accent);
  border-color: var(--accent);
}

.coverage-detail-page{height:100vh;overflow:auto;background:var(--panel);padding:0;box-sizing:border-box}

/* -- body ------------------------------------------------------------ */
/* Header and status strip are both fixed-height, so the body is what
   is left. Both numbers are tokens (tests/tokens.spec.js keeps them in
   step with layout/constants.js) — they used to be a magic 48. */
.app-body { display: flex; flex-direction: column; height: 100vh; overflow: hidden; }
.app-body.axi-tab { display: block; overflow: auto; background: var(--panel); }
.sidebar {
  width: 100%;
  max-height: min(72.5vh, calc(100vh - 105px));
  flex-shrink: 0;      /* honour the width in the flex row (don't squeeze) */
  border-top: 1px solid var(--line);
  padding: 0;
  overflow: auto;
  background: var(--panel);
}
/* Draggable divider between the sidebar and the canvas. */
.sidebar-resizer {
  flex: 0 0 5px;
  cursor: row-resize;
  background: transparent;
  margin-bottom: -1px;
  z-index: 2;
}
.sidebar-resizer:hover,
.sidebar-resizer:active {
  background: var(--accent);
}
.canvas-wrap {
  flex: 1;
  min-height: 0;
  min-width: 0;
  overflow: hidden;
  position: relative;
  display: flex;
}
.loading-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 0.6rem;
  /* Scrim over the previous view. ``--bg`` at 78% so it dims in dark
     as well; a baked rgba(248,250,252,…) was a white veil there. */
  background: color-mix(in srgb, var(--bg) 78%, transparent);
  backdrop-filter: blur(2px);
  font-size: 0.85rem;
  color: var(--fg-muted);
  z-index: 20;
  pointer-events: all;
}
.spinner {
  width: 28px;
  height: 28px;
  border: 3px solid var(--line-strong);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: rb-spin 0.8s linear infinite;
}
@keyframes rb-spin {
  to { transform: rotate(360deg); }
}
.empty-state, .loading, .error {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 0.6rem;
  height: calc(100vh - var(--header-h) - var(--status-h));
  text-align: center;
  padding: 2rem;
  /* content-box would add the 2rem padding on TOP of the calc and push
     the status strip below the fold — the strip is the one piece of
     chrome that has to stay reachable. */
  box-sizing: border-box;
}
.empty-state .empty-hint {
  font-size: 0.85rem;
  color: var(--fg-muted);
  max-width: 60ch;
  line-height: 1.5;
  background: var(--panel-2);
  padding: 0.75rem 1rem;
  border-radius: var(--radius-3);
}
.empty-state code, .empty-hint code {
  background: var(--line);
  padding: 0.05rem 0.3rem;
  border-radius: var(--radius-1);
  font-size: 0.85em;
}
.error pre {
  max-width: 60ch;
  white-space: pre-wrap;
  background: var(--err-bg);
  border: 1px solid var(--err);
  padding: 0.75rem;
  border-radius: var(--radius-2);
  color: var(--err);
}
.error-status {
  color: var(--err);
  font-weight: 600;
  margin: 0;
}
.error-reasons {
  max-width: 64ch;
  text-align: left;
  font-size: 0.85rem;
  color: var(--fg-muted);
  background: var(--panel-2);
  padding: 0.75rem 1rem;
  border-radius: var(--radius-3);
  line-height: 1.5;
}
.error-reasons-title {
  margin: 0 0 0.4rem;
  font-weight: 600;
}
.error-reasons ul {
  margin: 0;
  padding-left: 1.2rem;
}
.error-reasons li {
  margin: 0.2rem 0;
}
.error-reasons .error-hint {
  margin: 0.6rem 0 0;
  font-style: italic;
}
.error code {
  background: var(--line);
  padding: 0.05rem 0.3rem;
  border-radius: var(--radius-1);
  font-size: 0.85em;
}

/* -- bottom status strip --------------------------------------------- */
/* The second half of the chrome contract. Fixed height so the body's
   calc() is exact; HubStatus lays out the three slots inside it. */
.app-status-strip {
  height: var(--status-h);
  box-sizing: border-box;
  display: flex;
  align-items: center;
  padding: 0 0.75rem;
  background: var(--panel);
  border-top: 1px solid var(--line);
  font-size: var(--fs-small);
}
</style>

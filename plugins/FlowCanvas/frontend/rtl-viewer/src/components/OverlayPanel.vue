<template>
  <section class="overlay-panel">
    <div v-if="coverageUrl && store.selectedNode" class="project-line-coverage">
      <div class="line-coverage-heading" :title="projectCoverage?.source_matches === false ? '数据来源：远端构建（与本地 RTL 版本不同）' : '当前构建累计覆盖率'"><span>行覆盖率</span><span>{{ projectCoverage?.runs?.length || 0 }} 次累计</span></div>
      <div v-if="coverageRows.length" class="line-coverage-list tree-scroll">
        <div class="line-coverage-columns"><span>模块实例</span><span>含子模块</span><span>自身</span></div>
        <div v-for="row in visibleCoverageRows" :key="row.path" class="tree-row coverage-tree-row" :style="{ paddingLeft: 0.25 + row.depth * 0.7 + 'rem' }" :class="{ 'is-selected': row.path === store.selectedNode.id, 'has-children': row.hasChildren }" :role="row.hasChildren ? 'button' : undefined" :tabindex="row.hasChildren ? 0 : undefined" :aria-expanded="row.hasChildren ? !collapsedCoverage.has(row.path) : undefined" @click="scheduleToggle(row)" @dblclick.stop="openSource(row)" @keydown.enter.prevent="toggleCoverageRow(row)" @keydown.space.prevent="toggleCoverageRow(row)">
          <button v-if="row.hasChildren" type="button" class="tree-caret" :aria-label="(collapsedCoverage.has(row.path) ? 'Expand ' : 'Collapse ') + row.path" @click.stop="toggleCoverageRow(row)" @dblclick.stop>{{ collapsedCoverage.has(row.path) ? '▸' : '▾' }}</button>
          <span v-else class="tree-caret tree-caret-leaf" aria-hidden="true"></span>
          <span class="tree-inst" :title="row.path">{{ row.path.split('.').at(-1) }}</span>
          <span class="tree-module" :title="row.module">{{ row.module }}</span>
          <span class="coverage-tree-values">
          <span class="line-coverage-score" :title="row.subtree.covered + '/' + row.subtree.total + ' 覆盖点'">{{ percentage(row.subtree) }} <small>{{ row.subtree.covered }}/{{ row.subtree.total }}</small></span>
          <span class="line-coverage-score self-score" :title="row.covered + '/' + row.total + ' 覆盖点'">{{ percentage(row) }} <small>{{ row.covered }}/{{ row.total }}</small></span>
          </span>
        </div>
      </div>
      <p v-else>当前模块暂无覆盖率数据</p>
      <p v-if="coverageError" class="line-coverage-warning">{{ coverageError }}</p>
      <button type="button" class="line-coverage-clear" :disabled="clearing" @click="clearCoverage" title="清空整个项目本轮累计，保留历史运行">清空累计</button>
    </div>
    <dialog ref="sourceDialog" class="coverage-source-dialog" @click="closeOnBackdrop">
      <header><strong>{{ sourceTitle }}</strong><button type="button" @click="sourceDialog.close()" aria-label="关闭源码">×</button></header>
      <div v-if="!sourceError" class="source-coverage-legend"><span class="missed">未覆盖</span><span class="partial">部分覆盖</span></div>
      <p v-if="sourceError">{{ sourceError }}</p>
      <pre ref="sourcePre" v-else><div v-for="(line,index) in sourceLines" :key="index" :class="{ selected: index+1 >= sourceStart && index+1 <= sourceEnd, missed: sourceMisses.has(index+1), partial: sourcePartial.has(index+1) }" :title="sourceMisses.has(index+1) ? '未覆盖' : sourcePartial.has(index+1) ? '部分覆盖' : ''"><span>{{ index+1 }}</span><code>{{ line || ' ' }}</code></div></pre>
    </dialog>
    <ul v-if="summary.length">
      <li v-for="entry in summary" :key="entry.name">
        <label class="overlay-row">
          <input
            type="checkbox"
            :checked="store.enabledOverlays.has(entry.name)"
            :disabled="!entry.known"
            @change="store.toggleOverlay(entry.name)"
          />
          <span class="overlay-name">{{ entry.name }}</span>
          <span v-if="!entry.known" class="tag-unknown">unknown</span>
        </label>
        <ul v-if="entry.known && entry.name !== 'phys' && legendFor(entry.name).length" class="legend">
          <li v-for="item in legendFor(entry.name)" :key="item.label">
            <span
              class="swatch"
              :class="`swatch-${item.kind || 'fill'}`"
              :style="swatchStyle(item)"
            ></span>
            {{ item.label }}
          </li>
          <li
            v-if="tbScopeNoteFor(entry.name)"
            class="legend-note"
          >
            {{ tbScopeNoteFor(entry.name) }}
          </li>
        </ul>
        <!-- The phys overlay's legend is bivariate (area × power) and
             carries its own scope toggle + provenance block, so it
             replaces the flat swatch list rather than sitting beside
             it. Every other overlay keeps the generic list above. -->
        <PhysLegend v-if="entry.known && entry.name === 'phys'" />
      </li>
    </ul>
    <!-- Live overlays: sourced from the hub at runtime rather than
         from ``overlays_present``, so they list separately and carry
         a provenance line saying where the numbers came from. -->
    <ul v-if="hasLiveCoverage" class="live-overlays">
      <li>
        <label class="overlay-row">
          <input
            type="checkbox"
            data-testid="cov-live-toggle"
            :checked="store.covEnabled"
            @change="store.toggleCoverageTint()"
          />
          <span class="overlay-name">coverage</span>
          <span class="tag-live">live</span>
        </label>
        <p class="overlay-sub">{{ covProvenance }}</p>
        <ul class="legend">
          <li v-for="item in covLegend" :key="item.label">
            <span
              class="swatch"
              :class="`swatch-${item.kind || 'fill'}`"
              :style="swatchStyle(item)"
            ></span>
            {{ item.label }}
          </li>
        </ul>
      </li>
    </ul>
  </section>
</template>

<script setup>
// Per-overlay enable/disable + per-overlay legend slot.
//
// The list is driven by ``graph.overlays_present`` so the panel
// stays correct when a future producer ships an overlay this
// viewer doesn't know about — the entry appears tagged
// ``unknown`` and the checkbox is disabled (toggling it would do
// nothing visible).
import { computed, ref, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { useViewerStore } from '../store.js'
import { getOverlay, overlaySummary } from '../overlays/index.js'
import { coverageLiveOverlay } from '../overlays/coverage_live.js'
import { covGeneratedDate } from '../covData.js'
import { token, themeVersion } from '../theme.js'
import PhysLegend from './PhysLegend.vue'

const store = useViewerStore()
const projectCoverage = ref(null)
const coverageError = ref('')
const clearing = ref(false)
const coverageUrl = (() => {
  const view = new URLSearchParams(window.location.search).get('view')
  if (!view) return null
  const url = new URL(view, window.location.href)
  if (url.origin !== window.location.origin || !/^\/api\/chat\/projects\/[^/]+\/rtl-view$/.test(url.pathname)) return null
  return url.pathname.replace(/rtl-view$/, 'coverage')
})()
const percentage = row => row.total ? (100 * row.covered / row.total).toFixed(1) + '%' : '—'
const coverageRows = computed(() => {
  const root = store.selectedNode?.id
  if (!root) return []
  // Sum each instance's own counters exactly once, never sum child subtotals.
  const instances = [...new Map((projectCoverage.value?.report?.instances || []).map(row => [row.path, row])).values()]
  return instances.filter(row => row.path === root || row.path.startsWith(root + '.'))
    .sort((a, b) => a.path.localeCompare(b.path))
    .map(row => ({ ...row, hasChildren: instances.some(child => child.path.startsWith(row.path + '.')), depth: row.path.split('.').length - root.split('.').length,
      subtree: instances.filter(child => child.path === row.path || child.path.startsWith(row.path + '.'))
        .reduce((sum, child) => ({ covered: sum.covered + child.covered, total: sum.total + child.total }), { covered: 0, total: 0 }) }))
})
const collapsedCoverage = ref(new Set())
const visibleCoverageRows = computed(() => coverageRows.value.filter(row => !coverageRows.value.some(parent => collapsedCoverage.value.has(parent.path) && row.path.startsWith(parent.path + '.'))))
function toggleCoverageRow(row) {
  if (!row.hasChildren) return
  const next = new Set(collapsedCoverage.value)
  if (next.has(row.path)) next.delete(row.path); else next.add(row.path)
  collapsedCoverage.value = next
}
const sourceDialog=ref(null), sourcePre=ref(null), sourceLines=ref([]), sourceStart=ref(0),sourceEnd=ref(0),sourceError=ref(''),sourceTitle=ref('')
const sourceMisses=ref(new Set()), sourcePartial=ref(new Set())
let toggleTimer=null,sourceRequest=0
function scheduleToggle(row){clearTimeout(toggleTimer);toggleTimer=setTimeout(()=>toggleCoverageRow(row),220)}
function closeOnBackdrop(event){if(event.target===sourceDialog.value)sourceDialog.value.close()}
async function openSource(row){
  clearTimeout(toggleTimer);const request=++sourceRequest
  sourceTitle.value=row.module+' · 构建源码';sourceError.value='正在读取…';sourceLines.value=[]
  if(!sourceDialog.value.open)sourceDialog.value.showModal()
  try{
    const response=await fetch(coverageUrl+'/source?instance='+encodeURIComponent(row.path));const data=await response.json()
    if(request!==sourceRequest)return
    if(!response.ok)throw Error(data.detail||'读取失败')
    const coverageFile=(row.files || []).find(file=>file.path===data.file)
    sourceMisses.value=new Set(coverageFile?.uncovered_lines || []);sourcePartial.value=new Set(coverageFile?.partial_lines || [])
    sourceTitle.value=data.file+' · '+data.module;sourceStart.value=data.line;sourceEnd.value=data.end_line;sourceLines.value=data.content.split('\n');sourceError.value=''
    await nextTick();const selected=sourcePre.value?.querySelector('.selected');if(selected)sourcePre.value.scrollTop=selected.offsetTop-sourcePre.value.offsetTop-40
  }catch(error){if(request===sourceRequest)sourceError.value=error.message}
}
let polling = false, timer = null, disposed = false
async function refreshCoverage() {
  if (!coverageUrl || polling || document.hidden) return
  polling = true
  try {
    const response = await fetch(coverageUrl)
    if (!response.ok) throw Error('覆盖率读取失败')
    const data = await response.json()
    if (!disposed) { projectCoverage.value = data; store.projectCoverageInstances = data.report?.instances || []; coverageError.value = '' }
  } catch (error) { if (!disposed) coverageError.value = error.message }
  finally { polling = false }
}
async function clearCoverage() {
  if (!confirm('清空整个项目的本轮累计？历史运行和日志会保留。')) return
  clearing.value = true
  try {
    const response = await fetch(coverageUrl + '/reset', { method: 'POST' })
    if (!response.ok) throw Error('清空累计失败')
    projectCoverage.value = await response.json(); store.projectCoverageInstances = projectCoverage.value.report?.instances || []; coverageError.value = ''
  } catch (error) { coverageError.value = error.message }
  finally { clearing.value = false }
}
onMounted(() => { refreshCoverage(); timer = setInterval(refreshCoverage, 1500) })
onBeforeUnmount(() => { disposed = true; clearInterval(timer) })

const summary = computed(() =>
  store.graph ? overlaySummary(store.graph) : [],
)

// --- live coverage ---------------------------------------------------
//
// Not part of ``summary``: ``overlays_present`` describes what the
// PRODUCER baked into view.json, and this comes from the hub after the
// fact. It gets its own entry, tagged ``live``, with a line saying
// which run the numbers are from — the same payload can be minutes or
// days old and the panel is the only place that says so.
const hasLiveCoverage = computed(() => store.covData !== null)
const covProvenance = computed(() => {
  const day = covGeneratedDate(store.covData)
  const base = "from the hub's latest run"
  return day ? `${base} · ${day}` : base
})
const covLegend = computed(() => {
  themeVersion.value // the ramp swatches are resolved tokens, not var()
  return coverageLiveOverlay.legend()
})
function legendFor(name) {
  // Legend swatches are concrete colours an overlay resolved from a
  // token, so a theme flip has to re-run this. Reading themeVersion
  // during render is what registers the flip as a render dependency.
  themeVersion.value
  const overlay = getOverlay(name)
  if (!overlay || typeof overlay.legend !== 'function') return []
  return overlay.legend(store.graph) || []
}

// Phase 6d (#99): in TB view, the DUT-side clock/reset domain maps
// produced by rtl-buddy-cdc don't speak the testbench's scope —
// SDC is the design's timing contract, not the simulation harness's
// stimulus. So the DUT subtree keeps its full overlay tint while
// every TB scope above renders unannotated. Surface that as a small
// footnote on each affected overlay's legend rather than leaving a
// silent gap; users see a legend entry that explains why the TB
// blocks aren't coloured. Returns an empty string (falsy) for
// overlays that aren't affected or when the view is DUT-rooted.
function tbScopeNoteFor(name) {
  if (store.renderedViewMode !== 'tb') return ''
  if (name !== 'clock' && name !== 'reset') return ''
  // Phase 6e (#99): when a `tb_clock_map.json` is in play, the
  // renderer has already tinted TB scopes outside the DUT — at
  // least one non-DUT node carries an overlays.<name> entry. In
  // that case the "no map above DUT" footnote is misleading; hide
  // it and let the legend speak for itself.
  if (_overlayReachesTbScope(name)) return ''
  if (name === 'clock') return '(no clock map above DUT)'
  if (name === 'reset') return '(no reset map above DUT)'
  return ''
}

function _overlayReachesTbScope(overlayName) {
  const g = store.graph
  if (!g) return false
  const anchors = store.dutAnchorIds || []
  const isInsideDut = (id) =>
    anchors.some((a) => id === a || id.startsWith(a + '.'))
  for (const node of g.nodes) {
    if (isInsideDut(node.id)) continue
    const ov = node.overlays && node.overlays[overlayName]
    if (!ov) continue
    // The clock + reset overlays both surface their identity via a
    // non-empty field (``clock`` for clocks, ``reset`` for resets).
    // Anything truthy under the key means the overlay reaches this
    // TB-side node.
    if (overlayName === 'clock' && ov.clock) return true
    if (overlayName === 'reset' && ov.reset) return true
  }
  return false
}

// Per-kind swatch styling. ``fill`` is a solid filled box (clock
// palette pastels, default for entries without a ``kind`` field).
// ``stroke`` is an outlined-only box (reset border markers).
// ``dashed-line`` is a horizontal dashed segment (CDC / RDC edge
// styles). The kind is a hint from each overlay's legend() — see
// overlays/clock.js + overlays/reset.js.
function swatchStyle(item) {
  const kind = item.kind || 'fill'
  if (kind === 'stroke') {
    return { borderColor: item.swatch, borderWidth: '2px', background: 'transparent' }
  }
  if (kind === 'dashed-line' || kind === 'solid-line') {
    return { borderColor: item.swatch }
  }
  return { background: item.swatch }
}
</script>

<style scoped>
.overlay-panel { padding: 0.5rem; }
.overlay-panel h3 {
  margin: 0 0 0.5rem;
  font-size: 0.85rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--fg-muted);
}
.overlay-panel ul { list-style: none; padding: 0; margin: 0; }
.overlay-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-family: var(--font-mono);
  font-size: 0.9rem;
  cursor: pointer;
}
.tag-unknown {
  font-size: 0.7rem;
  background: var(--panel-2);
  color: var(--fg-muted);
  padding: 0.05rem 0.4rem;
  border-radius: 999px;
}
/* ``live`` marks an overlay whose data came from the hub at runtime
   rather than from the loaded view.json. Same chip geometry as the
   ``unknown`` tag, accent-coloured because it is a statement about
   freshness, not a warning. */
.tag-live {
  font-size: 0.7rem;
  background: var(--panel-2);
  color: var(--accent);
  padding: 0.05rem 0.4rem;
  border-radius: 999px;
}
/* Provenance line under a live overlay's checkbox. */
.overlay-sub {
  margin: 0.1rem 0 0.15rem 1.5rem;
  font-size: 0.7rem;
  color: var(--fg-faint);
}
/* The live group follows the payload list with a hairline between
   them, so "produced" and "fetched" don't read as one list. The
   sibling selector is what keeps the rule honest: with no payload
   overlays there is no preceding list, and a divider hanging under
   the heading would be separating nothing from something. */
ul + .live-overlays {
  margin-top: 0.4rem;
  padding-top: 0.4rem;
  border-top: 1px solid var(--line);
}
.legend {
  margin-left: 1.5rem;
  font-size: 0.75rem;
  color: var(--fg-muted);
}
.legend li { display: flex; align-items: center; gap: 0.5rem; padding: 0.1rem 0; }
/* Phase 6d (#99): TB-scope explanatory footnote on the clock/reset
   legends. Italic, smaller, indented so it reads as a side comment
   rather than a swatched legend entry. */
.legend-note {
  font-style: italic;
  font-size: 0.7rem;
  color: var(--fg-faint);
  margin-top: 0.15rem;
  padding-left: 1.25rem;
}
.swatch {
  width: 16px;
  height: 12px;
  border-radius: 2px;
  border: 1px solid var(--line-strong);
  display: inline-block;
  box-sizing: border-box;
}
/* Outlined-only swatch — reset border markers. The border colour
   is injected inline; the fill stays transparent so the kind reads
   as "stroke" rather than "fill". */
.swatch-stroke { border-radius: 2px; }
/* Horizontal dashed line — CDC / RDC edge styles. Render via a
   thick top border on a short, otherwise-borderless box so the
   dashes are visible at 16x12. */
.swatch-dashed-line {
  border: none;
  border-top-style: dashed;
  border-top-width: 3px;
  height: 0;
  width: 18px;
  align-self: center;
  margin-top: 4px;
  border-radius: 0;
}
/* Horizontal solid line — non-toggleable structural styles (e.g.
   port → instance signal flow). Same shape as the dashed
   variant; only the stroke style differs. */
.swatch-solid-line {
  border: none;
  border-top-style: solid;
  border-top-width: 3px;
  height: 0;
  width: 18px;
  align-self: center;
  margin-top: 4px;
  border-radius: 0;
}
.layout-legend { margin-left: 0; }
.empty {
  font-size: 0.85rem;
  color: var(--fg-muted);
  margin: 0;
}
/* The "here's the command" line: quieter than the statement above it,
   and the command itself wraps rather than widening the sidebar. */
.empty-hint {
  margin-top: 0.35rem;
  font-size: 0.75rem;
  color: var(--fg-faint);
  line-height: 1.4;
}
.empty-hint code {
  font-family: var(--font-mono);
  background: var(--panel-2);
  padding: 0.05rem 0.3rem;
  border-radius: var(--radius-1);
  word-break: break-word;
}

.project-line-coverage { padding: 0 0 6px; font-size: var(--fs-small); }
.line-coverage-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 3px 6px 7px; color: var(--fg-muted); font-size: 11px; }
.project-line-coverage p { margin: 7px 6px; line-height: 1.6; color: var(--fg-muted); font-size: 11px; }
.project-line-coverage .line-coverage-warning { color: var(--warn); }
.line-coverage-clear { margin: 7px 6px 0; padding: 0; border: 0; background: none; color: var(--fg-muted); font-size: 10px; cursor: pointer; }
.line-coverage-clear:hover { color: var(--fg); }
.line-coverage-columns { display:flex; align-items:center; gap:.3rem; padding:.15rem .25rem .35rem; color:var(--fg-muted); font-size:var(--fs-small); }
.line-coverage-columns span:first-child { flex:1; }
.line-coverage-columns span:nth-child(2) { width:116px; text-align:right; }
.line-coverage-columns span:nth-child(3) { width:108px; text-align:right; }
.coverage-tree-row .tree-inst { flex-shrink:0; max-width:45%; }
.coverage-tree-row .tree-module { min-width:0; }
.coverage-tree-values { display:flex; gap:.3rem; margin-left:auto; flex-shrink:0; }
.line-coverage-score { width:116px; text-align:right; font-family:var(--font-mono); font-size:var(--fs-small); font-variant-numeric:tabular-nums; }
.self-score { width:108px; color:var(--fg-muted); }
.line-coverage-score small { font-size:10px; color:var(--fg-muted); margin-left:4px; font-weight:400; }
.coverage-tree-row:focus-visible { outline:1px dashed var(--line-strong); outline-offset:-1px; }
.coverage-source-dialog { width: min(1000px,94vw); max-height: 90vh; padding: 0; border: 1px solid var(--line); border-radius: 8px; background: var(--panel); color: var(--fg); }
.coverage-source-dialog::backdrop { background: #0005; }
.coverage-source-dialog header { display:flex; justify-content:space-between; align-items:center; padding:10px 14px; font-size:12px; }
.coverage-source-dialog header button { border:0; background:none; color:inherit; cursor:pointer; font-size:20px; }
.coverage-source-dialog pre { margin:0; max-height:70vh; overflow:auto; padding:8px 0; font:11px/1.7 var(--font-mono); }
.coverage-source-dialog pre>div { display:flex; min-width:max-content; }
.coverage-source-dialog pre span { width:48px; flex-shrink:0; text-align:right; padding-right:12px; color:var(--fg-muted); user-select:none; }
.coverage-source-dialog .selected { background:color-mix(in srgb,var(--accent) 12%,transparent); }
</style>

<style scoped src="./hierarchy-rows.css"></style>

<style scoped>
.source-coverage-legend { display:flex; gap:12px; padding:0 14px 8px; font-size:11px; color:var(--fg-muted); }
.source-coverage-legend span { padding:2px 7px; border-left:3px solid; }
.coverage-source-dialog pre>div { border-left:3px solid transparent; }
.coverage-source-dialog .missed { background:color-mix(in srgb,#e5484d 15%,var(--panel)); border-left-color:#e5484d; }
.coverage-source-dialog .partial { background:color-mix(in srgb,#d99614 16%,var(--panel)); border-left-color:#d99614; }
</style>

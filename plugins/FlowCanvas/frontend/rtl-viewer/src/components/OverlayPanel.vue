<template>
  <section class="overlay-panel">
    <div v-if="coverageUrl && store.graph" class="project-line-coverage">
      <div class="line-coverage-toolbar"><input v-model="filter" class="line-coverage-search" placeholder="筛选模块…" aria-label="筛选模块" @keydown.esc.prevent.stop="filter=''" /></div>
      <div class="line-coverage-summary">
        <span>{{ coverageRows.length }} 模块</span>
        <button type="button" class="line-coverage-toggle" :title="allExpanded ? '全部折叠' : '全部展开'" :aria-label="allExpanded ? '全部折叠' : '全部展开'" :disabled="!coverageRows.length" @click="toggleAll">{{ allExpanded ? '全部折叠' : '全部展开' }}</button>
      </div>
      <div v-if="coverageRows.length" class="line-coverage-list tree-scroll">
        <div class="line-coverage-columns"><span>模块实例</span><span>含子模块</span><span>自身</span></div>
        <div v-for="row in visibleCoverageRows" :key="row.path" class="tree-row coverage-tree-row" :data-instance="row.path" :style="{ paddingLeft: (10 + row.depth * 14) + 'px' }" :class="{ 'is-selected': row.path === store.selectedNode?.id, 'has-children': row.hasChildren, 'is-root': row.depth === 0 }" :role="row.hasChildren ? 'button' : undefined" :tabindex="row.hasChildren ? 0 : undefined" :aria-expanded="row.hasChildren ? !collapsedCoverage.has(row.path) : undefined" @mouseenter="loadSource(row).catch(()=>{})" @focusin="loadSource(row).catch(()=>{})" @click="scheduleToggle(row)" @dblclick.stop="openSource(row)" @keydown.enter.prevent="toggleCoverageRow(row)" @keydown.space.prevent="toggleCoverageRow(row)">
          <button v-if="row.hasChildren" type="button" class="tree-caret" :aria-label="(collapsedCoverage.has(row.path) ? '展开 ' : '折叠 ') + row.path" @click.stop="toggleCoverageRow(row)" @dblclick.stop><svg viewBox="0 0 16 16" aria-hidden="true" :class="{expanded: !collapsedCoverage.has(row.path)}"><path d="m5 3 6 5-6 5Z"/></svg></button>
          <span v-else class="tree-caret tree-caret-leaf" aria-hidden="true"></span>
          <span class="coverage-row-name">
            <span class="tree-inst" :title="row.path">{{ row.path.split('.').at(-1) }}</span>
            <button type="button" class="coverage-source-link" :title="'查看 ' + row.module + ' 源码'" :aria-label="'查看 ' + row.module + ' 源码'" @click.stop="openSource(row)" @dblclick.stop @keydown.enter.stop @keydown.space.stop>{{ sourceLocations[row.path] || row.files?.[0]?.path?.split('/').at(-1) || row.module }}</button>
          </span>
          <button class="tree-ai-explain" type="button" :aria-label="'AI 分析'+metricLabel+' '+row.module" :title="'AI 分析'+metricLabel" @mouseenter="analyzeCoverage(row,$event,'preview')" @mouseleave="analyzeCoverage(row,$event,'leave')" @click.stop="analyzeCoverage(row,$event,'pin')" @dblclick.stop @keydown.stop><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M13 4H5a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-6M7 9h4M7 13h8M7 17h5"/><path class="design-ai-spark" d="m18 1 1.4 4.6L24 7l-4.6 1.4L18 13l-1.4-4.6L12 7l4.6-1.4Z"/></svg></button>
          <span class="coverage-tree-values">
            <span v-for="(metric,index) in [row.subtree,row]" :key="index" class="line-coverage-score" :title="(index ? '自身' : '含子模块') + '：' + metric.covered + '/' + metric.total + ' 覆盖点'">
              <span class="line-coverage-percent">{{ percentage(metric) }}</span><small>{{ metric.covered }}/{{ metric.total }}</small>
              <span class="line-coverage-meter" aria-hidden="true" :style="{'--coverage': (metric.total ? Math.min(100,100*metric.covered/metric.total) : 0) + '%'}"></span>
            </span>
          </span>
        </div>
      </div>
      <p v-else>当前模块暂无覆盖率数据</p>
      <p v-if="coverageError" class="line-coverage-warning">{{ coverageError }}</p>
    </div>
    <dialog ref="sourceDialog" class="coverage-source-dialog" @click="closeOnBackdrop">
      <header><strong>{{ sourceTitle }}</strong><button type="button" @click="sourceDialog.close()" aria-label="关闭源码">×</button></header>
      <select v-if="sourceFiles.length>1" v-model="sourceFile" class="coverage-source-selector" aria-label="源码文件" @change="openSource(sourceRow,sourceFile)"><option v-for="file in sourceFiles" :key="file.path" :value="file.path">{{ file.path }}</option></select>
      <p v-if="sourceError">{{ sourceError }}</p>
      <pre ref="sourcePre" v-else><div v-for="(line,index) in sourceLines" :key="index" :class="{ selected: index+1 >= sourceStart && index+1 <= sourceEnd, covered: sourceHits.has(index+1), missed: sourceMisses.has(index+1), partial: sourcePartial.has(index+1) }" :title="sourceMisses.has(index+1) ? '未覆盖' : sourcePartial.has(index+1) ? '部分覆盖' : sourceHits.has(index+1) ? '已覆盖' : ''"><span>{{ index+1 }}</span><code>{{ line || ' ' }}</code></div></pre>
    </dialog>
  </section>
</template>
<script setup>
import { computed, ref, onBeforeUnmount, nextTick, watch } from 'vue'
import { useViewerStore } from '../store.js'
import { coverageMetricRows } from '../coverageMetricRows.js'
const store = useViewerStore()
const metric = new URLSearchParams(location.search).get('metric') || 'line'
const metricLabel = ({line:'行覆盖率',toggle:'翻转覆盖率',branch:'分支覆盖率',expr:'表达式覆盖率',cond:'条件覆盖率',fsm:'状态机覆盖率'})[metric] || '覆盖率'
/** Send cloneable coverage context to the shared host task card. */
function analyzeCoverage(row,event,action){
  const rect=event.currentTarget.getBoundingClientRect()
  const node=store.nodesById.get(row.path)
  const context={type:'flowcanvas-explain-design',kind:'line-coverage',action,instance:row.path,module:row.module,
    metric,source:node?.source||null,coverage:{self:{covered:row.covered,total:row.total},subtree:row.subtree,
      instances:coverageRows.value.filter(item=>item.path===row.path||item.path.startsWith(row.path+'.')),
      report:'.flowcanvas/coverage'},rect:{left:rect.left,right:rect.right,top:rect.top,bottom:rect.bottom}}
  window.parent.postMessage(JSON.parse(JSON.stringify(context)),location.origin)
}
const projectCoverage = computed(()=>store.projectCoverageState)
const coverageError = computed(()=>store.projectCoverageError)
const coverageUrl = (() => {
  const view = new URLSearchParams(window.location.search).get('view')
  if (!view) return null
  const url = new URL(view, window.location.href)
  if (url.origin !== window.location.origin || !/^\/api\/chat\/projects\/[^/]+\/rtl-view$/.test(url.pathname)) return null
  return url.pathname.replace(/rtl-view$/, 'coverage')
})()
const percentage = row => row.total ? (100 * row.covered / row.total).toFixed(1) + '%' : '—'
const coverageRows = computed(() => {
  const root = store.graph?.top
  if (!root) return []
  // Sum each instance's own counters exactly once, never sum child subtotals.
  const instances = [...new Map(coverageMetricRows(projectCoverage.value?.report,metric,root).map(row => [row.path,row])).values()]
  return instances.filter(row => row.path === root || row.path.startsWith(root + '.'))
    .sort((a, b) => a.path.localeCompare(b.path))
    .map(row => ({ ...row, hasChildren: instances.some(child => child.path.startsWith(row.path + '.')), depth: row.path.split('.').length - root.split('.').length,
      subtree: instances.filter(child => child.path === row.path || child.path.startsWith(row.path + '.'))
        .reduce((sum, child) => ({ covered: sum.covered + child.covered, total: sum.total + child.total }), { covered: 0, total: 0 }) }))
})
const filter = ref('')
const collapsedCoverage = ref(new Set())
const allExpanded = computed(()=>coverageRows.value.every(row=>!row.hasChildren || !collapsedCoverage.value.has(row.path)))
function toggleAll() {
  filter.value=''
  collapsedCoverage.value=allExpanded.value ? new Set(coverageRows.value.filter(row=>row.hasChildren).map(row=>row.path)) : new Set()
}

watch(()=>store.selection,async id=>{
  collapsedCoverage.value=new Set([...collapsedCoverage.value].filter(path=>!id?.startsWith(path+'.')))
  await nextTick()
  const row=[...document.querySelectorAll('.coverage-tree-row')].find(el=>el.dataset.instance===id)
  row?.scrollIntoView({block:'nearest'})
})

const visibleCoverageRows = computed(() => {
  const query=filter.value.trim().toLowerCase()
  if(query){
    const matches=coverageRows.value.filter(row=>(row.path+' '+row.module).toLowerCase().includes(query))
    return coverageRows.value.filter(row=>matches.some(match=>match.path===row.path||match.path.startsWith(row.path+'.')))
  }
  return coverageRows.value.filter(row=>!coverageRows.value.some(parent=>collapsedCoverage.value.has(parent.path)&&row.path.startsWith(parent.path+'.')))
})
function toggleCoverageRow(row) {
  if (!row.hasChildren) return
  const next = new Set(collapsedCoverage.value)
  if (next.has(row.path)) next.delete(row.path); else next.add(row.path)
  collapsedCoverage.value = next
}
const sourceDialog=ref(null), sourcePre=ref(null), sourceLines=ref([]), sourceStart=ref(0),sourceEnd=ref(0),sourceError=ref(''),sourceTitle=ref('')
const sourceMisses=ref(new Set()), sourcePartial=ref(new Set()), sourceHits=ref(new Set())
const sourceLocations=ref({}), sourceCache=new Map()
const sourceFiles=ref([]),sourceFile=ref(''),sourceRow=ref(null)
watch(()=>[projectCoverage.value?.build_id,projectCoverage.value?.updated],()=>{sourceCache.clear();sourceLocations.value={}})
// Share the exact build-source lookup between hover labels and the source viewer.
async function loadSource(row,file=row.files?.[0]?.path){
  const key=(projectCoverage.value?.build_id||'')+':'+row.path+':'+metric+':'+file
  if(!sourceCache.has(key))sourceCache.set(key,(async()=>{
    const response=await fetch(coverageUrl+'/source?instance='+encodeURIComponent(row.path)+'&metric='+encodeURIComponent(metric)+'&file='+encodeURIComponent(file||'')),data=await response.json()
    if(!response.ok)throw Error(data.detail||'读取失败')
    return data
  })())
  try{const data=await sourceCache.get(key);if(key===(projectCoverage.value?.build_id||'')+':'+row.path+':'+metric+':'+file)sourceLocations.value[row.path]=data.file.split('/').at(-1)+':'+data.line;return data}
  catch(error){sourceCache.delete(key);throw error}
}
let toggleTimer=null,sourceRequest=0
function scheduleToggle(row){clearTimeout(toggleTimer);toggleTimer=setTimeout(()=>toggleCoverageRow(row),220)}
function closeOnBackdrop(event){if(event.target===sourceDialog.value)sourceDialog.value.close()}
async function openSource(row,file=row.files?.[0]?.path){
  sourceRow.value=row;sourceFiles.value=row.files||[];sourceFile.value=file
  clearTimeout(toggleTimer);const request=++sourceRequest
  sourceTitle.value=row.module+' · 构建源码';sourceError.value='正在读取…';sourceLines.value=[]
  if(!sourceDialog.value.open)sourceDialog.value.showModal()
  try{
    const data=await loadSource(row,file)
    if(request!==sourceRequest)return
    const coverageFile=(row.files || []).find(file=>file.path===data.file)
    sourceHits.value=new Set(coverageFile?.covered_lines || []);sourceMisses.value=new Set(coverageFile?.uncovered_lines || []);sourcePartial.value=new Set(coverageFile?.partial_lines || [])
    sourceTitle.value=data.file+' · '+data.module;sourceStart.value=data.line;sourceEnd.value=data.end_line;sourceLines.value=data.content.split('\n');sourceError.value=''
    await nextTick();const selected=sourcePre.value?.querySelector('.selected');if(selected)sourcePre.value.scrollTop=selected.offsetTop-sourcePre.value.offsetTop-40
  }catch(error){if(request===sourceRequest)sourceError.value=error.message}
}
onBeforeUnmount(() => { clearTimeout(toggleTimer); sourceRequest++ })

</script>
<style scoped>
.project-line-coverage { padding: 0 0 6px; font-size: var(--fs-small); }
.line-coverage-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 3px 6px 7px; color: var(--fg-muted); font-size: 11px; }
.project-line-coverage p { margin: 7px 6px; line-height: 1.6; color: var(--fg-muted); font-size: 11px; }
.project-line-coverage .line-coverage-warning { color: var(--warn); }
.line-coverage-clear { margin: 7px 6px 0; padding: 0; border: 0; background: none; color: var(--fg-muted); font-size: 10px; cursor: pointer; }
.line-coverage-clear:hover { color: var(--fg); }
.coverage-tree-row:focus-visible { outline:1px dashed var(--line-strong); outline-offset:-1px; }
.coverage-source-dialog { width: min(1000px,94vw); max-height: 90vh; padding: 0; border: 1px solid var(--line); border-radius: 8px; background: var(--panel); color: var(--fg); }
.coverage-source-dialog::backdrop { background: #0005; }
.coverage-source-dialog header { display:flex; justify-content:space-between; align-items:center; padding:10px 14px; font-size:12px; }
.coverage-source-dialog header button { border:0; background:none; color:inherit; cursor:pointer; font-size:20px; }
.coverage-source-selector{margin:0 14px 8px;max-width:calc(100% - 28px);font:11px system-ui;color:var(--fg);background:var(--panel);border:1px solid var(--line);padding:4px}
.coverage-source-dialog pre { margin:0; max-height:70vh; overflow:auto; padding:8px 0; font:11px/1.7 var(--font-mono); }
.coverage-source-dialog pre>div { display:flex; min-width:max-content; }
.coverage-source-dialog pre span { width:48px; flex-shrink:0; text-align:right; padding-right:12px; color:var(--fg-muted); user-select:none; }
.coverage-source-dialog .selected { background:color-mix(in srgb,var(--accent) 12%,transparent); }
</style>

<style scoped src="./hierarchy-rows.css"></style>

<style scoped>
.coverage-source-dialog pre>div { border-left:3px solid transparent; }
.coverage-source-dialog .covered { background:color-mix(in srgb,#36b37e 15%,var(--panel)); border-left-color:#36b37e; }
.coverage-source-dialog .missed { background:color-mix(in srgb,#e5484d 15%,var(--panel)); border-left-color:#e5484d; }
.coverage-source-dialog .partial { background:color-mix(in srgb,#d99614 16%,var(--panel)); border-left-color:#d99614; }
</style>

<style scoped>
.overlay-panel{min-height:100vh;display:flex;flex-direction:column;background:var(--panel);font-family:system-ui,-apple-system,"Segoe UI",sans-serif}
.project-line-coverage{display:flex;flex-direction:column;flex:1;padding:10px 12px 0}
.line-coverage-toolbar{display:flex;margin-bottom:9px}
.line-coverage-search{box-sizing:border-box;min-width:0;width:100%;border:1px solid var(--line);border-radius:5px;padding:6px 9px;background:color-mix(in srgb,var(--accent) 3%,var(--panel));color:var(--fg);font:12px system-ui,-apple-system,"Segoe UI",sans-serif;outline-offset:2px}
.line-coverage-summary{display:flex;align-items:center;flex-wrap:wrap;gap:6px 12px;margin:0 -12px;padding:0 12px 7px;border-bottom:1px solid var(--line);color:var(--fg-muted);font-size:11px}
.line-coverage-summary button{font:inherit;color:var(--fg-muted);background:none;border:0;cursor:pointer;padding:3px 4px;border-radius:4px;margin:0}
.line-coverage-summary button:hover{background:color-mix(in srgb,var(--fg) 3%,var(--panel))}
.line-coverage-summary button:disabled{opacity:.45;cursor:default}
.line-coverage-summary .line-coverage-toggle{margin-left:auto;font-family:inherit;font-size:9px;font-weight:400;white-space:nowrap}
.line-coverage-list.tree-scroll{flex:1;max-height:none;overflow:visible;margin:0 -12px;padding:8px 12px 28px;background:var(--panel)}
.coverage-tree-row{box-sizing:border-box;min-height:32px;margin:0;border-radius:4px;gap:6px;padding-top:5px;padding-bottom:5px;outline:1px solid transparent;outline-offset:-1px}
.coverage-tree-row:hover{background:color-mix(in srgb,var(--accent) 4%,var(--panel))}
.coverage-tree-row.is-selected{background:color-mix(in srgb,var(--accent) 7%,var(--panel));outline:0}
.coverage-row-name{display:flex;align-items:center;gap:8px;flex:1;min-width:0;overflow:hidden}
.coverage-tree-row .tree-inst{font:500 12px/1.5 system-ui,sans-serif;flex:0 1 auto;min-width:0;max-width:none}
.coverage-source-link{flex-shrink:0;border:0;background:none;padding:2px 0;color:var(--fg-muted);font:9px/1.5 system-ui,sans-serif;white-space:nowrap;opacity:0;pointer-events:none;cursor:pointer}
.coverage-tree-row:hover .coverage-source-link,.coverage-tree-row:focus-within .coverage-source-link{opacity:.75;pointer-events:auto}
.coverage-source-link:hover{opacity:1!important;color:var(--accent);text-decoration:underline;text-underline-offset:3px}
@media(hover:none){.coverage-source-link{opacity:.75;pointer-events:auto}}
.coverage-tree-row.is-root .tree-inst{font-weight:650}
.coverage-tree-row .tree-module{display:none}
.coverage-tree-row .tree-caret{width:12px;height:16px;display:grid;place-items:center}
.tree-caret svg{width:10px;height:10px;fill:currentColor}
.tree-caret svg.expanded{transform:rotate(90deg)}
.coverage-tree-values{display:flex;gap:14px;margin-left:auto;flex-shrink:0}
.line-coverage-score{display:grid;grid-template-columns:1fr auto;align-items:baseline;gap:3px 6px;width:78px;text-align:right;font:400 10px/1.4 system-ui,sans-serif;font-variant-numeric:tabular-nums}
.line-coverage-percent{font-size:11px;color:var(--fg);font-weight:500}
.line-coverage-score small{font-size:9px;color:var(--fg-muted);font-weight:400}
.line-coverage-meter{grid-column:1/-1;height:2px;border-radius:2px;background:color-mix(in srgb,var(--fg) 7%,var(--panel));overflow:hidden}
.line-coverage-meter::before{content:'';display:block;width:var(--coverage);height:100%;background:#36b37e;border-radius:inherit}
.line-coverage-columns{display:flex;align-items:center;gap:14px;padding:2px 4px 7px 8px;font:9px/1.5 system-ui,sans-serif;color:var(--fg-muted)}
.line-coverage-columns span:first-child{flex:1}
.line-coverage-columns span:nth-child(2),.line-coverage-columns span:nth-child(3){width:78px;text-align:right}
/* Match the functional tab's controls without changing the coverage table. */
:global(html[data-theme="light"] .overlay-panel){background:#fafafa}
.line-coverage-search::placeholder{color:var(--fg-muted);opacity:.8}
.line-coverage-summary>span{font-family:inherit;font-size:9px;font-weight:400;color:color-mix(in srgb,var(--fg) 65%,var(--panel))}
.line-coverage-summary .line-coverage-clear{padding:2px}
</style>

<style scoped>
/* Keep actions in their own reserved slot so the two metric columns stay aligned. */
.coverage-tree-row{padding-right:4px}
.coverage-row-name .tree-inst{flex:0 1 auto}
.coverage-source-link{flex:0 1 auto;min-width:0;max-width:45%;overflow:hidden;text-overflow:ellipsis}
.tree-ai-explain{display:grid;place-items:center;flex:0 0 20px;width:20px;height:20px;margin:0 2px;padding:0;border:0;border-radius:4px;background:transparent;color:var(--fg-muted);opacity:0;cursor:pointer;transition:opacity .15s}
.tree-ai-explain svg{display:block;width:16px;height:16px;fill:none;stroke:currentColor;stroke-width:1.5;stroke-linecap:round;stroke-linejoin:round;overflow:visible}
.tree-ai-explain .design-ai-spark{fill:#9466e8;stroke:#6e83f4;stroke-width:.7;transform-box:fill-box;transform-origin:center;transition:transform .25s}
.tree-row:hover .tree-ai-explain,.tree-row:focus-within .tree-ai-explain{opacity:1}
.tree-ai-explain:hover{color:var(--accent)}
.tree-ai-explain:hover .design-ai-spark{transform:rotate(10deg) scale(1.1)}
@media(hover:none){.tree-ai-explain{opacity:1}}
@media(prefers-reduced-motion:reduce){.tree-ai-explain,.tree-ai-explain .design-ai-spark{transition:none}}
</style>

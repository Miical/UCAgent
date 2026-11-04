<template>
  <section class="overlay-panel">
    <div v-if="coverageUrl && store.selectedNode" class="project-line-coverage">
      <div class="line-coverage-toolbar"><input v-model="filter" class="line-coverage-search" placeholder="筛选模块…" aria-label="筛选模块" @keydown.esc="filter=''" /></div>
      <div class="line-coverage-summary">
        <span>{{ coverageRows.length }} 模块</span>
        <button type="button" class="line-coverage-toggle" :title="allExpanded ? '全部折叠' : '全部展开'" :aria-label="allExpanded ? '全部折叠' : '全部展开'" :disabled="!coverageRows.length" @click="toggleAll">{{ allExpanded ? '全部折叠' : '全部展开' }}</button>
      </div>
      <div v-if="coverageRows.length" class="line-coverage-list tree-scroll">
        <div class="line-coverage-columns"><span>模块实例</span><span>含子模块</span><span>自身</span></div>
        <div v-for="row in visibleCoverageRows" :key="row.path" class="tree-row coverage-tree-row" :data-instance="row.path" :style="{ paddingLeft: (10 + row.depth * 14) + 'px' }" :class="{ 'is-selected': row.path === store.selectedNode.id, 'has-children': row.hasChildren }" :role="row.hasChildren ? 'button' : undefined" :tabindex="row.hasChildren ? 0 : undefined" :aria-expanded="row.hasChildren ? !collapsedCoverage.has(row.path) : undefined" @click="scheduleToggle(row)" @dblclick.stop="openSource(row)" @keydown.enter.prevent="toggleCoverageRow(row)" @keydown.space.prevent="toggleCoverageRow(row)">
          <button v-if="row.hasChildren" type="button" class="tree-caret" :aria-label="(collapsedCoverage.has(row.path) ? '展开 ' : '折叠 ') + row.path" @click.stop="toggleCoverageRow(row)" @dblclick.stop><svg viewBox="0 0 16 16" aria-hidden="true" :class="{expanded: !collapsedCoverage.has(row.path)}"><path d="m5 3 6 5-6 5Z"/></svg></button>
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
    </div>
    <dialog ref="sourceDialog" class="coverage-source-dialog" @click="closeOnBackdrop">
      <header><strong>{{ sourceTitle }}</strong><button type="button" @click="sourceDialog.close()" aria-label="关闭源码">×</button></header>
      <div v-if="!sourceError" class="source-coverage-legend"><span class="missed">未覆盖</span><span class="partial">部分覆盖</span></div>
      <p v-if="sourceError">{{ sourceError }}</p>
      <pre ref="sourcePre" v-else><div v-for="(line,index) in sourceLines" :key="index" :class="{ selected: index+1 >= sourceStart && index+1 <= sourceEnd, missed: sourceMisses.has(index+1), partial: sourcePartial.has(index+1) }" :title="sourceMisses.has(index+1) ? '未覆盖' : sourcePartial.has(index+1) ? '部分覆盖' : ''"><span>{{ index+1 }}</span><code>{{ line || ' ' }}</code></div></pre>
    </dialog>
  </section>
</template>
<script setup>
import { computed, ref, onBeforeUnmount, nextTick, watch } from 'vue'
import { useViewerStore } from '../store.js'
const store = useViewerStore()
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
  const instances = [...new Map((projectCoverage.value?.report?.instances || []).map(row => [row.path, row])).values()]
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
.coverage-tree-row{box-sizing:border-box;min-height:26px;margin:1px 0;border-radius:4px;gap:6px;padding-top:3px;padding-bottom:3px;outline:1px solid transparent;outline-offset:-1px}
.coverage-tree-row:hover{background:color-mix(in srgb,var(--accent) 6%,var(--panel))}
.coverage-tree-row.is-selected{background:color-mix(in srgb,var(--accent) 12%,var(--panel));outline:1px dashed color-mix(in srgb,var(--accent) 28%,var(--panel))}
.coverage-tree-row .tree-inst{font:600 12px/1.5 system-ui,sans-serif;flex-shrink:0;max-width:40%}
.coverage-tree-row .tree-module{min-width:0;font-size:11px;visibility:hidden}
.coverage-tree-row:hover .tree-module,.coverage-tree-row:focus-within .tree-module{visibility:visible}
.coverage-tree-row .tree-caret{width:10px;display:grid;place-items:center}
.tree-caret svg{width:10px;height:10px;fill:currentColor}
.tree-caret svg.expanded{transform:rotate(90deg)}
.coverage-tree-values{display:flex;gap:.3rem;margin-left:auto;flex-shrink:0}
.line-coverage-score{width:116px;text-align:right;color:#1aa66b;font:400 11px/1.5 system-ui,sans-serif;font-variant-numeric:tabular-nums}
.self-score{width:108px}
.line-coverage-score small{font-size:10px;color:var(--fg-muted);margin-left:4px;font-weight:400}
.line-coverage-columns{display:flex;align-items:center;gap:.3rem;padding:.15rem .25rem 6px;font:11px/1.5 system-ui,sans-serif;color:var(--fg-muted)}
.line-coverage-columns span:first-child{flex:1}
.line-coverage-columns span:nth-child(2){width:116px;text-align:right}
.line-coverage-columns span:nth-child(3){width:108px;text-align:right}
/* Match the functional tab's controls without changing the coverage table. */
:global(html[data-theme="light"] .overlay-panel){background:#fafafa}
.line-coverage-search::placeholder{color:var(--fg-muted);opacity:.8}
.line-coverage-summary>span{font-family:inherit;font-size:9px;font-weight:400;color:color-mix(in srgb,var(--fg) 65%,var(--panel))}
.line-coverage-summary .line-coverage-clear{padding:2px}
</style>

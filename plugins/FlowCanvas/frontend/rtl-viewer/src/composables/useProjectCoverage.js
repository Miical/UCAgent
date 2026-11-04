import { onMounted, onBeforeUnmount, watch } from 'vue'
export function useProjectCoverage(store) {
  const view = new URLSearchParams(location.search).get('view')
  const url = view ? new URL(view, location.href) : null
  if (!url || url.origin !== location.origin || !/^\/api\/chat\/projects\/[^/]+\/rtl-view$/.test(url.pathname)) return
  const base = url.pathname.replace(/rtl-view$/, '')
  let timer, busy=false, disposed=false, revision=''
  async function refresh() {
    if (busy || document.hidden) return
    busy=true
    try {
      const response=await fetch(base+'coverage')
      if (!response.ok) throw Error('覆盖率读取失败')
      const data=await response.json()
      if(disposed)return
      store.projectCoverageState=data
      store.projectCoverageInstances=data.report?.instances||[]
      store.projectCoverageError=''
      if(store.projectCoverageMode==='functional'){
        const metadataResponse=await fetch(base+'targets/revision')
        if(!metadataResponse.ok)throw Error('验证目标读取失败')
        const metadata=await metadataResponse.json()
        const next=JSON.stringify(metadata)
        if(next!==revision){
          const targetResponse=await fetch(base+'targets')
          if(!targetResponse.ok)throw Error('验证目标读取失败')
          const targets=await targetResponse.json()
          if(disposed)return
          const groups=targets.targets.filter(t=>t.kind==='group')
          const records=groups.map(t=>targets.coverage?.targets?.[t.id])
          const valid=groups.length>0&&records.every(r=>r?.status==='available')
          const observations=[...new Map(records.flatMap(r=>r?.observations||[]).map(r=>[r.instance+'::'+r.point,r])).values()]
          store.projectFunctionalCoverage={valid,covered:observations.reduce((s,r)=>s+r.covered,0),total:observations.reduce((s,r)=>s+r.total,0)}
          revision=next
        }
      }
    } catch(error) { if(!disposed){store.projectCoverageError=error.message;store.projectFunctionalCoverage={valid:false,covered:0,total:0}} }
    finally{busy=false}
  }
  watch(()=>store.projectCoverageMode,refresh)
  onMounted(()=>{refresh();timer=setInterval(refresh,1500)})
  onBeforeUnmount(()=>{disposed=true;clearInterval(timer)})
}

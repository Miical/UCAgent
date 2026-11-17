/** Fetch published coverage reports for verification lists without polling design canvases. */
import { onMounted, onBeforeUnmount } from 'vue'

/** Keep only the dedicated coverage-list iframe synchronized with report state. */
export function useProjectCoverage(store) {
  const params = new URLSearchParams(location.search)
  if (params.get('coverageOnly') !== '1') return
  const view = params.get('view')
  const url = view ? new URL(view, location.href) : null
  if (!url || url.origin !== location.origin || !/^\/api\/chat\/projects\/[^/]+\/rtl-view$/.test(url.pathname)) return
  const base = url.pathname.replace(/rtl-view$/, '')
  let timer, busy=false, disposed=false
  /** Read only published report data and discard replies after unmount. */
  async function refresh() {
    if (busy || document.hidden) return
    busy=true
    try {
      const response=await fetch(base+'coverage')
      if (!response.ok) throw Error('覆盖率读取失败')
      const data=await response.json()
      if(disposed)return
      store.projectCoverageState=data
      store.projectCoverageError=''
    } catch(error) { if(!disposed)store.projectCoverageError=error.message }
    finally{busy=false}
  }
  onMounted(()=>{refresh();timer=setInterval(refresh,1500)})
  onBeforeUnmount(()=>{disposed=true;clearInterval(timer)})
}

// Paint a bottom-up, shape-clipped coverage layer without altering source fills.
const ns = 'http://www.w3.org/2000/svg'
let sequence = 0
export function paintProjectCoverage(svg, mode, instances, functional, topInstance) {
  if (!svg) return
  svg.querySelectorAll('[data-project-coverage]').forEach(el => el.remove())
  if (mode === 'none') return
  const rows = [...new Map((instances || []).map(row => [row.path, row])).values()]
  for (const group of svg.querySelectorAll('g.node[data-node-id], g.cluster[data-node-id], g.sch-block[data-node-id], g.sch-frame[data-node-id]')) {
    const path = group.getAttribute('data-node-id')
    if(mode === 'functional' && path !== topInstance)continue
    const descendants = mode === 'functional' ? (functional?.valid ? [functional] : []) : rows.filter(row => row.path === path || row.path.startsWith(path + '.'))
    const total = descendants.reduce((sum, row) => sum + row.total, 0)
    const covered = descendants.reduce((sum, row) => sum + row.covered, 0)
    const ratio = total ? Math.min(1, Math.max(0, covered / total)) : mode==='functional' ? 0 : 1
    // Labels in the schematic are separate SVG groups; Graphviz keeps them
    // inside the node. Append a removable tspan so toggles never change labels.
    const own = rows.find(row => row.path === path)
    const labelGroups = [...svg.querySelectorAll('g.sch-label[data-node-id]')]
      .filter(label => label.getAttribute('data-node-id') === path)
    const candidates = labelGroups.length
      ? labelGroups.flatMap(label => [...label.querySelectorAll('text:not(.sch-toggle)')])
      : [...group.querySelectorAll('text')]
    const label = candidates.find(text => text.textContent.trim() === own?.module)
      || candidates.find(text => text.textContent.includes(own?.module || path))
      || candidates[0]
    if (label) {
      const value = document.createElementNS(ns, 'tspan')
      value.setAttribute('data-project-coverage', '')
      value.setAttribute('dx', '6')
      value.setAttribute('font-size', '0.85em')
      value.setAttribute('font-weight', 'normal')
      value.setAttribute('style', 'fill:#16c76a !important')
      value.textContent = total ? (ratio * 100).toFixed(1) + '%' : mode==='functional' ? '—' : 'N/A'
      value.style.cursor='pointer'
      value.style.pointerEvents='auto'
      value.addEventListener('click',event=>{event.stopPropagation();window.parent.postMessage({type:'flowcanvas-open-coverage',mode,instance:path},location.origin)})
      const title = document.createElementNS(ns, 'title')
      title.textContent = total ? (mode==='functional'?'累计功能覆盖率：':'累计行覆盖率（含子模块）：') + covered + '/' + total : mode==='functional' ? '功能覆盖率尚无有效数据' : '当前报告无行覆盖率数据；全绿仅为 N/A 展示，不计入覆盖率统计'
      value.append(title)
      label.append(value)
    }
    const shapes = [...group.children].filter(el => ['polygon','rect','ellipse','path'].includes(el.tagName) && !el.hasAttribute('data-rb-dut-frame'))
    if (!shapes.length) continue
    const bounds = shapes.map(el => el.getBBox())
    const top = Math.min(...bounds.map(b => b.y))
    const bottom = Math.max(...bounds.map(b => b.y + b.height))
    const gradient = document.createElementNS(ns, 'linearGradient')
    const id = 'fc-coverage-' + ++sequence
    for (const [key,value] of Object.entries({ id, gradientUnits:'userSpaceOnUse', x1:0, x2:0, y1:bottom, y2:top, 'data-project-coverage':'' })) gradient.setAttribute(key,value)
    for (const [offset,opacity] of [[0,.25],[ratio,.25],[ratio,0],[1,0]]) {
      const stop=document.createElementNS(ns,'stop')
      stop.setAttribute('offset',offset)
      stop.setAttribute('stop-color','#67c78f')
      stop.setAttribute('stop-opacity',opacity)
      gradient.append(stop)
    }
    svg.prepend(gradient)
    for (const shape of shapes) {
      const tint=shape.cloneNode(false)
      tint.removeAttribute('id')
      tint.removeAttribute('class')
      tint.setAttribute('data-project-coverage','')
      tint.setAttribute('style', 'fill:url(#'+id+') !important;stroke:none !important;pointer-events:none')
      shape.after(tint)
    }
  }
}

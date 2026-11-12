/** Project typed atoms into the same instance/file rows used by line coverage. */
export function coverageMetricRows(report, metric, root) {
  if (metric === 'line') return report?.instances || []
  const summary = report?.code_metrics?.[metric]?.dut
  if (!root || !summary?.items?.length) return []
  const rows=new Map()
  for (const item of summary.items) {
    const parts=(item.hierarchy||'').split('.'), start=parts.indexOf(root)
    const path=start>=0?parts.slice(start).join('.'):root
    if(!rows.has(path))rows.set(path,{path,module:item.module||root,covered:0,total:0,files:new Map()})
    const row=rows.get(path);row.total++;if(item.hits>0)row.covered++
    if (!row.files.has(item.file)) row.files.set(item.file,{path:item.file,sha256:item.sha256,lines:new Map()})
    if (!Number.isInteger(item.line) || item.line < 1) continue
    const file=row.files.get(item.file),line=file.lines.get(item.line)||{covered:0,total:0}
    line.total++;if(item.hits>0)line.covered++
    file.lines.set(item.line,line)
  }
  return [...rows.values()].map(row=>({...row,files:[...row.files.values()].map(file=>({
    path:file.path,sha256:file.sha256,
    covered_lines:[...file.lines].filter(([,v])=>v.covered===v.total).map(([line])=>line),
    uncovered_lines:[...file.lines].filter(([,v])=>v.covered===0).map(([line])=>line),
    partial_lines:[...file.lines].filter(([,v])=>v.covered>0&&v.covered<v.total).map(([line])=>line)
  }))}))
}

/** Expose measured source-line status and native bit/metric atoms without inventing hits. */
export function coverageMetricDetails(report, metric) {
  if (metric !== 'line') {
    const summary=report?.code_metrics?.[metric]?.dut
    return [...(summary?.items||[]),...(summary?.states||[])].map(item=>({...item,name:item.name||item.signal}))
  }
  return (report?.instances||[]).flatMap(row=>(row.files||[]).flatMap(file=>{
    const covered=new Set(file.covered_lines||[]),missed=new Set(file.uncovered_lines||[]),partial=new Set(file.partial_lines||[])
    const lines=new Set([...covered,...missed,...partial,...Object.keys(file.line_hits||{}).map(Number)])
    return [...lines].sort((a,b)=>a-b).map(line=>({
      id:JSON.stringify([row.path,file.path,line]),hierarchy:row.path,module:row.module,
      file:file.path,line,name:'行 '+line,
      status:partial.has(line)?'部分覆盖':missed.has(line)?'未覆盖':covered.has(line)?'已覆盖':
        file.line_hits[line]>0?'已覆盖':'未覆盖'
    }))
  }))
}

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

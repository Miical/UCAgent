/** Semantic module graph layout with routed connections and readable multilingual labels. */
import { instance } from '@viz-js/viz'
let engine
/** Wrap CJK text and unbroken source symbols by their approximate display width. */
function lines(text,limit){const result=[];let line='',width=0;for(const char of String(text||'')){const size=/[^\u0000-\u00ff]/u.test(char)?2:1;if(char==='\n'||width+size>limit){result.push(line);line='';width=0}if(char!=='\n'){line+=char;width+=size}}if(line)result.push(line);return result}
/** Emit only escaped labels; model strings cannot inject Graphviz attributes or markup. */
export function structureDot(structure,direction='LR'){
  const quote=value=>JSON.stringify(String(value)),escape=value=>value.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;')
  const nodes=structure.nodes||[],ids=new Map(nodes.map((node,index)=>[node.id,'n'+index]))
  const width=rows=>Math.max(40,...rows.map(line=>Array.from(line).reduce((size,char)=>size+(/[^\u0000-\u00ff]/u.test(char)?13:6.5),24)))
  const dot=['digraph structure {','graph [rankdir='+(['LR','TB'].includes(direction)?direction:'LR')+',bgcolor="transparent",splines=polyline,nodesep=0.5,ranksep=0.8,pad=0.22,outputorder=edgesfirst];','node [shape=box,style=rounded,fontname="sans-serif",fontsize=12,margin="0.18,0.14",color="#cbd5e1"];','edge [fontname="sans-serif",fontsize=11,arrowsize=0.65,color="#718096",penwidth=1.1];']
  for(const node of nodes){const label=[...lines(node.title,22),...lines(node.detail,28)],shape=({storage:'cylinder',interface:'ellipse'})[node.kind]||'box';dot.push(`${ids.get(node.id)} [label=${quote(label.join('\n'))},width=${(Math.max(150,width(label))/72).toFixed(3)},shape=${shape}];`)}
  for(const edge of structure.edges||[]){if(!ids.has(edge.from)||!ids.has(edge.to))continue;const label=lines(edge.label,24),table=`<TABLE BORDER="0" CELLBORDER="0" CELLPADDING="3" WIDTH="${width(label)}"><TR><TD>${label.map(escape).join('<BR/>')}</TD></TR></TABLE>`,style=({control:'dashed',status:'dotted'})[edge.kind]||'solid';dot.push(`${ids.get(edge.from)} -> ${ids.get(edge.to)} [label=<${table}>,style=${style}];`)}
  dot.push('}');return dot.join('\n')
}
/** Center small graphs and keep dense graphs readable in a bounded scrolling viewport. */
export async function renderStructure(host,structure){
  engine ||= instance()
  const viz=await engine;let svg=viz.renderSVGElement(structureDot(structure));if(!host.isConnected)return
  const available=Math.max(300,host.clientWidth||680),score=element=>Math.max(1,element.viewBox.baseVal.width/available)*Math.max(1,element.viewBox.baseVal.height/400)
  if(svg.viewBox.baseVal.width>available){const vertical=viz.renderSVGElement(structureDot(structure,'TB'));if(score(vertical)<score(svg))svg=vertical}
  svg.classList.add('module-structure-svg');svg.setAttribute('role','img');svg.setAttribute('aria-label','模块功能单元及数据与控制关系')
  const box=svg.viewBox.baseVal;svg.style.width=box.width+'px';svg.style.height=box.height+'px'
  for(const [index,group] of [...svg.querySelectorAll('g.node')].entries()){const item=structure.nodes[index];if(item)group.querySelector('title').textContent=item.title+(item.text?'：'+item.text:'')}
  const frame=document.createElement('div');frame.className='module-structure-viewport';frame.tabIndex=0;frame.setAttribute('aria-label','核心结构图，可滚动查看完整连接');frame.append(svg);host.replaceChildren(frame)
  const kinds=[...new Set((structure.edges||[]).map(edge=>edge.kind||'relation'))]
  if(kinds.length>1){const legend=document.createElement('div');legend.className='module-structure-legend';for(const kind of kinds){const entry=document.createElement('span');entry.className='relation-'+kind;entry.textContent=({data:'数据',control:'控制',status:'状态反馈',relation:'连接'})[kind]||'连接';legend.append(entry)}host.append(legend)}
}

/** Share the measured coverage projection with the host's design subtab. */
export { coverageMetricRows } from './coverageMetricRows.js'

/** Exercise real Graphviz layout for branching, feedback and multilingual labels without a browser. */
import test from 'node:test'
import assert from 'node:assert/strict'
import { instance } from '@viz-js/viz'
import { structureDot } from '../src/overview-structure.js'
const viz=await instance()
for(const count of [1,3,7])test(`layout preserves ${count} functional units and separates node boxes`,()=>{
  const nodes=Array.from({length:count},(_,i)=>({id:'unit'+i,title:'接收控制与错误判定单元'+i,detail:'very_long_signal_without_whitespace_'+i}))
  const edges=nodes.slice(1).map((_,i)=>({from:'unit'+i,to:'unit'+(i+1),label:'数据通路与控制条件',kind:i%2?'control':'data'}))
  if(count>2){edges.push({from:'unit0',to:'unit2',label:'并行数据传递'});edges.push({from:'unit'+(count-1),to:'unit0',label:'状态反馈与访问许可',kind:'status'})}
  const graph=viz.renderJSON(structureDot({nodes,edges}))
  assert.equal(graph.objects.length,count);assert.equal((graph.edges||[]).length,edges.length)
  const boxes=graph.objects.map(n=>{const [x,y]=n.pos.split(',').map(Number);return {x,y,w:Number(n.width)*72,h:Number(n.height)*72}})
  for(let i=0;i<boxes.length;i++)for(let j=i+1;j<boxes.length;j++){const a=boxes[i],b=boxes[j];assert.ok(Math.abs(a.x-b.x)>=(a.w+b.w)/2||Math.abs(a.y-b.y)>=(a.h+b.h)/2)}
  for(const edge of graph.edges||[]){let font=11;for(const draw of edge._ldraw_||[]){if(draw.op==='F')font=draw.size;if(draw.op!=='T')continue;const [px,y]=draw.pt,w=draw.width,h=font,x=px+(draw.align==='l'?w/2:draw.align==='r'?-w/2:0);for(const box of boxes)assert.ok(Math.abs(x-box.x)>=(w+box.w)/2||Math.abs(y+h*.3-box.y)>=(h+box.h)/2)}}

})
test('labels cannot inject extra nodes or HTML into the graph',()=>{
 const graph=viz.renderJSON(structureDot({nodes:[{id:'a',title:'" ]; injected [label="bad',detail:'<script>alert(1)</script>'},{id:'b',title:'目标'}],edges:[{from:'a',to:'b',label:'<TABLE> & "test"'}]}));assert.equal(graph.objects.length,2);assert.equal(graph.edges.length,1)
})

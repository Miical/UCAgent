/** Verify marker commands and precise point navigation without a browser or simulator. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const helper=require('../../../ucagent/server/static/surfer/deep-link.js');
(async()=>{
  const messages=[],listeners={},point={time:'9007199254741001',title:'请求变化',signals:['tb.req']},second={time:'9007199254741010',title:'返回数据',start:'9007199254741005',end:'9007199254741020',signals:['tb.valid','tb.data[7:0]']};
  const api={id_of_name:async()=>7,inject_message:text=>messages.push(JSON.parse(text))};
  let applied;
  const context={URL,location:{origin:'http://localhost',href:'http://localhost/surfer/?analysis_url='+encodeURIComponent('/api/chat/projects/p/test-runs/'+'a'.repeat(32)+'/waveform-analysis')},
    window:{UCAgentSurferDeepLink:{...helper,applyWhenReady:async payload=>{applied=payload;}},addEventListener:(type,fn)=>{listeners[type]=fn;}},parent:{},
    fetch:async()=>({ok:true,json:async()=>({status:'ready',analysis:{begin:'9007199254740000',end:'9007199254741100',signals:['tb.req','tb.valid','tb.data[7:0]'],points:[point,second]}})})};
  vm.createContext(context);
  vm.runInContext(fs.readFileSync('plugins/FlowCanvas/src/flowcanvas/web/wave-viewer.js','utf8'),context);
  await context.window.FlowCanvasWaveViewer(api);
  assert.equal(applied.signals[0],'tb.req');
  assert.equal(messages.some(message=>message.Batch.some(item=>item.AddMarker||item.SetMarker)),false);
  const before=messages.length;
  listeners.message({origin:'http://evil',source:context.parent,data:{type:'flowcanvas-wave-point',index:0}});
  assert.equal(messages.length,before);
  listeners.message({origin:'http://localhost',source:context.parent,data:{type:'flowcanvas-wave-point',index:0}});
  assert.equal(messages.length,before+1);
  assert.deepEqual(messages.at(-1).Batch.find(message=>message.CursorSet).CursorSet,helper.decimalToBigIntJson(point.time));
  listeners.message({origin:'http://localhost',source:context.parent,data:{type:'flowcanvas-wave-point',index:1}});
  await new Promise(setImmediate);
  const batch=messages.at(-1).Batch;
  assert.deepEqual(batch.find(message=>message.RemoveItems).RemoveItems,[7]);
  const variables=batch.find(message=>message.AddVariables).AddVariables;
  assert.deepEqual(variables.map(variable=>variable.name),['valid','data']);
  assert.deepEqual(batch.find(message=>message.CursorSet).CursorSet,helper.decimalToBigIntJson(second.time));
  const zoom=batch.find(message=>message.ZoomToRange).ZoomToRange;
  assert.deepEqual(zoom.start,helper.decimalToBigIntJson(second.start));
  assert.deepEqual(zoom.end,helper.decimalToBigIntJson(second.end));
  assert.equal(zoom.viewport_idx,0);
  assert.equal(messages[0].Batch[0].SelectTheme,'light-high-contrast');
  const autoZoom=messages.find(message=>message.Batch.some(item=>item.ZoomToRange)).Batch.find(message=>message.ZoomToRange).ZoomToRange;
  assert.deepEqual(autoZoom.start,helper.decimalToBigIntJson('9007199254740992'));
  assert.deepEqual(autoZoom.end,helper.decimalToBigIntJson('9007199254741010'));
  console.log('Per-point signals, local zoom and precise navigation OK');
})().catch(error=>{console.error(error);process.exitCode=1;});

/** Verify marker commands and precise point navigation without a browser or simulator. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const helper=require('../../../ucagent/server/static/surfer/deep-link.js');
(async()=>{
  const messages=[],listeners={},point={time:'9007199254741001',title:'请求变化',signals:['tb.req']},second={time:'9007199254741010',title:'返回数据',start:'9007199254741005',end:'9007199254741020',signals:['tb.valid','tb.data[7:0]']};
  const api={id_of_name:async()=>7,index_of_name:async name=>['tb.req','tb.valid','tb.data'].indexOf(name),inject_message:text=>messages.push(JSON.parse(text))};
  let applied;
  const context={URL,location:{origin:'http://localhost',href:'http://localhost/surfer/?analysis_url='+encodeURIComponent('/api/chat/projects/p/test-runs/'+'a'.repeat(32)+'/waveform-analysis')},
    window:{UCAgentSurferDeepLink:{...helper,applyWhenReady:async payload=>{applied=payload;}},addEventListener:(type,fn)=>{listeners[type]=fn;}},parent:{},
    fetch:async()=>({ok:true,json:async()=>({status:'ready',analysis:{begin:'9007199254740000',end:'9007199254741100',signals:['tb.req','tb.valid','tb.data[7:0]'],points:[point,second]}})})};
  vm.createContext(context);
  vm.runInContext(fs.readFileSync('plugins/FlowCanvas/src/flowcanvas/web/wave-viewer.js','utf8'),context);
  await context.window.FlowCanvasWaveViewer(api);
  assert.deepEqual(Array.from(applied.signals),['tb.req','tb.valid','tb.data[7:0]']);
  assert.equal(messages.some(message=>message.Batch.some(item=>item.AddMarker||item.SetMarker)),false);
  const before=messages.length;
  listeners.message({origin:'http://evil',source:context.parent,data:{type:'flowcanvas-wave-point',index:0}});
  assert.equal(messages.length,before);
  listeners.message({origin:'http://localhost',source:context.parent,data:{type:'flowcanvas-wave-point',index:0}});
  await new Promise(setImmediate);
  assert.equal(messages.length,before+1);
  assert.deepEqual(messages.at(-1).Batch.find(message=>message.CursorSet).CursorSet,helper.decimalToBigIntJson(point.time));
  listeners.message({origin:'http://localhost',source:context.parent,data:{type:'flowcanvas-wave-point',index:1}});
  await new Promise(setImmediate);
  const batch=messages.at(-1).Batch;
  assert.equal(batch.some(message=>message.RemoveItems||message.AddVariables),false);
  assert.deepEqual(batch.filter(message=>message.SetItemSelected).map(message=>message.SetItemSelected),[[1,true],[2,true]]);
  assert.equal(batch.find(message=>message.ItemColorChange?.[0]?.Explicit===0).ItemColorChange[1],null);
  assert.equal(batch.find(message=>message.ItemColorChange?.[0]?.Explicit===1).ItemColorChange[1],'blue');
  assert.equal(messages.flatMap(message=>message.Batch||[]).some(message=>message.ItemNameChange),false);
  assert.equal(batch.some(message=>message.AddGraphic),false);
  assert.deepEqual(batch.find(message=>message.CursorSet).CursorSet,helper.decimalToBigIntJson(second.time));
  assert.deepEqual(batch.find(message=>message.GoToTime).GoToTime,[helper.decimalToBigIntJson(second.time),0]);
  assert.equal(messages.flatMap(message=>message.Batch||[]).some(message=>message.ZoomToRange),false);
  assert.equal(messages[0].Batch[0].SelectTheme,'light-high-contrast');
  const count=messages.length;
  listeners.message({origin:'http://localhost',source:context.parent,data:{type:'flowcanvas-wave-point',index:0}});
  listeners.message({origin:'http://localhost',source:context.parent,data:{type:'flowcanvas-wave-point',index:1}});
  await new Promise(setImmediate);
  assert.equal(messages.length,count+1);
  assert.deepEqual(messages.at(-1).Batch.filter(message=>message.SetItemSelected).map(message=>message.SetItemSelected),[[1,true],[2,true]]);
  console.log('All AI signals, active-group highlights, preserved scale and precise navigation OK');
})().catch(error=>{console.error(error);process.exitCode=1;});

/** Embedded controls expose a bounded protocol and hide all standalone application chrome. */
(async()=>{
  const messages=[],posted=[],listeners={},parent={postMessage:message=>posted.push(message)};
  const query=new URLSearchParams({embedded:'1',signals:JSON.stringify(['tb.clk']),analysis_url:'/api/chat/projects/p/test-runs/'+'a'.repeat(32)+'/waveform-analysis'});
  const context={URL,location:{origin:'http://localhost',href:'http://localhost/surfer/?'+query},parent,
    window:{UCAgentSurferDeepLink:{...helper,applyWhenReady:async()=>{}},addEventListener:(name,fn)=>{listeners[name]=fn;},on_surfer_error:error=>{throw Error(error);}},
    fetch:async()=>({ok:true,json:async()=>({status:'empty',analysis:null})})};
  vm.createContext(context);vm.runInContext(fs.readFileSync('plugins/FlowCanvas/src/flowcanvas/web/wave-viewer.js','utf8'),context);
  await context.window.FlowCanvasWaveViewer({inject_message:message=>messages.push(JSON.parse(message)),id_of_name:async()=>1,index_of_name:async()=>0});
  const settings=messages[0].Batch;
  for(const name of ['SetMenuVisible','SetToolbarVisible','SetOverviewVisible','SetStatusbarVisible','SetSidePanelVisible'])assert.equal(settings.find(item=>name in item)[name],false);
  assert.equal(posted[0].type,'flowcanvas-wave-ready');assert.deepEqual(Array.from(posted[0].signals),['tb.clk']);
  const control=(action,extra={})=>listeners.message({origin:'http://localhost',source:parent,data:{type:'flowcanvas-wave-control',action,...extra}});
  control('zoom-in');assert.equal(messages.at(-1).CanvasZoom.delta,.5);control('fit');assert.equal(messages.at(-1).ZoomToFit.viewport_idx,0);
  control('unit',{unit:'PicoSeconds'});assert.equal(messages.at(-1).SetTimeUnit,'PicoSeconds');const before=messages.length;control('unit',{unit:'unknown'});control('signals',{signals:[]});assert.equal(messages.length,before);
  control('signals',{signals:['tb.req','tb.ready']});await new Promise(setImmediate);assert.equal(messages.slice(before).flatMap(message=>message.Batch||[]).find(item=>item.AddVariables).AddVariables.length,2);
  console.log('Embedded chrome, readiness, signal replacement and bounded controls OK');
})().catch(error=>{console.error(error);process.exitCode=1;});

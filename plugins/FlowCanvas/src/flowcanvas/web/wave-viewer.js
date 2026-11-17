/** Report renderer boot failures before the WASM setup hook becomes available. */
if(new URL(location.href).searchParams.get('embedded')==='1'){
  const report=message=>parent.postMessage({type:'flowcanvas-wave-error',message:String(message||'绘图引擎加载失败')},location.origin);
  window.addEventListener('error',event=>report(event.message));
  window.addEventListener('unhandledrejection',event=>report(event.reason?.message||event.reason));
  // This renderer stays bound to the run selected by FlowCanvas. Other files
  // and the standalone command prompt belong in the full-viewer escape hatch.
  window.addEventListener('pointerdown',()=>parent.postMessage({type:'flowcanvas-wave-interaction'},location.origin),true);
  window.addEventListener('drop',event=>{if(event.dataTransfer?.files?.length){event.preventDefault();event.stopImmediatePropagation();}},true);
  window.addEventListener('keydown',event=>{const key=event.key.toLowerCase();if(((event.ctrlKey||event.metaKey)&&key==='o')||(event.altKey&&key==='m')||(!event.ctrlKey&&!event.metaKey&&!event.altKey&&['b','t',' '].includes(key))){event.preventDefault();event.stopImmediatePropagation();}},true);
}
/** Show per-point signal groups and cursor positions without adding annotation rows. */
window.FlowCanvasWaveViewer=async function(api){
  const current=new URL(location.href),embedded=current.searchParams.get('embedded')==='1',reference=current.searchParams.get('analysis_url');
  const settings=[{SelectTheme:'light-high-contrast'}];
  if(embedded)settings.push({SetMenuVisible:false},{SetToolbarVisible:false},{SetOverviewVisible:false},{SetStatusbarVisible:false},{SetSidePanelVisible:false},{SetShowIndices:false},{SetNameAlignRight:false},{SetTimeUnit:'Auto'},{SetUIZoomFactor:.95});
  else settings.push({SetMenuVisible:true},{SetToolbarVisible:true},{SetOverviewVisible:true},{SetStatusbarVisible:true},{SetSidePanelVisible:true});
  api.inject_message(JSON.stringify({Batch:settings}));
  if(!reference)return;
  const url=new URL(reference,location.origin);
  if(url.origin!==location.origin||!/^\/api\/chat\/projects\/[^/]+\/test-runs\/[a-f0-9]{32}\/waveform-analysis$/.test(url.pathname))throw Error('Invalid analysis URL');
  const response=await fetch(url,{cache:'no-store'}),saved=await response.json();
  if(!response.ok)throw Error('无法读取波形解读');
  const analysis=saved.status==='ready'&&saved.analysis?saved.analysis:{}, {points=[],begin,end}=analysis,helper=window.UCAgentSurferDeepLink;
  const aiSignals=[...new Set([...(analysis.signals||[]),...points.flatMap(point=>point.signals)])];
  let currentSignals=aiSignals.length?aiSignals:(embedded?JSON.parse(current.searchParams.get('signals')||'[]'):[]),activeSignals=[],activePoint=points.length?0:-1,focusVersion=0;
  if(!currentSignals.length||currentSignals.length>32||currentSignals.some(name=>typeof name!=='string'||!name))return;
  await helper.applyWhenReady({signals:currentSignals},api);
  const pointGuides=embedded?window.FlowCanvasWavePoints?.(api,points,end):null;
  /** Replace waveform rows atomically and discard an older asynchronous selection. */
  async function replaceSignals(names,version,messages){
    if(JSON.stringify(currentSignals)===JSON.stringify(names))return true;
    const ids=await Promise.all(currentSignals.map(name=>api.id_of_name(helper.variableDisplayName(helper.variableRef(name)))));
    if(version!==focusVersion)return false;
    const references=names.map(helper.variableRef),existing=ids.filter(id=>id!==undefined&&id!==null);
    if(existing.length)messages.push({RemoveItems:existing});
    messages.push({SetActiveScope:{WaveScope:references[0].path}},{ForceVariableNameTypes:'Global'},{AddVariables:references});
    currentSignals=[...names];activeSignals=[];return true;
  }
  /** Emphasize the current signal group without renaming or removing other traces. */
  async function highlight(names,version,messages,pointIndex=activePoint){
    const rows=await Promise.all(currentSignals.map(async name=>({name,index:await api.index_of_name(helper.variableDisplayName(helper.variableRef(name)))})));
    if(version!==focusVersion)return false;
    messages.push('ItemSelectionClear','UnfocusItem');
    for(const {name,index} of rows){
      if(index===undefined||index===null)continue;
      const active=names.includes(name);
      if(active!==activeSignals.includes(name))messages.push({ItemColorChange:[{Explicit:index},active?'blue':null]});
      if(active)messages.push({SetItemSelected:[index,true]});
    }
    activeSignals=names.filter(name=>currentSignals.includes(name));activePoint=pointIndex;pointGuides?.select(pointIndex);return true;
  }
  /** Keep every AI trace visible while focusing the selected point's group and time window. */
  async function focus(point){
    const version=++focusVersion,messages=[];
    if(!await replaceSignals([...new Set([...aiSignals,...currentSignals])].slice(0,32),version,messages))return;
    if(messages.length){api.inject_message(JSON.stringify({Batch:messages}));messages.length=0;}
    if(!await highlight(point.signals,version,messages,points.indexOf(point)))return;
    // Keep the user's scale while centering the selected time.
    messages.push({GoToTime:[helper.decimalToBigIntJson(point.time),0]});
    messages.push({CursorSet:helper.decimalToBigIntJson(point.time)});
    api.inject_message(JSON.stringify({Batch:messages}));
    if(embedded)parent.postMessage({type:'flowcanvas-wave-signals',signals:currentSignals,activeSignals},location.origin);
  }
  /** Accept only a small control protocol from the same-origin owning panel. */
  window.addEventListener('message',event=>{
    if(event.origin!==location.origin||event.source!==parent)return;
    const data=event.data,report=error=>window.on_surfer_error(error.message);
    if(data?.type==='flowcanvas-wave-point'){const point=points[data.index];if(point)focus(point).catch(report);return;}
    if(!embedded||data?.type!=='flowcanvas-wave-control')return;
    const simple={fit:{ZoomToFit:{viewport_idx:0}},'zoom-in':{CanvasZoom:{mouse_ptr:null,delta:.5,viewport_idx:0}},'zoom-out':{CanvasZoom:{mouse_ptr:null,delta:2,viewport_idx:0}}};
    if(simple[data.action]){api.inject_message(JSON.stringify(simple[data.action]));return;}
    if(data.action==='unit'&&['Auto','NanoSeconds','PicoSeconds','MicroSeconds','MilliSeconds'].includes(data.unit)){api.inject_message(JSON.stringify({SetTimeUnit:data.unit}));return;}
    if(['signals','highlight'].includes(data.action)&&Array.isArray(data.signals)&&data.signals.length>0&&data.signals.length<=32&&new Set(data.signals).size===data.signals.length&&data.signals.every(name=>typeof name==='string'&&name.length>0&&name.length<=4096)){
      const version=++focusVersion,messages=[],names=data.action==='highlight'?[...new Set([...currentSignals,...data.signals])]:data.signals;
      if(names.length>32)return;
      (async()=>{if(!await replaceSignals(names,version,messages))return;if(messages.length){api.inject_message(JSON.stringify({Batch:messages}));messages.length=0;}if(!await highlight(data.action==='highlight'?data.signals:[],version,messages))return;api.inject_message(JSON.stringify({Batch:messages}));parent.postMessage({type:'flowcanvas-wave-signals',signals:currentSignals,activeSignals},location.origin);})().catch(report);
    }
  });
  if(points.length)await focus(points[0]);else api.inject_message(JSON.stringify({ZoomToFit:{viewport_idx:0}}));
  if(embedded)parent.postMessage({type:'flowcanvas-wave-ready',signals:currentSignals,activeSignals},location.origin);
};

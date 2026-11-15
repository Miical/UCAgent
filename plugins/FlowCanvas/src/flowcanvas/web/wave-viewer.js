/** Show per-point signal groups and cursor positions without adding annotation rows. */
window.FlowCanvasWaveViewer=async function(api){
  api.inject_message(JSON.stringify({Batch:[{SelectTheme:'light-high-contrast'}]}));
  const current=new URL(location.href),reference=current.searchParams.get('analysis_url');
  if(!reference)return;
  const url=new URL(reference,location.origin);
  if(url.origin!==location.origin||!/^\/api\/chat\/projects\/[^/]+\/test-runs\/[a-f0-9]{32}\/waveform-analysis$/.test(url.pathname))throw Error('Invalid analysis URL');
  const response=await fetch(url,{cache:'no-store'}),saved=await response.json();
  if(!response.ok)throw Error('无法读取波形解读');
  if(saved.status!=='ready'||!saved.analysis?.points?.length)return;
  const {points,begin,end}=saved.analysis,helper=window.UCAgentSurferDeepLink;
  let currentSignals=points[0].signals,focusVersion=0;
  await helper.applyWhenReady({signals:currentSignals},api);
  /** Replace the prior point's signal rows before moving to the new native-tick position. */
  async function focus(point){
    const version=++focusVersion,messages=[];
    if(JSON.stringify(currentSignals)!==JSON.stringify(point.signals)){
      const ids=await Promise.all(currentSignals.map(name=>api.id_of_name(helper.variableDisplayName(helper.variableRef(name)))));
      if(version!==focusVersion)return;
      const references=point.signals.map(helper.variableRef);
      const existing=ids.filter(id=>id!==undefined&&id!==null);
      if(existing.length)messages.push({RemoveItems:existing});
      messages.push({SetActiveScope:{WaveScope:references[0].path}},{ForceVariableNameTypes:'Global'},{AddVariables:references});
      currentSignals=point.signals;
    }
    const time=BigInt(point.time),lo=BigInt(begin),hi=BigInt(end);
    let start,stop;
    if(point.start!=null&&point.end!=null){start=BigInt(point.start);stop=BigInt(point.end);}
    else{
      // Neighbor spacing supplies local context; cap it against the real recording duration.
      let radius=(hi-lo)/50n;if(radius<1n)radius=1n;
      for(const other of points){const gap=BigInt(other.time)>time?BigInt(other.time)-time:time-BigInt(other.time);if(gap>0n&&gap<radius)radius=gap;}
      start=time-radius;stop=time+radius;
    }
    if(start<lo)start=lo;if(stop>hi)stop=hi;
    if(stop>start)messages.push({ZoomToRange:{start:helper.decimalToBigIntJson(String(start)),end:helper.decimalToBigIntJson(String(stop)),viewport_idx:0}});
    messages.push({CursorSet:helper.decimalToBigIntJson(point.time)});
    api.inject_message(JSON.stringify({Batch:messages}));
  }
  window.addEventListener('message',event=>{if(event.origin===location.origin&&event.source===parent&&event.data?.type==='flowcanvas-wave-point'){const point=points[event.data.index];if(point)focus(point).catch(error=>window.on_surfer_error(error.message));}});
  await focus(points[0]);
};

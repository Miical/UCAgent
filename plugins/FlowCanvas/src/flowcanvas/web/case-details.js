/** Shared AI action button, hover animation and scoped task preview. */
let flowCanvasTaskIconSequence=0;
window.FlowCanvasCaseTaskButton=function(kind,label,scope){
  const el=(tag,cls)=>{const node=document.createElement(tag);if(cls)node.className=cls;return node;};
  const gradientId='case-task-spark-'+(++flowCanvasTaskIconSequence);
  const task=el('button','ai-case-add detail-ai-task');task.type='button';task.title='AI '+label;task.setAttribute('aria-label','AI '+label);
  task.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><defs><linearGradient id="'+gradientId+'" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#4285ff"/><stop offset=".55" stop-color="#9164ec"/><stop offset="1" stop-color="#df77ba"/></linearGradient></defs><path d="M12 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-6M8 10h3M8 14h8M8 18h5"/><path class="ai-colored-spark" d="M18 2l1.2 3.8L23 7l-3.8 1.2L18 12l-1.2-3.8L13 7l3.8-1.2Z" fill="url(#'+gradientId+')" stroke="url(#'+gradientId+')"/></svg>';
  const caption=el('span');caption.textContent=label;task.append(caption);
  const open=preview=>{if(!task.disabled)document.dispatchEvent(new CustomEvent('flowcanvas-case-task',{detail:{...scope,anchor:task,preview}}));};
  let timer;task.onmouseenter=()=>{clearTimeout(timer);timer=setTimeout(()=>{if(task.isConnected&&task.matches(':hover'))open(true);},220);};task.onmouseleave=()=>clearTimeout(timer);
  task.onclick=event=>{event.stopPropagation();event.preventDefault();clearTimeout(timer);open(false);};return task;
};


/** Build continuous document sections and remember collapse state for each inspector. */
window.FlowCanvasDetailSection=function(owner,label,key=label){
  const states=owner.flowCanvasSections||(owner.flowCanvasSections=new Map());
  const section=document.createElement('details'),heading=document.createElement('summary'),caption=document.createElement('span');
  section.className='case-section module-section detail-section';heading.className='module-section-heading';caption.textContent=label;heading.append(caption);section.append(heading);
  section.open=states.get(key)!==false;section.ontoggle=()=>states.set(key,section.open);return section;
};

/** Present both directions of coverage association as the same flat, navigable list. */
window.FlowCanvasCoverageGroup=function(container,{label,kind}){
  const el=(tag,cls,text)=>{const node=document.createElement(tag);if(cls)node.className=cls;if(text!==undefined)node.textContent=text;return node;};
  const group=el('div','coverage-link-group');
  group.dataset.kind=kind;
  const count=el('span','coverage-link-count'),body=el('div','coverage-link-body');
  if(label){
    const heading=el('div','coverage-link-heading');
    heading.append(el('h5','',label),count);
    group.append(heading);
  }
  group.append(body);container.append(group);
  function list(rows,nested=false){
    const entries=el('ul','coverage-link-list'+(nested?' coverage-link-children':''));
    for(const row of rows){
      const entry=el('li','coverage-link-entry');
      const link=el('button','coverage-link-row');
      link.type='button';link.disabled=!row.open;
      if(row.open)link.onclick=row.open;
      if(row.caseResult){
        const badge=el('span','case-result-badge');
        badge.dataset.caseResult=row.caseResult;
        badge.dataset.status='neutral';
        link.append(badge);
      }
      link.append(el('span','coverage-link-name',row.label));
      if(row.kind)link.append(el('span','coverage-link-kind',row.kind));
      const arrow=el('span','coverage-link-arrow');
      arrow.innerHTML='<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8h9m-4-4 4 4-4 4"/></svg>';
      link.append(arrow);entry.append(link);
      if(row.description)entry.append(el('p','coverage-link-description',row.description));
      if(row.children?.length)entry.append(list(row.children,true));
      entries.append(entry);
    }
    return entries;
  }
  function render(rows,message,total=rows.length){
    count.textContent=total||'';
    body.replaceChildren(rows.length?list(rows):el('p','coverage-link-empty',message));
  }
  render([],'正在读取…');
  return render;
};

/** Place a short binding near the upper third, or show its start with a few lines of context. */
window.FlowCanvasScrollToSourceLine=function(panel,row,endRow=row,{pad=true}={}){
  if(!row)return;
  requestAnimationFrame(()=>{
    if(!panel.contains(row)||!row.getClientRects().length)return;
    panel.classList.remove('source-line-centered');
    const height=panel.clientHeight;
    const rowRect=row.getBoundingClientRect();
    const offset=panel.scrollTop+rowRect.top-panel.getBoundingClientRect().top-panel.clientTop;
    const context=Math.min(48,height/5);
    const rangeHeight=endRow.getBoundingClientRect().bottom-rowRect.top;
    if(rangeHeight>=height){
      panel.scrollTop=Math.max(0,offset-context);
      return;
    }
    const anchor=Math.max(height/3,rangeHeight/2);
    if(!pad){
      panel.scrollTop=Math.max(0,offset+rangeHeight/2-anchor);
      return;
    }
    const midpoint=rowRect.top+rangeHeight/2;
    const contentEnd=panel.lastElementChild.getBoundingClientRect().bottom;
    const before=Math.ceil(Math.max(0,anchor-offset-rangeHeight/2));
    const after=Math.ceil(Math.max(0,height-anchor-contentEnd+midpoint));
    panel.style.setProperty('--source-jump-before',before+'px');
    panel.style.setProperty('--source-jump-after',after+'px');
    panel.classList.add('source-line-centered');
    panel.scrollTop=Math.max(0,offset+before+rangeHeight/2-anchor);
  });
};

/** Selected-case inspector with independently selected source bindings and real execution records. */
window.FlowCanvasCaseDetails=function(host,project,createRunButton){
  const el=(tag,cls,text)=>{const node=document.createElement(tag);if(cls)node.className=cls;if(text!==undefined)node.textContent=text;return node;};
  let selected=null,report=null,tab='overview',runId=null,sourceId=null,revision='',generation=0,offset=0,outputRequest=null;
  let wavePanel=null,waveExplanation=null;
  let coverageSections=null,coveragePolling=false;
  let runs=[],log=null,resultNote=null,runCommand=null,runSelect=null,runBadge=null,wavePath='',waveRevision='',wavePolling=false;
  const positions=new Map(),tabPrefix='case-detail-'+Math.random().toString(36).slice(2)+'-';
  const names={overview:'概览',source:'源码',runs:'运行记录',wave:'波形'};
  const statusNames={starting:'启动中',running:'运行中',completed:'已结束',failed:'运行失败',interrupted:'已中断'};
  const stepStatus={planned:'待执行',observed:'已观察',passed:'通过',failed:'失败',skipped:'已跳过',unknown:'未确认'};
  const title=el('header','case-detail-title'),tabs=el('nav','case-detail-tabs'),body=el('div','case-detail-body');
  tabs.setAttribute('role','tablist');tabs.setAttribute('aria-label','测试用例详情');host.append(title,tabs,body);
  for(const [key,name]of Object.entries(names)){
    const button=el('button','',name);button.type='button';button.setAttribute('role','tab');button.id=tabPrefix+key;button.onclick=()=>{tab=key;render();};
    button.onkeydown=event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();const keys=Object.keys(names),index=keys.indexOf(tab);tab=event.key==='Home'?keys[0]:event.key==='End'?keys.at(-1):keys[(index+(event.key==='ArrowRight'?1:-1)+keys.length)%keys.length];render();document.getElementById(tabPrefix+tab).focus();};tabs.append(button);
  }
  /** Announce only visible selection metadata, without model calls or implicit execution. */
  function publish(extra={}){
    if(!selected)return;
    const position={tab,run_id:runId,binding_id:tab==='source'?sourceId:null,...extra};
    positions.set(selected.id,position);
    document.dispatchEvent(new CustomEvent('flowcanvas-case-view',{detail:{case_id:selected.id,view:position}}));
  }
  /** Read JSON with actionable errors and no untrusted HTML insertion. */
  async function json(url,options){const response=await fetch(url,options);const value=await response.json();if(!response.ok)throw Error(typeof value.detail==='string'?value.detail:'无法读取数据');return value;}
  /** Build a project-scoped URL from the current immutable selection. */
  function base(){return '/api/chat/projects/'+encodeURIComponent(project().id);}
  /** Create the shared flat section used throughout case details. */
  function section(label,key=label){const node=label==='波形'?el('section','case-section'):window.FlowCanvasDetailSection(host,label,selected.id+':'+tab+':'+key);if(label==='波形')node.append(el('h4','module-section-heading',label));body.append(node);if(label==='AI 解读'&&tab==='wave'){waveExplanation=node;window.FlowCanvasCaseWorkspace?.setWaveAnalysis(project().id,selected.id,node);}return node;}
  /** Render a labeled value using the shared section layout. */
  function field(label,value,code=false){section(label).append(el(code?'pre':'p','',value||'尚未填写'));}
  function coverageTree(render,rows,empty){
    const ids=new Set(rows.map(row=>row.id));
    const children=new Map();
    for(const row of rows){
      const parent=ids.has(row.parent)?row.parent:null;
      if(!children.has(parent))children.set(parent,[]);
      children.get(parent).push(row);
    }
    const build=parent=>(children.get(parent)||[]).map(row=>({
      label:row.name,
      kind:({group:'功能组',point:'功能点',testpoint:'测试点'})[row.kind],
      open:row.available===false?null:()=>document.dispatchEvent(new CustomEvent('flowcanvas-case-restore',{
        detail:{project_id:project().id,case_id:row.id,coverageLink:true}
      })),
      children:build(row.id)
    }));
    render(build(null),empty,rows.length);
  }
  function showCoverageOverview(){
    const content=el('div','coverage-link-groups');
    section('关联验证目标').append(content);
    coverageSections={
      planned:window.FlowCanvasCoverageGroup(content,{label:'计划覆盖',kind:'planned'}),
      actual:window.FlowCanvasCoverageGroup(content,{label:'本批实际命中',kind:'actual'}),
      signature:''
    };
    refreshCoverageLinks();
  }
  function showRunCoverage(){
    const content=el('div','coverage-link-groups');
    section('本次实际命中').append(content);
    coverageSections={actual:window.FlowCanvasCoverageGroup(content,{kind:'actual'}),signature:''};
    refreshCoverageLinks();
  }
  async function refreshCoverageLinks(){
    if(!coverageSections||coveragePolling)return;
    const parts=coverageSections,overview=!!parts.planned;
    const endpoint=overview?'/testcases/'+encodeURIComponent(selected.id):'/test-runs/'+encodeURIComponent(runId);
    coveragePolling=true;
    try{
      const result=await json(base()+endpoint+'/coverage-links');
      if(parts!==coverageSections)return;
      const signature=JSON.stringify(result);
      if(parts.signature===signature)return;
      parts.signature=signature;
      if(overview)coverageTree(parts.planned,result.planned,'尚未登记计划覆盖目标');
      const empty=({pending:'运行结束后更新',unavailable:'暂无覆盖数据',partial:'暂无可确认的命中'})[result.status]||'暂无命中';
      coverageTree(parts.actual,overview?result.actual:result.targets,empty);
    }catch(error){
      if(parts===coverageSections&&!parts.signature){
        parts.planned?.([],'暂时无法读取覆盖关联');
        parts.actual([],'暂时无法读取覆盖关联');
      }
    }finally{
      coveragePolling=false;
      if(parts!==coverageSections&&coverageSections)refreshCoverageLinks();
    }
  }
  const coveragePoll=setInterval(()=>{if(!document.hidden&&!host.closest('[hidden]'))refreshCoverageLinks();},2500);
  function overviewBlock(data){
    for(const [label,value] of [['验证目的',data.purpose],['验证方法',data.method]]){
      section(label).append(el('p',value?'':'case-detail-empty',value||'待补充'));
    }
  }
  /** Bind the shared action button to the currently selected test case. */
  function taskButton(kind,label,extra={}){return window.FlowCanvasCaseTaskButton(kind,label,{project:project(),testcase:selected,kind,...extra});}
  /** Open a cited current-source range in the existing viewer, scoped to this project. */
  function sourceReference(file,line,end){
    const label=file+(line?':'+line+(end&&end!==line?'-'+end:''):'');
    const link=el('button','case-story-source',label);link.type='button';
    const root=project().path;
    link.title='查看当前源码 · '+label;
    link.onclick=async()=>{
      try{const {openLocalSource}=await import('./markdown-renderer.js');await openLocalSource({path:file.startsWith('/')?file:root+'/'+file,line,end});}
      catch(error){link.title='无法打开源码：'+error.message;link.textContent=label+' · 打开失败';}
    };
    return link;
  }
  /** Present the explanation as one continuous story instead of nested cards. */
  function timeline(){
    const steps=Array.isArray(selected.timeline)?selected.timeline:[];
    const flow=section('技术流程');
    if(!steps.length){flow.append(el('p','case-detail-empty','待补充'));return;}
    const story=el('div','case-story');
    // The root question is already shown in the fixed overview; timeline starts at real phases.
    const phases=steps.length===1&&steps[0].children?.length?steps[0].children:steps;
    function flatten(items,prefix=''){
      for(const [index,step] of items.entries()){
        const number=prefix?prefix+'.'+(index+1):String(index+1);
        const row=el('article','case-story-row');row.dataset.depth=prefix?'1':'0';
        const head=el('div','case-story-head');head.append(el('span','case-story-number',number),el('strong','',step.title||'未命名步骤'));row.append(head);
        if(step.description)row.append(el('p','case-story-description',step.description));
        if(step.why){const line=el('p','case-story-line');line.append(el('b','','为什么：'),document.createTextNode(step.why));row.append(line);}
        if(step.how){const line=el('p','case-story-line');line.append(el('b','','具体怎么做：'),document.createTextNode(step.how));row.append(line);}
        if(step.actors?.length){const line=el('p','case-story-line');line.append(el('b','','涉及：'),document.createTextNode(step.actors.join('、')));row.append(line);}
        if(step.assignments?.length){
          const block=el('div','case-story-assignments');block.append(el('div','case-story-label','关键实现'));
          for(const assignment of step.assignments){const detail=el('div','case-story-assignment');const code=el('code','',assignment.expression);const meaning=el('span','',assignment.meaning);detail.append(code,meaning);if(assignment.file)detail.append(sourceReference(assignment.file,assignment.line));block.append(detail);}row.append(block);
        }
        if(step.expected){const line=el('p','case-story-line');line.append(el('b','','预期观察：'),document.createTextNode(step.expected));row.append(line);}
        if(step.evidence?.length){
          const evidence=el('div','case-story-evidence');
          for(const item of step.evidence){
            const entry=el('div','case-story-citation');
            const kind=({source:'源码',log:'日志',waveform:'波形',assertion:'断言',coverage:'覆盖率'})[item.kind]||'依据';
            entry.append(el('span','',kind+' · '+item.label));
            if(item.path)entry.append(item.kind==='source'?sourceReference(item.path,item.line,item.end_line):el('code','',item.path+(item.line?':'+item.line:'')));
            evidence.append(entry);
          }
          row.append(evidence);
        }else row.append(el('p','case-story-evidence','尚未附证据，需核对上述解释。'));
        story.append(row);flatten(step.children||[],number);
      }
    }
    flatten(phases);flow.append(story);
  }
  /** Choose a run explicitly; never silently substitute another case's artifacts. */
  async function loadRuns(token){
    const rows=await json(base()+'/test-runs?case_id='+encodeURIComponent(selected.id)+'&include_smoke=true');if(token!==generation)return false;
    runs=rows.filter(row=>row.case_id===selected.id);
    if(!runs.some(row=>row.id===runId))runId=runs[0]?.id||null;publish();
    return true;
  }
  /** Populate one shared run selector for the log and waveform tabs. */
  function runLabel(row,index){
    const time=new Date(row.started*1000).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false});
    return time+' · '+(statusNames[row.status]||row.status)+(index===0?' · 最近':'');
  }
  function updateRunBadge(row){
    runBadge.textContent=statusNames[row?.status]||row?.status||'';
    runBadge.dataset.status=row?.status||'';
  }
  function picker(){
    const picker=el('div','case-run-picker'),label=el('label','case-run-picker-label','运行记录');
    runSelect=el('select','case-detail-select case-run-select');runSelect.ariaLabel='选择本用例的运行记录';
    for(const [index,row] of runs.entries()){
      const option=el('option','',runLabel(row,index));option.value=row.id;runSelect.append(option);
    }
    runSelect.value=runId;runSelect.onchange=()=>{runId=runSelect.value;render();};
    runBadge=el('span','case-run-status');updateRunBadge(runs.find(row=>row.id===runId));
    label.append(runSelect);picker.append(label,runBadge);body.append(picker);
  }
  /** Show every related source file once, highlighting all of its bound fragments. */
  async function source(token){
    const bindings=report?.bindings||[];
    if(!bindings.length){body.append(el('p','case-detail-empty','尚无源码绑定'));return;}
    if(!bindings.some(binding=>binding.id===sourceId))sourceId=bindings.find(binding=>binding.role==='definition')?.id||bindings[0].id;
    const results=await Promise.allSettled(bindings.map(binding=>json(base()+'/testcases',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({operation:'inspect',binding_id:binding.id})})));
    if(token!==generation)return;
    let selectedRow,selectedEndRow;
    const files=new Map(),blocks=[],roles={definition:'用例定义',stimulus:'激励与约束',check:'检查逻辑',coverage:'覆盖采样',configuration:'环境与配置',rtl:'RTL 实现'};
    results.forEach((result,index)=>{const binding=bindings[index];if(result.status==='rejected'){const part=section(roles[binding.role]||'源码');part.append(el('p','targets-error',binding.file+'：'+result.reason.message));return;}const source=result.value.source;if(!files.has(source.file))files.set(source.file,[]);files.get(source.file).push({...source,id:binding.id,role:binding.role});});
    for(const [file,ranges] of files){
      const part=section([...new Set(ranges.map(range=>roles[range.role]||'源码'))].join(' · '),file);part.classList.add('case-section-source');part.querySelector('summary').append(el('code','',file));
      const pre=el('pre','case-detail-source');pre.tabIndex=0;pre.ariaLabel='测试用例相关源码';
      const full=ranges[0].file_content!==undefined,start=full?1:ranges[0].line;
      (full?ranges[0].file_content:ranges[0].content).split('\n').forEach((text,index)=>{const line=start+index,bound=ranges.find(range=>line>=range.line&&line<=range.end_line),row=el('div',bound?'is-bound':'');row.dataset.line=String(line);row.dataset.bindingId=(bound||ranges[0]).id;row.append(el('span','',String(line)),el('code','',text||' '));pre.append(row);});
      pre.onmouseup=()=>{const selection=window.getSelection();if(!selection?.toString()||!pre.contains(selection.anchorNode)||!pre.contains(selection.focusNode))return;const first=selection.anchorNode.parentElement.closest('[data-line]'),last=selection.focusNode.parentElement.closest('[data-line]');if(first&&last){sourceId=first.dataset.bindingId;const lines=[Number(first.dataset.line),Number(last.dataset.line)];publish({line:Math.min(...lines),end_line:Math.max(...lines)});}};
      part.append(pre);blocks.push({part,pre});
      const selectedRange=ranges.find(range=>range.id===sourceId);
      if(selectedRange){part.open=true;selectedRow=pre.children[selectedRange.line-start];selectedEndRow=pre.children[selectedRange.end_line-start];}
    }
    window.FlowCanvasScrollToSourceLine(body,selectedRow,selectedEndRow);
    publish();let scrollTimer;
    body.onscroll=()=>{clearTimeout(scrollTimer);scrollTimer=setTimeout(()=>{if(token!==generation||body.hidden)return;const top=body.getBoundingClientRect().top,block=blocks.find(({part,pre})=>part.open&&pre.getBoundingClientRect().bottom>top),row=block&&[...block.pre.children].find(item=>item.getBoundingClientRect().bottom>top);if(row){sourceId=row.dataset.bindingId;publish({line:Number(row.dataset.line)});}},150);};
  }
  /** Fetch logs incrementally and retain the user's scroll position. */
  async function output(token){
    if(outputRequest===token)return;
    outputRequest=token;
    try{
      const data=await json(base()+'/test-runs/'+encodeURIComponent(runId)+'/output?offset='+offset);
      if(token!==generation)return;
      const row=data.run,index=runs.findIndex(run=>run.id===row.id);
      if(index>=0&&runs[index].status!==row.status){
        runs[index]=row;
        runSelect.options[index].textContent=runLabel(row,index);
        updateRunBadge(row);
        refreshCoverageLinks();
      }
      if(runCommand.textContent!==(row.command||''))runCommand.textContent=row.command||'';
      if(resultNote){
        const result=row.test_result,status=({passed:'通过',failed:'失败',running:'运行中'})[result?.status];
        const text=(status?status+' · ':'')+(result?.reason||'');
        if(resultNote.textContent!==text)resultNote.textContent=text;
        resultNote.dataset.status=result?.status||'';
      }
      offset=data.offset;
      if(!data.text)return;
      const follow=body.scrollTop>0&&log.parentElement.open&&log.getBoundingClientRect().bottom<=body.getBoundingClientRect().bottom+40
        &&body.scrollHeight-body.scrollTop-body.clientHeight<40;
      log.append(document.createTextNode(data.text));
      if(log.textContent.length>2000000)log.textContent='[较早输出已省略；完整日志保存在本次运行目录]\n'+log.textContent.slice(-1500000);
      if(follow)body.scrollTop=body.scrollHeight;
    }finally{
      if(outputRequest===token)outputRequest=null;
    }
  }
  /** Present only files discovered inside this execution's managed directory. */
  async function execution(token){
    if(!await loadRuns(token))return;
    if(!runId){body.append(el('p','case-detail-empty','此用例尚无正式运行记录'));return;}
    picker();const run=runs.find(row=>row.id===runId);
    if(tab==='wave'){
      const result=await json(base()+'/test-runs/'+encodeURIComponent(runId)+'/waveforms');if(token!==generation)return;
      const waiting=['starting','running'].includes(run?.status);
      if(!result.files.length||run?.waveform_status==='failed'||waiting){
        const viewer=section('波形'),actions=el('div','case-overview-actions'),task=taskButton('case-waveform','解读波形');task.disabled=true;task.title='本次运行尚无波形文件';actions.append(task);viewer.querySelector('.module-section-heading').remove();
        viewer.append(el('p','case-detail-empty',waiting?'运行仍在生成波形，结束后将自动载入。':run?.waveform_error?'波形不可用：'+run.waveform_error:'本次运行尚无波形文件'));const explanation=section('AI 解读');explanation.classList.add('case-wave-explanation');explanation.querySelector('.module-section-heading').append(actions);explanation.append(el('p','case-detail-empty','生成波形后可进行解读。'));return;
      }
      const viewer=section('波形');viewer.classList.add('case-section-wave');
      const file=result.files.find(file=>file.path==='waveforms/waves.vcd')||result.files.find(file=>/\.(vcd|fst)$/i.test(file.path));
      if(!file){viewer.querySelector('.module-section-heading').remove();viewer.append(el('p','case-detail-empty','本次运行没有可查看的 VCD/FST 波形'));return;}
      const path=file.path;wavePath=path;publish({waveform:path});
      const analysisUrl=base()+'/test-runs/'+encodeURIComponent(runId)+'/waveform-analysis?case_id='+encodeURIComponent(selected.id)+'&waveform='+encodeURIComponent(path);
      const saved=await json(analysisUrl);if(token!==generation)return;
      waveRevision=JSON.stringify([saved.revision,saved.status]);
      const actions=el('div','case-overview-actions');actions.append(taskButton('case-waveform','解读波形',{run_id:runId,waveform:path}));viewer.querySelector('.module-section-heading').remove();
      const source=base()+'/test-runs/'+encodeURIComponent(runId)+'/waveform-view/'+path.split('/').map(encodeURIComponent).join('/');
      const catalogUrl=analysisUrl.replace('/waveform-analysis?','/waveform-signals?');
      wavePanel=window.FlowCanvasWavePanel(viewer,{source,analysisUrl,catalogUrl,runSelect:runSelect.closest('.case-run-picker'),analysis:saved.analysis,onSelection:()=>{for(const button of waveExplanation?.querySelectorAll('.case-wave-point-title')||[])button.setAttribute('aria-pressed','false');}});
      const explanation=section('AI 解读');explanation.classList.add('case-wave-explanation');explanation.querySelector('.module-section-heading').append(actions);
      if(!saved.analysis){explanation.append(el('p','case-detail-empty',saved.status==='stale'?'波形已更新，请重新解读。':'点击“解读波形”生成分析。'));return;}
      for(const [index,point] of saved.analysis.points.entries()){
        const entry=el('article','case-wave-point'),jump=el('button','case-wave-point-title',(index+1)+'. '+point.title);jump.type='button';
        jump.setAttribute('aria-pressed',String(index===0));jump.title='查看对应信号和时间窗口';
        jump.onclick=()=>{for(const button of explanation.querySelectorAll('.case-wave-point-title'))button.setAttribute('aria-pressed',String(button===jump));wavePanel?.focus(index);};
        entry.append(jump,el('span','case-wave-point-time',point.time+' · 原生时间刻度'),el('p','',point.explanation));
        const values=el('p','case-wave-point-values');values.append(el('code','',Object.entries(point.values).map(([signal,value])=>signal+' = '+value).join('\n')));entry.append(values);explanation.append(entry);
      }
      return;
    }
    field('开始时间',new Date(run.started*1000).toLocaleString());
    runCommand=el('pre','case-section-code');section('执行命令').append(runCommand);
    showRunCoverage();
    log=el('pre','case-detail-log');log.tabIndex=0;log.ariaLabel='所选运行的日志';const resultSection=section('执行结果');resultNote=el('p','case-result-note');resultSection.append(resultNote,log);await output(token);
  }
  /** Replace only the active tab when selection changes, guarding stale asynchronous responses. */
  function render(){
    const token=++generation;coverageSections=null;wavePanel?.dispose();wavePanel=null;waveExplanation=null;if(selected)window.FlowCanvasCaseWorkspace?.setWaveAnalysis(project().id,selected.id,null);offset=0;log=null;resultNote=null;wavePath='';body.onscroll=null;body.replaceChildren();body.dataset.tab=tab;title.replaceChildren();
    for(const button of tabs.children){const active=button.id===tabPrefix+tab;button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;}
    body.setAttribute('role','tabpanel');body.setAttribute('aria-labelledby',tabPrefix+tab);
    if(!selected){title.append(el('strong','','测试用例详情'));body.append(el('p','case-detail-empty','在左侧选择测试用例'));return;}
    publish();
    const heading=el('div','case-detail-heading');
    heading.append(el('strong','',selected.title||selected.name),el('code','',selected.name));
    if(createRunButton){
      const error=el('p','targets-error case-detail-run-error');error.setAttribute('role','alert');
      const actions=el('div','case-detail-header-actions');
      actions.append(createRunButton(selected,message=>{error.textContent=message;}));
      title.append(heading,actions,error);
    }else title.append(heading);
    if(tab==='overview'){
      const actions=el('div','detail-overview-toolbar');actions.append(taskButton('case-overview','生成用例概览'));body.append(actions);
      overviewBlock(selected.overview||{});
      showCoverageOverview();
      timeline();
      return;
    }
    (tab==='source'?source(token):execution(token)).catch(error=>{if(token===generation)body.append(el('p','targets-error',error.message));});
  }
  const runPoll=setInterval(async()=>{if(host.closest('[hidden]')||document.hidden||tab!=='runs'||!runId||!log)return;const token=generation;try{await output(token);}catch(error){if(token===generation)log.textContent=error.message;}},1000);
  const wavePoll=setInterval(async()=>{
    if(tab!=='wave'||!runId||document.hidden||host.closest('[hidden]')||wavePolling)return;
    wavePolling=true;const token=generation;
    try{
      if(['starting','running'].includes(runs.find(row=>row.id===runId)?.status)){
        const rows=await json(base()+'/test-runs?case_id='+encodeURIComponent(selected.id)+'&include_smoke=true');
        if(token!==generation)return;const next=rows.find(row=>row.id===runId);if(next&&next.status!==runs.find(row=>row.id===runId)?.status){render();return;}
      }
      if(!wavePath)return;
      const saved=await json(base()+'/test-runs/'+encodeURIComponent(runId)+'/waveform-analysis?case_id='+encodeURIComponent(selected.id)+'&waveform='+encodeURIComponent(wavePath));if(token===generation&&JSON.stringify([saved.revision,saved.status])!==waveRevision)render();}catch{}finally{wavePolling=false;}
  },2500);
  const onRun=event=>{
    if(event.detail.project?.id===project()?.id&&event.detail.run?.case_id===selected?.id){runId=event.detail.run.id;tab='runs';render();body.scrollTop=0;}
  };
  document.addEventListener('flowcanvas-test-run',onRun);
  const onClose=event=>{if(event.detail.case_id===selected?.id){wavePanel?.dispose();wavePanel=null;window.FlowCanvasCaseWorkspace?.setWaveAnalysis(project().id,selected.id,null);revision='';}};
  document.addEventListener('flowcanvas-case-tab-closed',onClose);
  render();
  return {dispose(){generation++;coverageSections=null;clearInterval(coveragePoll);wavePanel?.dispose();if(selected)window.FlowCanvasCaseWorkspace?.setWaveAnalysis(project().id,selected.id,null);clearInterval(runPoll);clearInterval(wavePoll);document.removeEventListener('flowcanvas-test-run',onRun);document.removeEventListener('flowcanvas-case-tab-closed',onClose);host.remove();},context(){return {tab,run_id:runId,binding_id:tab==='source'?sourceId:null};},select(node,bindings,initialView){const next=JSON.stringify([node,bindings]);if(revision===next&&!initialView)return;const changed=selected?.id!==node?.id;selected=node;report=bindings;revision=next;if(changed||initialView){const position=initialView||positions.get(node?.id)||{};tab=Object.hasOwn(names,position.tab)?position.tab:'overview';runId=typeof position.run_id==='string'?position.run_id:null;sourceId=typeof position.binding_id==='string'?position.binding_id:null;}render();}};
};

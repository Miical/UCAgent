/** Selected-case inspector with independently selected source bindings and real execution records. */
window.FlowCanvasCaseDetails=function(host,project){
  const el=(tag,cls,text)=>{const node=document.createElement(tag);if(cls)node.className=cls;if(text!==undefined)node.textContent=text;return node;};
  let selected=null,report=null,tab='overview',runId=null,sourceId=null,revision='',generation=0,offset=0,polling=false;
  let runs=[],log=null,runCommand=null,runSelect=null,wavePath='',waveRevision='',wavePolling=false;
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
  function section(label){const node=el('section','case-section');node.append(el('h4','case-section-title',label));body.append(node);return node;}
  /** Render a labeled value using the shared section layout. */
  function field(label,value,code=false){section(label).append(el(code?'pre':'p','',value||'尚未填写'));}
  function overviewBlock(data){
    for(const [label,value] of [['验证目的',data.purpose],['验证方法',data.method]]){
      section(label).append(el('p',value?'':'case-detail-empty',value||'待补充'));
    }
  }
  /** Reuse the AI task hover-preview and pin behavior with an immutable case/run scope. */
  function taskButton(kind,label,extra={}){
    const task=el('button','ai-case-add');task.type='button';task.title='AI '+label;task.setAttribute('aria-label','AI '+label);
    task.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><defs><linearGradient id="case-ai-spark" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#4285ff"/><stop offset=".55" stop-color="#9164ec"/><stop offset="1" stop-color="#df77ba"/></linearGradient></defs><path d="M12 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-6M8 10h3M8 14h8M8 18h5"/><path class="ai-colored-spark" d="M18 2l1.2 3.8L23 7l-3.8 1.2L18 12l-1.2-3.8L13 7l3.8-1.2Z" fill="url(#case-ai-spark)" stroke="url(#case-ai-spark)"/></svg>';
    const scope={project:project(),testcase:selected,kind,...extra};
    const open=preview=>{if(!task.disabled)document.dispatchEvent(new CustomEvent('flowcanvas-case-task',{detail:{...scope,anchor:task,preview}}));};
    let timer;task.onmouseenter=()=>{clearTimeout(timer);timer=setTimeout(()=>{if(task.isConnected&&task.matches(':hover'))open(true);},220);};task.onmouseleave=()=>clearTimeout(timer);
    task.onclick=()=>{clearTimeout(timer);open(false);};return task;
  }
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
  function picker(){
    runSelect=el('select','case-detail-select');runSelect.ariaLabel='选择本用例的运行记录';
    for(const row of runs){const option=el('option','',new Date(row.started*1000).toLocaleString()+' · '+(statusNames[row.status]||row.status)+' · '+row.id.slice(0,8));option.value=row.id;runSelect.append(option);}
    runSelect.value=runId;runSelect.onchange=()=>{runId=runSelect.value;render();};body.append(runSelect);
  }
  /** Display the selected source directly and highlight its bound range. */
  async function source(token){
    const bindings=report?.bindings||[];
    if(!bindings.length){body.append(el('p','case-detail-empty','尚无源码绑定'));return;}
    if(!bindings.some(binding=>binding.id===sourceId))sourceId=bindings[0].id;
    const result=await json(base()+'/testcases',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({operation:'inspect',binding_id:sourceId})});
    if(token!==generation)return;
    const data=result.source;const pre=el('pre','case-detail-source');pre.tabIndex=0;pre.ariaLabel='测试用例源码';
    const full=data.file_content!==undefined,start=full?1:data.line;
    (full?data.file_content:data.content).split('\n').forEach((text,index)=>{const line=start+index,row=el('div',line>=data.line&&line<=data.end_line?'is-bound':'');row.dataset.line=String(line);row.append(el('span','',String(line)),el('code','',text||' '));if(line===data.line)row.dataset.start='true';pre.append(row);});
    publish();
    let scrollTimer;
    pre.onscroll=()=>{clearTimeout(scrollTimer);scrollTimer=setTimeout(()=>{if(token!==generation)return;const bounds=pre.getBoundingClientRect();const row=[...pre.children].find(node=>node.getBoundingClientRect().bottom>bounds.top);if(row)publish({line:Number(row.dataset.line)});},150);};
    pre.onmouseup=()=>{const selection=window.getSelection();if(!selection?.toString()||!pre.contains(selection.anchorNode)||!pre.contains(selection.focusNode))return;const first=selection.anchorNode.parentElement.closest('[data-line]'),last=selection.focusNode.parentElement.closest('[data-line]');if(first&&last){const lines=[Number(first.dataset.line),Number(last.dataset.line)];publish({line:Math.min(...lines),end_line:Math.max(...lines)});}};
    const sourceSection=section('源码');sourceSection.classList.add('case-section-source');sourceSection.append(pre);requestAnimationFrame(()=>{if(token===generation){const row=pre.querySelector('[data-start]');if(row)pre.scrollTop=Math.max(0,row.offsetTop-pre.offsetTop-70);}});
  }
  /** Fetch logs incrementally and retain the user's scroll position. */
  async function output(token){
    const data=await json(base()+'/test-runs/'+encodeURIComponent(runId)+'/output?offset='+offset);
    if(token!==generation)return;
    const row=data.run;
    runCommand.textContent=row.command||'';
    const follow=log.scrollHeight-log.scrollTop-log.clientHeight<40;
    if(data.text)log.append(document.createTextNode(data.text));offset=data.offset;if(log.textContent.length>2000000)log.textContent='[较早输出已省略；完整日志保存在本次运行目录]\n'+log.textContent.slice(-1500000);if(follow)log.scrollTop=log.scrollHeight;
  }
  /** Present only files discovered inside this execution's managed directory. */
  async function execution(token){
    if(!await loadRuns(token))return;
    if(!runId){body.append(el('p','case-detail-empty','此用例尚无正式运行记录'));return;}
    picker();const run=runs.find(row=>row.id===runId);
    if(tab==='wave'){
      const result=await json(base()+'/test-runs/'+encodeURIComponent(runId)+'/waveforms');if(token!==generation)return;
      if(!result.files.length){
        const viewer=section('波形'),actions=el('div','case-overview-actions'),task=taskButton('case-waveform','解读波形');task.disabled=true;task.title='本次运行尚无波形文件';actions.append(task);viewer.querySelector('h4').remove();
        viewer.append(el('p','case-detail-empty','本次运行尚无波形文件'));const explanation=section('AI 解读');explanation.classList.add('case-wave-explanation');explanation.querySelector('h4').append(actions);explanation.append(el('p','case-detail-empty','生成波形后可进行解读。'));return;
      }
      const viewer=section('波形');viewer.classList.add('case-section-wave');
      const file=result.files.find(file=>file.path==='waveforms/waves.vcd')||result.files.find(file=>/\.(vcd|fst)$/i.test(file.path));
      if(!file){viewer.querySelector('h4').remove();viewer.append(el('p','case-detail-empty','本次运行没有可查看的 VCD/FST 波形'));return;}
      const path=file.path;wavePath=path;publish({waveform:path});
      const analysisUrl=base()+'/test-runs/'+encodeURIComponent(runId)+'/waveform-analysis?case_id='+encodeURIComponent(selected.id)+'&waveform='+encodeURIComponent(path);
      const saved=await json(analysisUrl);if(token!==generation)return;
      waveRevision=JSON.stringify([saved.revision,saved.status]);
      const actions=el('div','case-overview-actions');actions.append(taskButton('case-waveform','解读波形',{run_id:runId,waveform:path}));viewer.querySelector('h4').remove();
      const source=base()+'/test-runs/'+encodeURIComponent(runId)+'/waveform-view/'+path.split('/').map(encodeURIComponent).join('/');
      const frame=el('iframe','case-wave-viewer');frame.title='Surfer 波形查看器';frame.allow='cross-origin-isolated';
      const url=new URL('/surfer/',location.origin);url.searchParams.set('load_url',new URL(source,location.origin).href);
      if(saved.status==='ready'){
        url.searchParams.set('analysis_url',analysisUrl);url.searchParams.set('startup_commands','run_command_file_from_url '+location.origin+'/surfer/ucagent-wave-ready.sucl');
      }
      frame.src=url.href;viewer.append(frame);
      const explanation=section('AI 解读');explanation.classList.add('case-wave-explanation');explanation.querySelector('h4').append(actions);
      if(!saved.analysis){explanation.append(el('p','case-detail-empty',saved.status==='stale'?'波形已更新，请重新解读。':'点击右侧 AI 按钮生成解读。'));return;}
      for(const [index,point] of saved.analysis.points.entries()){
        const entry=el('article','case-wave-point'),jump=el('button','case-wave-point-title',(index+1)+'. '+point.title);jump.type='button';
        jump.setAttribute('aria-pressed',String(index===0));jump.title='查看对应信号和时间窗口';
        jump.onclick=()=>{for(const button of explanation.querySelectorAll('.case-wave-point-title'))button.setAttribute('aria-pressed',String(button===jump));frame.contentWindow.postMessage({type:'flowcanvas-wave-point',index},location.origin);};
        entry.append(jump,el('span','case-wave-point-time',point.time+' · 原生时间刻度'),el('p','',point.explanation));
        const values=el('p','case-wave-point-values');values.append(el('code','',Object.entries(point.values).map(([signal,value])=>signal+' = '+value).join('\n')));entry.append(values);explanation.append(entry);
      }
      return;
    }
    field('开始时间',new Date(run.started*1000).toLocaleString());
    runCommand=el('pre','case-section-code');section('执行命令').append(runCommand);
    log=el('pre','case-detail-log');log.tabIndex=0;log.ariaLabel='所选运行的日志';section('执行结果').append(log);await output(token);
  }
  /** Replace only the active tab when selection changes, guarding stale asynchronous responses. */
  function render(){
    const token=++generation;offset=0;log=null;wavePath='';body.replaceChildren();body.dataset.tab=tab;title.replaceChildren();
    for(const button of tabs.children){const active=button.id===tabPrefix+tab;button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;}
    body.setAttribute('role','tabpanel');body.setAttribute('aria-labelledby',tabPrefix+tab);
    if(!selected){title.append(el('strong','','测试用例详情'));body.append(el('p','case-detail-empty','在左侧选择测试用例'));return;}
    publish();
    title.append(el('strong','',selected.title||selected.name),el('code','',selected.name));
    if(tab==='overview'){
      const actions=el('div','case-overview-actions');actions.append(taskButton('case-overview','生成用例概览'));
      overviewBlock(selected.overview||{});
      body.querySelector('.case-section-title').append(actions);
      timeline();
      return;
    }
    (tab==='source'?source(token):execution(token)).catch(error=>{if(token===generation)body.append(el('p','targets-error',error.message));});
  }
  const runPoll=setInterval(async()=>{if(host.closest('[hidden]')||document.hidden||tab!=='runs'||!runId||!log||polling)return;polling=true;const token=generation;try{await output(token);}catch(error){if(token===generation)log.textContent=error.message;}finally{polling=false;}},1000);
  const wavePoll=setInterval(async()=>{
    if(tab!=='wave'||!wavePath||document.hidden||host.closest('[hidden]')||wavePolling)return;
    wavePolling=true;const token=generation;
    try{const saved=await json(base()+'/test-runs/'+encodeURIComponent(runId)+'/waveform-analysis?case_id='+encodeURIComponent(selected.id)+'&waveform='+encodeURIComponent(wavePath));if(token===generation&&JSON.stringify([saved.revision,saved.status])!==waveRevision)render();}catch{}finally{wavePolling=false;}
  },2500);
  const onRun=event=>{
    if(event.detail.project?.id===project()?.id&&event.detail.run?.case_id===selected?.id){runId=event.detail.run.id;tab='runs';render();}
  };
  document.addEventListener('flowcanvas-test-run',onRun);
  render();
  return {dispose(){generation++;clearInterval(runPoll);clearInterval(wavePoll);document.removeEventListener('flowcanvas-test-run',onRun);host.remove();},context(){return {tab,run_id:runId,binding_id:tab==='source'?sourceId:null};},select(node,bindings){const next=JSON.stringify([node,bindings]);if(revision===next)return;const changed=selected?.id!==node?.id;selected=node;report=bindings;revision=next;if(changed){const position=positions.get(node?.id)||{};tab=position.tab||'overview';runId=position.run_id||null;sourceId=position.binding_id||null;}render();}};
};
